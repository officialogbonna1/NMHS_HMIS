"""
Stock control: receiving, counting and writing off — now per location.

Everything this file asserted before the two-location split it still
asserts; what changed is that each assertion names the shelf it is about.
The one deliberate behaviour change is where a delivery lands: goods are
received into **Main Store**, so a drug is not dispensable until it has been
transferred to the Pharmacy. `test_locations.py` covers that flow; this file
covers the operations themselves.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import (
    MAIN_STORE, PHARMACY, Batch, Item, StockLocation, StockMovement, StockRecord,
)


class StockControlTests(TestCase):
    def setUp(self):
        self.pharmacist = User.objects.create_user(username="pharmacist", password="test",
                                                   role="pharmacist")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.item = Item.objects.create(name="Paracetamol", reorder_threshold=5)
        self.store = StockLocation.objects.get(code=MAIN_STORE)
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)
        self.client = APIClient()
        self.client.force_authenticate(self.pharmacist)

    def _receive(self, quantity=20, expiry_days=90, batch_no="B1", location=None):
        payload = {
            "item": self.item.id, "batch_no": batch_no, "quantity": quantity,
            "cost_price": "10", "sale_price": "20",
            "expiry_date": (timezone.localdate() + timedelta(days=expiry_days)).isoformat(),
            "supplier": "Acme",
        }
        if location is not None:
            payload["location"] = location.id
        return self.client.post("/api/batches/", payload)

    def _stock(self, batch_id, location):
        record = StockRecord.objects.filter(batch_id=batch_id, location=location).first()
        return record.quantity if record else 0

    def test_receiving_stock_logs_a_movement_against_the_store(self):
        response = self._receive(quantity=20)
        self.assertEqual(response.status_code, 201, response.data)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.change, movement.reason), (20, "received"))
        self.assertEqual(movement.performed_by, self.pharmacist)
        # A delivery arrives at the store, and the movement says so.
        self.assertEqual(movement.location, self.store)
        self.assertEqual(self.item.total_quantity, 20)
        self.assertEqual(self.item.quantity_at(self.store), 20)
        self.assertEqual(self.item.quantity_at(self.pharmacy), 0)

    def test_a_stock_count_corrects_the_batch_and_logs_the_difference(self):
        batch_id = self._receive(quantity=20).data["id"]
        response = self.client.post(f"/api/batches/{batch_id}/count/", {
            "counted_quantity": 17, "note": "monthly count", "location": self.store.id,
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._stock(batch_id, self.store), 17)
        adjustment = StockMovement.objects.get(reason="adjustment")
        self.assertEqual(adjustment.change, -3)
        self.assertEqual(adjustment.reference, "monthly count")
        self.assertEqual(adjustment.location, self.store)

    def test_a_count_that_matches_writes_no_movement(self):
        batch_id = self._receive(quantity=20).data["id"]
        self.client.post(f"/api/batches/{batch_id}/count/",
                         {"counted_quantity": 20, "location": self.store.id})
        self.assertFalse(StockMovement.objects.filter(reason="adjustment").exists())

    def test_a_negative_count_is_rejected(self):
        batch_id = self._receive(quantity=20).data["id"]
        response = self.client.post(f"/api/batches/{batch_id}/count/",
                                    {"counted_quantity": -1, "location": self.store.id})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._stock(batch_id, self.store), 20)

    def test_a_count_must_name_the_location_it_was_taken_at(self):
        """
        No default. Posting a count against a guessed shelf would invent
        stock in one location and destroy it in the other.
        """
        batch_id = self._receive(quantity=20).data["id"]
        response = self.client.post(f"/api/batches/{batch_id}/count/",
                                    {"counted_quantity": 5})
        self.assertEqual(response.status_code, 400)
        self.assertIn("location", response.data)
        self.assertEqual(self._stock(batch_id, self.store), 20)
        self.assertFalse(StockMovement.objects.filter(reason="adjustment").exists())

    def test_only_expired_batches_can_be_written_off(self):
        batch_id = self._receive(quantity=20).data["id"]
        self.assertEqual(self.client.post(f"/api/batches/{batch_id}/write_off/").status_code, 400)

        Batch.objects.filter(pk=batch_id).update(
            expiry_date=timezone.localdate() - timedelta(days=1))
        response = self.client.post(f"/api/batches/{batch_id}/write_off/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total_quantity"], 0)
        self.assertEqual(StockMovement.objects.get(reason="expired_writeoff").change, -20)

    def test_writing_off_clears_every_shelf_the_lot_is_standing_on(self):
        """Expired is expired everywhere — clearing one shelf and leaving the
        other is how an expired unit still reaches a patient."""
        batch_id = self._receive(quantity=20).data["id"]
        self.client.post("/api/stock-transfers/", {
            "source": self.store.id, "destination": self.pharmacy.id,
            "lines": [{"batch": batch_id, "quantity": 8}],
        }, format="json")
        Batch.objects.filter(pk=batch_id).update(
            expiry_date=timezone.localdate() - timedelta(days=1))

        self.client.post(f"/api/batches/{batch_id}/write_off/")
        self.assertEqual(self._stock(batch_id, self.store), 0)
        self.assertEqual(self._stock(batch_id, self.pharmacy), 0)
        self.assertEqual(
            set(StockMovement.objects.filter(reason="expired_writeoff")
                .values_list("location__code", flat=True)),
            {MAIN_STORE, PHARMACY},
        )

    def test_a_doctor_sees_availability_not_counts_and_cannot_touch_stock(self):
        self._receive(quantity=20)
        doctor_client = APIClient()
        doctor_client.force_authenticate(self.doctor)

        response = doctor_client.get("/api/items/")
        self.assertEqual(response.status_code, 200)
        item = response.data["results"][0]
        self.assertNotIn("total_quantity", item)
        # Received into the store, so the counter cannot fill it yet: the
        # doctor is told it is unavailable rather than prescribing something
        # the pharmacy would have to refuse.
        self.assertFalse(item["available"])

        self.client.post("/api/stock-transfers/", {
            "source": self.store.id, "destination": self.pharmacy.id,
            "lines": [{"item": self.item.id, "quantity": 20}],
        }, format="json")
        item = doctor_client.get("/api/items/").data["results"][0]
        self.assertTrue(item["available"])

        self.assertEqual(doctor_client.get("/api/batches/").status_code, 403)
        self.assertEqual(doctor_client.get("/api/stock-movements/").status_code, 403)
        self.assertEqual(doctor_client.get("/api/stock-records/").status_code, 403)
        self.assertEqual(doctor_client.get("/api/stock-transfers/").status_code, 403)
