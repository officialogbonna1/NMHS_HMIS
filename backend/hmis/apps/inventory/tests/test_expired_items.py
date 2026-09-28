"""
Expired Items: a register of **lots**, never of products.

Paracetamol 500mg with Batch A past its date and Batch B good for another
year is one product with one expired lot. Batch A appears here and stops
being usable; Batch B stays on sale; the product stays active.

Two ways a lot becomes expired, one definition of the result
(`inventory.models.expired_q`):

* **automatically** — its printed `expiry_date` is before today on the
  hospital's own calendar (`timezone.localdate()`), with nothing run and
  nothing written;
* **by hand** — an inventory administrator marks it, with a reason, and the
  batch keeps its item, number, dates, prices, supplier and stock.

Either way dispensing, the till, FEFO transfers and the doctor's
availability flag stop counting it, and nothing is deleted.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.pharmacy.testing import pay_for
from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.inventory.models import (
    MAIN_STORE, PHARMACY, Batch, StockLocation, StockMovement, StockRecord,
)
from apps.inventory.services import fefo_lines_for, quantity_on_hand
from apps.inventory.testing import product, stock_the_pharmacy, stock_the_store
from apps.patients.models import Patient
from apps.pharmacy.services import available_quantity, create_prescription, dispense_prescription
from apps.sales import services as pos


class ExpiredItemsTestCase(TestCase):
    def setUp(self):
        self.hospital_admin = User.objects.create_user(username="ha", password="t",
                                                       role="hospital_admin")
        self.store_keeper = User.objects.create_user(username="stores", password="t",
                                                     role="inventory_manager")
        self.pharmacist = User.objects.create_user(username="ph", password="t",
                                                   role="pharmacist")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.store = StockLocation.objects.get(code=MAIN_STORE)
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)

        self.paracetamol = product("Paracetamol 500mg", unit_name="Tablet")
        self.batch_a = stock_the_pharmacy(item=self.paracetamol, quantity=30,
                                          actor=self.store_keeper, batch_no="A",
                                          cost_price="8", sale_price="15", expiry_days=10)
        self.batch_b = stock_the_pharmacy(item=self.paracetamol, quantity=20,
                                          actor=self.store_keeper, batch_no="B",
                                          expiry_days=400)
        self.admin_api = self._client(self.hospital_admin)

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def expire_by_date(self, batch, days_ago=1):
        Batch.objects.filter(pk=batch.pk).update(
            expiry_date=timezone.localdate() - timedelta(days=days_ago))
        batch.refresh_from_db()

    def register(self, **params):
        response = self.admin_api.get("/api/stock-records/expired/", params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def mark(self, batch, reason="Cold chain broken", user=None):
        return self._client(user or self.hospital_admin).post(
            f"/api/batches/{batch.pk}/mark-expired/", {"reason": reason}, format="json")


class AutomaticExpiryTests(ExpiredItemsTestCase):
    def test_a_lot_past_its_date_appears_with_nothing_run(self):
        self.expire_by_date(self.batch_a)
        rows = self.register()["results"]
        self.assertEqual([(r["batch_no"], r["location_name"], r["quantity"], r["expiry_status"])
                          for r in rows], [("A", "Pharmacy", 30, "expired")])
        self.assertEqual(rows[0]["item_name"], "Paracetamol 500mg")

    def test_an_unexpired_lot_does_not_appear(self):
        self.assertEqual(self.register()["results"], [])

    def test_a_lot_expiring_today_is_still_usable_today(self):
        """`expiry_date < today` — the date printed is the last good day."""
        Batch.objects.filter(pk=self.batch_a.pk).update(expiry_date=timezone.localdate())
        self.assertEqual(self.register()["results"], [])

    def test_one_expired_lot_does_not_archive_the_product(self):
        self.expire_by_date(self.batch_a)
        self.paracetamol.refresh_from_db()
        self.assertTrue(self.paracetamol.is_active)
        # Batch B is still sold and dispensed.
        self.assertEqual(available_quantity(self.paracetamol), 20)

    def test_expired_stock_is_not_usable_anywhere_and_is_not_deleted(self):
        self.expire_by_date(self.batch_a)
        self.assertEqual(quantity_on_hand(item=self.paracetamol, location=self.pharmacy), 20)
        lines = fefo_lines_for(item=self.paracetamol, quantity=5, location=self.pharmacy)
        self.assertEqual([line["batch"].batch_no for line in lines], ["B"])
        # Still standing on the shelf, with its receipt, until written off.
        self.assertEqual(StockRecord.objects.get(batch=self.batch_a).quantity, 30)
        self.assertTrue(StockMovement.objects.filter(batch=self.batch_a).exists())

    def test_a_written_off_lot_stays_on_the_register_when_asked_for(self):
        self.expire_by_date(self.batch_a)
        self.assertEqual(self.admin_api.post(f"/api/batches/{self.batch_a.pk}/write_off/")
                         .status_code, 200)
        self.assertEqual(self.register()["results"], [])
        rows = self.register(include_empty=1)["results"]
        self.assertEqual([(r["batch_no"], r["quantity"]) for r in rows], [("A", 0)])
        self.assertTrue(Batch.objects.filter(pk=self.batch_a.pk).exists())


class ManualExpiryTests(ExpiredItemsTestCase):
    def test_marking_keeps_the_batch_itself_and_moves_nothing(self):
        movements = StockMovement.objects.count()
        response = self.mark(self.batch_a)
        self.assertEqual(response.status_code, 200, response.data)

        batch = Batch.objects.get(pk=self.batch_a.pk)
        self.assertEqual(
            (batch.item_id, batch.batch_no, batch.cost_price, batch.sale_price,
             batch.expiry_date, batch.supplier),
            (self.paracetamol.pk, "A", Decimal("8.00"), Decimal("15.00"),
             self.batch_a.expiry_date, self.batch_a.supplier))
        self.assertIsNotNone(batch.marked_expired_at)
        self.assertEqual(batch.marked_expired_by, self.hospital_admin)
        self.assertEqual(batch.marked_expired_reason, "Cold chain broken")
        self.assertEqual(StockRecord.objects.get(batch=batch).quantity, 30)
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(Batch.objects.filter(batch_no="A").count(), 1)   # no copy

    def test_a_marked_lot_appears_as_marked_expired(self):
        self.mark(self.batch_a)
        rows = self.register()["results"]
        self.assertEqual([(r["batch_no"], r["expiry_status"], r["marked_expired_reason"])
                          for r in rows], [("A", "marked_expired", "Cold chain broken")])
        self.assertEqual([r["batch_no"] for r in self.register(status="expired")["results"]],
                         [])

    def test_a_marked_lot_is_excluded_from_dispensing_the_till_and_the_picker(self):
        self.mark(self.batch_a)
        self.assertEqual(available_quantity(self.paracetamol), 20)

        patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                         created_by=self.hospital_admin)
        prescription = create_prescription(patient=patient, doctor=self.doctor,
                                           item=self.paracetamol, quantity=5)
        dispense_prescription(prescription=pay_for(prescription, by=self.pharmacist), pharmacist=self.pharmacist)
        self.assertEqual(StockRecord.objects.get(batch=self.batch_a).quantity, 30)
        self.assertEqual(StockRecord.objects.get(batch=self.batch_b).quantity, 15)

        pos.open_register(operator=self.cashier, opening_float="0")
        sale, _ = pos.complete_sale(operator=self.cashier,
                                    lines=[{"item": self.paracetamol.pk, "quantity": 2}])
        self.assertEqual(list(sale.items.values_list("batch__batch_no", flat=True)), ["B"])
        self.assertEqual(StockRecord.objects.get(batch=self.batch_a).quantity, 30)

        # 13 left on B; asking for 20 must not reach into A.
        with self.assertRaises(ValidationError):
            create_prescription(patient=patient, doctor=self.doctor,
                                item=self.paracetamol, quantity=20)

    def test_a_marked_lot_is_not_transferred_by_fefo(self):
        store_lot = stock_the_store(item=self.paracetamol, quantity=10,
                                    actor=self.store_keeper, batch_no="S", expiry_days=30)
        self.mark(store_lot)
        with self.assertRaises(ValidationError):
            fefo_lines_for(item=self.paracetamol, quantity=1, location=self.store)

    def test_a_marked_lot_can_then_be_written_off(self):
        self.mark(self.batch_a)
        response = self.admin_api.post(f"/api/batches/{self.batch_a.pk}/write_off/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(StockRecord.objects.get(batch=self.batch_a).quantity, 0)
        self.assertTrue(StockMovement.objects.filter(batch=self.batch_a,
                                                     reason="expired_writeoff").exists())

    def test_marking_needs_a_reason_and_happens_once(self):
        self.assertEqual(self.mark(self.batch_a, reason=" ").data["code"], "reason_required")
        self.assertEqual(self.mark(self.batch_a).status_code, 200)
        self.assertEqual(self.mark(self.batch_a).data["code"], "already_marked")

    def test_marking_and_returning_to_use_are_audited_and_reversible(self):
        self.mark(self.batch_a)
        row = AuditLog.objects.get(action="stock.batch_marked_expired",
                                   object_id=self.batch_a.pk)
        self.assertEqual((row.actor, row.details["reason"], row.details["stock"]),
                         (self.hospital_admin, "Cold chain broken", {"pharmacy": 30}))

        refused = self.admin_api.post(f"/api/batches/{self.batch_a.pk}/return-to-use/", {},
                                      format="json")
        self.assertEqual(refused.data["code"], "reason_required")
        back = self.admin_api.post(f"/api/batches/{self.batch_a.pk}/return-to-use/",
                                   {"reason": "Fridge log checked — within range"},
                                   format="json")
        self.assertEqual(back.status_code, 200, back.data)
        self.assertEqual(available_quantity(self.paracetamol), 50)
        self.assertTrue(AuditLog.objects.filter(action="stock.batch_returned_to_use",
                                                object_id=self.batch_a.pk).exists())

    def test_returning_to_use_never_revives_a_lot_past_its_date(self):
        self.mark(self.batch_a)
        self.expire_by_date(self.batch_a)
        self.admin_api.post(f"/api/batches/{self.batch_a.pk}/return-to-use/",
                            {"reason": "Mistake"}, format="json")
        self.batch_a.refresh_from_db()
        self.assertTrue(self.batch_a.is_expired)
        self.assertEqual(available_quantity(self.paracetamol), 20)

    def test_the_store_keeper_may_mark_and_the_pharmacist_may_not(self):
        self.assertEqual(self.mark(self.batch_a, user=self.pharmacist).status_code, 403)
        self.batch_a.refresh_from_db()
        self.assertIsNone(self.batch_a.marked_expired_at)
        self.assertEqual(self.mark(self.batch_a, user=self.store_keeper).status_code, 200)

    def test_the_mark_cannot_be_written_by_a_patch(self):
        self.admin_api.patch(f"/api/batches/{self.batch_a.pk}/", {
            "marked_expired_at": timezone.now().isoformat(), "marked_expired_reason": "x"},
            format="json")
        self.batch_a.refresh_from_db()
        self.assertIsNone(self.batch_a.marked_expired_at)
