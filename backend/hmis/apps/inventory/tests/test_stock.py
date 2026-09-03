from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.inventory.models import Item, Batch, StockMovement


class StockControlTests(TestCase):
    def setUp(self):
        self.pharmacist = User.objects.create_user(username="pharmacist", password="test", role="pharmacist")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.item = Item.objects.create(name="Paracetamol", reorder_threshold=5)
        self.client = APIClient(); self.client.force_authenticate(self.pharmacist)

    def _receive(self, quantity=20, expiry_days=90, batch_no="B1"):
        return self.client.post("/api/batches/", {
            "item": self.item.id, "batch_no": batch_no, "quantity": quantity,
            "cost_price": "10", "sale_price": "20",
            "expiry_date": (timezone.localdate() + timedelta(days=expiry_days)).isoformat(),
            "supplier": "Acme",
        })

    def test_receiving_stock_logs_a_movement(self):
        response = self._receive(quantity=20)
        self.assertEqual(response.status_code, 201)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.change, movement.reason), (20, "received"))
        self.assertEqual(movement.performed_by, self.pharmacist)
        self.assertEqual(self.item.total_quantity, 20)

    def test_a_stock_count_corrects_the_batch_and_logs_the_difference(self):
        batch_id = self._receive(quantity=20).data["id"]
        response = self.client.post(f"/api/batches/{batch_id}/count/", {"counted_quantity": 17, "note": "monthly count"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["quantity"], 17)
        adjustment = StockMovement.objects.get(reason="adjustment")
        self.assertEqual(adjustment.change, -3)
        self.assertEqual(adjustment.reference, "monthly count")

    def test_a_count_that_matches_writes_no_movement(self):
        batch_id = self._receive(quantity=20).data["id"]
        self.client.post(f"/api/batches/{batch_id}/count/", {"counted_quantity": 20})
        self.assertFalse(StockMovement.objects.filter(reason="adjustment").exists())

    def test_a_negative_count_is_rejected(self):
        batch_id = self._receive(quantity=20).data["id"]
        response = self.client.post(f"/api/batches/{batch_id}/count/", {"counted_quantity": -1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Batch.objects.get(pk=batch_id).quantity, 20)

    def test_only_expired_batches_can_be_written_off(self):
        batch_id = self._receive(quantity=20).data["id"]
        self.assertEqual(self.client.post(f"/api/batches/{batch_id}/write_off/").status_code, 400)

        Batch.objects.filter(pk=batch_id).update(expiry_date=timezone.localdate() - timedelta(days=1))
        response = self.client.post(f"/api/batches/{batch_id}/write_off/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["quantity"], 0)
        self.assertEqual(StockMovement.objects.get(reason="expired_writeoff").change, -20)

    def test_a_doctor_sees_availability_not_counts_and_cannot_touch_stock(self):
        self._receive(quantity=20)
        doctor_client = APIClient(); doctor_client.force_authenticate(self.doctor)

        response = doctor_client.get("/api/items/")
        self.assertEqual(response.status_code, 200)
        item = response.data["results"][0]
        self.assertTrue(item["available"])
        self.assertNotIn("total_quantity", item)

        self.assertEqual(doctor_client.get("/api/batches/").status_code, 403)
        self.assertEqual(doctor_client.get("/api/stock-movements/").status_code, 403)
