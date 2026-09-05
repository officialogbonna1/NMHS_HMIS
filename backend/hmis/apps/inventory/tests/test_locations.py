"""
Two locations, one flow: Supplier → Main Store → Pharmacy → patient.

The properties these tests exist to hold:

* stock is **product + batch + location**, never one undifferentiated number;
* moving it between locations is a **transfer document**, not an edit;
* a transfer conserves the hospital's total, and both halves land or neither;
* the pharmacy can only dispense what is standing **in the pharmacy**;
* nobody can move a quantity through the API without a movement behind it.

The last one is the security requirement, and it is checked three ways —
against the serializer, against the routes, and against the model itself.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import (
    MAIN_STORE, PHARMACY, Batch, Item, StockLocation, StockMovement, StockRecord,
    dispensing_location, receiving_location,
)
from apps.inventory.services import (
    InsufficientStockError, post_stock_count, receive_stock, transfer_stock,
)
from apps.patients.models import Patient
from apps.pharmacy.services import (
    OutOfStockError, create_prescription, dispense_prescription,
)


class LocationTestCase(TestCase):
    def setUp(self):
        self.pharmacist = User.objects.create_user(username="pharm", password="t",
                                                   role="pharmacist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.store = StockLocation.objects.get(code=MAIN_STORE)
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)
        self.item = Item.objects.create(name="Paracetamol 500mg")
        self.batch = Batch.objects.create(
            item=self.item, batch_no="PCM001", cost_price=Decimal("10"),
            sale_price=Decimal("20"),
            expiry_date=timezone.localdate() + timedelta(days=180))
        self.client = APIClient()
        self.client.force_authenticate(self.pharmacist)

    def _at(self, location, batch=None):
        record = StockRecord.objects.filter(batch=batch or self.batch,
                                            location=location).first()
        return record.quantity if record else 0


class SeededLocationTests(LocationTestCase):
    def test_the_two_operational_locations_exist_with_their_roles(self):
        self.assertEqual(receiving_location(), self.store)
        self.assertEqual(dispensing_location(), self.pharmacy)
        self.assertEqual(self.store.kind, "store")
        self.assertEqual(self.pharmacy.kind, "dispensary")

    def test_a_third_location_is_a_row_not_a_deployment(self):
        """The extensibility requirement: locations are data."""
        theatre = StockLocation.objects.create(code="theatre", name="Theatre Store",
                                               kind="store", display_order=30)
        receive_stock(batch=self.batch, quantity=10, actor=self.pharmacist, location=theatre)
        self.assertEqual(self._at(theatre), 10)
        # And the flags that carry the workflow are untouched by its arrival.
        self.assertEqual(receiving_location(), self.store)
        self.assertEqual(dispensing_location(), self.pharmacy)


class TransferTests(LocationTestCase):
    def setUp(self):
        super().setUp()
        receive_stock(batch=self.batch, quantity=500, actor=self.pharmacist)

    def test_the_worked_example(self):
        """
        Main Store 500 → transfer 100 → Main Store 400, Pharmacy 100,
        hospital total still 500.
        """
        self.assertEqual(self._at(self.store), 500)

        transfer = transfer_stock(
            source=self.store, destination=self.pharmacy,
            lines=[{"batch": self.batch, "quantity": 100}], actor=self.pharmacist)

        self.assertEqual(self._at(self.store), 400)
        self.assertEqual(self._at(self.pharmacy), 100)
        self.assertEqual(self.batch.total_quantity, 500)
        self.assertEqual(self.item.total_quantity, 500)
        self.assertTrue(transfer.reference.startswith("TRF-"))

    def test_a_transfer_writes_a_movement_at_each_end(self):
        transfer = transfer_stock(
            source=self.store, destination=self.pharmacy,
            lines=[{"batch": self.batch, "quantity": 100}], actor=self.pharmacist)

        movements = StockMovement.objects.filter(transfer=transfer)
        self.assertEqual(movements.count(), 2)
        out = movements.get(reason="transfer_out")
        into = movements.get(reason="transfer_in")
        self.assertEqual((out.location, out.change), (self.store, -100))
        self.assertEqual((into.location, into.change), (self.pharmacy, 100))
        # Both carry the transfer's own reference number, so the paperwork
        # and the ledger can be tied together without matching strings.
        self.assertEqual({out.reference, into.reference}, {transfer.reference})

    def test_a_transfer_cannot_take_more_than_the_source_is_holding(self):
        with self.assertRaises(InsufficientStockError):
            transfer_stock(source=self.store, destination=self.pharmacy,
                           lines=[{"batch": self.batch, "quantity": 501}],
                           actor=self.pharmacist)
        self.assertEqual(self._at(self.store), 500)
        self.assertEqual(self._at(self.pharmacy), 0)

    def test_a_transfer_is_all_or_nothing(self):
        """A transfer that moved two of three lines leaves nobody able to say
        which line was short."""
        other = Batch.objects.create(
            item=Item.objects.create(name="Amoxicillin"), batch_no="AMX001",
            cost_price=Decimal("50"), sale_price=Decimal("80"),
            expiry_date=timezone.localdate() + timedelta(days=90))
        receive_stock(batch=other, quantity=5, actor=self.pharmacist)

        with self.assertRaises(InsufficientStockError):
            transfer_stock(source=self.store, destination=self.pharmacy, actor=self.pharmacist,
                           lines=[{"batch": self.batch, "quantity": 10},
                                  {"batch": other, "quantity": 50}])

        self.assertEqual(self._at(self.store), 500)
        self.assertEqual(self._at(self.pharmacy), 0)
        self.assertEqual(self._at(self.store, other), 5)
        self.assertFalse(StockMovement.objects.filter(reason="transfer_out").exists())

    def test_a_transfer_to_the_same_location_is_refused(self):
        with self.assertRaises(Exception):
            transfer_stock(source=self.store, destination=self.store, actor=self.pharmacist,
                           lines=[{"batch": self.batch, "quantity": 1}])

    def test_transferring_by_item_picks_the_earliest_expiring_batch(self):
        """FEFO out of the store, so the short-dated lot reaches the counter
        rather than ageing on a shelf nobody dispenses from."""
        short = Batch.objects.create(
            item=self.item, batch_no="PCM000", cost_price=Decimal("10"),
            sale_price=Decimal("20"),
            expiry_date=timezone.localdate() + timedelta(days=10))
        receive_stock(batch=short, quantity=30, actor=self.pharmacist)

        response = self.client.post("/api/stock-transfers/", {
            "source": self.store.id, "destination": self.pharmacy.id,
            "lines": [{"item": self.item.id, "quantity": 50}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)

        self.assertEqual(self._at(self.pharmacy, short), 30)   # emptied first
        self.assertEqual(self._at(self.pharmacy), 20)          # the rest
        self.assertEqual(self._at(self.store, short), 0)
        self.assertEqual(self._at(self.store), 480)

    def test_the_api_records_who_transferred_and_what(self):
        response = self.client.post("/api/stock-transfers/", {
            "source": self.store.id, "destination": self.pharmacy.id,
            "note": "Weekly top-up",
            "lines": [{"batch": self.batch.id, "quantity": 100}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total_units"], 100)
        self.assertEqual(response.data["source_name"], "Main Store")
        self.assertEqual(response.data["destination_name"], "Pharmacy")
        self.assertEqual(response.data["transferred_by"], self.pharmacist.id)
        self.assertEqual(len(response.data["lines"]), 1)
        self.assertEqual(response.data["lines"][0]["batch_no"], "PCM001")

    def test_a_transfer_cannot_be_edited_after_the_fact(self):
        transfer = transfer_stock(source=self.store, destination=self.pharmacy,
                                  lines=[{"batch": self.batch, "quantity": 100}],
                                  actor=self.pharmacist)
        for method in ("patch", "put", "delete"):
            response = getattr(self.client, method)(f"/api/stock-transfers/{transfer.id}/", {})
            self.assertEqual(response.status_code, 405, method)


class DispensingIsPharmacyOnlyTests(LocationTestCase):
    def setUp(self):
        super().setUp()
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.doctor)
        receive_stock(batch=self.batch, quantity=500, actor=self.pharmacist)

    def test_stock_sitting_in_the_store_cannot_be_dispensed(self):
        with self.assertRaises(OutOfStockError):
            create_prescription(patient=self.patient, doctor=self.doctor,
                                item=self.item, quantity=10)
        self.assertEqual(self._at(self.store), 500)

    def test_dispensing_draws_down_the_pharmacy_only(self):
        transfer_stock(source=self.store, destination=self.pharmacy,
                       lines=[{"batch": self.batch, "quantity": 100}], actor=self.pharmacist)

        prescription = create_prescription(patient=self.patient, doctor=self.doctor,
                                           item=self.item, quantity=10)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)

        self.assertEqual(self._at(self.pharmacy), 90)
        self.assertEqual(self._at(self.store), 400)   # untouched
        self.assertEqual(self.item.total_quantity, 490)

        movement = StockMovement.objects.get(reason="prescription")
        self.assertEqual(movement.location, self.pharmacy)
        self.assertEqual(movement.change, -10)
        self.assertEqual(movement.reference, f"prescription:{prescription.id}")

    def test_a_prescription_larger_than_the_counter_holds_is_refused(self):
        """Even with plenty in the store: the counter cannot hand over what
        it does not have, and the message says to transfer more."""
        transfer_stock(source=self.store, destination=self.pharmacy,
                       lines=[{"batch": self.batch, "quantity": 5}], actor=self.pharmacist)
        prescription = create_prescription(patient=self.patient, doctor=self.doctor,
                                           item=self.item, quantity=5)
        transfer_stock(source=self.pharmacy, destination=self.store,
                       lines=[{"batch": self.batch, "quantity": 5}], actor=self.pharmacist)

        with self.assertRaises(OutOfStockError) as caught:
            dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        self.assertIn("pharmacy shelf", " ".join(caught.exception.messages))
        prescription.refresh_from_db()
        self.assertEqual(prescription.status, "pending")

    def test_dispensing_is_fefo_within_the_pharmacy(self):
        short = Batch.objects.create(
            item=self.item, batch_no="PCM000", cost_price=Decimal("10"),
            sale_price=Decimal("20"),
            expiry_date=timezone.localdate() + timedelta(days=10))
        receive_stock(batch=short, quantity=6, actor=self.pharmacist, location=self.pharmacy)
        transfer_stock(source=self.store, destination=self.pharmacy,
                       lines=[{"batch": self.batch, "quantity": 100}], actor=self.pharmacist)

        prescription = create_prescription(patient=self.patient, doctor=self.doctor,
                                           item=self.item, quantity=10)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)

        self.assertEqual(self._at(self.pharmacy, short), 0)    # earliest expiry first
        self.assertEqual(self._at(self.pharmacy), 96)

    def test_expired_pharmacy_stock_is_never_dispensed(self):
        transfer_stock(source=self.store, destination=self.pharmacy,
                       lines=[{"batch": self.batch, "quantity": 100}], actor=self.pharmacist)
        fresh = Batch.objects.create(
            item=self.item, batch_no="PCM002", cost_price=Decimal("10"),
            sale_price=Decimal("20"),
            expiry_date=timezone.localdate() + timedelta(days=365))
        receive_stock(batch=fresh, quantity=50, actor=self.pharmacist, location=self.pharmacy)
        Batch.objects.filter(pk=self.batch.pk).update(
            expiry_date=timezone.localdate() - timedelta(days=1))

        prescription = create_prescription(patient=self.patient, doctor=self.doctor,
                                           item=self.item, quantity=10)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)

        self.assertEqual(self._at(self.pharmacy), 100)          # expired, untouched
        self.assertEqual(self._at(self.pharmacy, fresh), 40)


class PhysicalInventoryTests(LocationTestCase):
    def setUp(self):
        super().setUp()
        receive_stock(batch=self.batch, quantity=500, actor=self.pharmacist)
        transfer_stock(source=self.store, destination=self.pharmacy,
                       lines=[{"batch": self.batch, "quantity": 100}], actor=self.pharmacist)

    def test_the_count_sheet_is_per_location(self):
        store_sheet = self.client.get("/api/stock-counts/sheet/",
                                      {"location": self.store.id}).data
        pharmacy_sheet = self.client.get("/api/stock-counts/sheet/",
                                         {"location": self.pharmacy.id}).data

        self.assertEqual(store_sheet["location"]["code"], MAIN_STORE)
        self.assertEqual(store_sheet["lines"][0]["system_quantity"], 400)
        self.assertEqual(pharmacy_sheet["lines"][0]["system_quantity"], 100)
        # Product | Batch | Location | System quantity — the row a counter fills in.
        line = store_sheet["lines"][0]
        for field in ("item_name", "batch_no", "location_name", "system_quantity"):
            self.assertIn(field, line)

    def test_posting_a_count_adjusts_that_location_only(self):
        response = self.client.post("/api/stock-counts/", {
            "location": self.pharmacy.id, "note": "Monthly count",
            "lines": [{"batch": self.batch.id, "counted_quantity": 97}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)

        self.assertEqual(self._at(self.pharmacy), 97)
        self.assertEqual(self._at(self.store), 400)         # the store was not counted

        adjustment = StockMovement.objects.get(reason="adjustment")
        self.assertEqual(adjustment.change, -3)
        self.assertEqual(adjustment.location, self.pharmacy)

    def test_the_count_keeps_what_the_system_believed(self):
        count = post_stock_count(
            location=self.pharmacy, actor=self.pharmacist,
            lines=[{"batch": self.batch, "counted_quantity": 97}])
        line = count.lines.get()
        self.assertEqual((line.system_quantity, line.counted_quantity, line.difference),
                         (100, 97, -3))
        self.assertEqual(count.discrepancy_count, 1)
        self.assertTrue(count.reference.startswith("CNT-"))

    def test_a_line_that_matches_is_recorded_but_adjusts_nothing(self):
        count = post_stock_count(
            location=self.pharmacy, actor=self.pharmacist,
            lines=[{"batch": self.batch, "counted_quantity": 100}])
        self.assertEqual(count.lines.count(), 1)
        self.assertEqual(count.discrepancy_count, 0)
        self.assertFalse(StockMovement.objects.filter(reason="adjustment").exists())


class StockCannotBeWrittenDirectlyTests(LocationTestCase):
    """
    The security requirement: no route, serializer or object may move a
    quantity without a movement beside it.
    """

    def setUp(self):
        super().setUp()
        receive_stock(batch=self.batch, quantity=500, actor=self.pharmacist)
        self.record = StockRecord.objects.get(batch=self.batch, location=self.store)

    def test_stock_records_are_read_only_over_the_api(self):
        for method, payload in (("patch", {"quantity": 9999}),
                                ("put", {"quantity": 9999}),
                                ("delete", {})):
            response = getattr(self.client, method)(
                f"/api/stock-records/{self.record.id}/", payload, format="json")
            self.assertEqual(response.status_code, 405, method)
        self.assertEqual(self._at(self.store), 500)

    def test_stock_cannot_be_created_out_of_thin_air(self):
        response = self.client.post("/api/stock-records/", {
            "batch": self.batch.id, "location": self.pharmacy.id, "quantity": 9999,
        }, format="json")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self._at(self.pharmacy), 0)

    def test_patching_a_batch_cannot_change_a_quantity(self):
        """The hole this refactor closed: `quantity` was writable on Batch."""
        response = self.client.patch(f"/api/batches/{self.batch.id}/", {
            "quantity": 9999, "opening_quantity": 9999, "total_quantity": 9999,
        }, format="json")
        self.assertIn(response.status_code, (200, 400))
        self.assertEqual(self.batch.total_quantity, 500)
        self.assertEqual(StockMovement.objects.count(), 1)   # the receipt, nothing else

    def test_the_model_itself_refuses_a_quantity_change_without_a_service(self):
        """
        The last line of defence. Even code inside the project — a new view, a
        management command, a shell — cannot move the number this way.
        """
        self.record.quantity = 9999
        with self.assertRaises(PermissionDenied):
            self.record.save()
        self.record.refresh_from_db()
        self.assertEqual(self.record.quantity, 500)

    def test_movements_cannot_be_posted_by_hand(self):
        response = self.client.post("/api/stock-movements/", {
            "batch": self.batch.id, "location": self.store.id,
            "change": 9999, "reason": "received",
        }, format="json")
        self.assertEqual(response.status_code, 405)
