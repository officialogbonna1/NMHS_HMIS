"""
The category, walked end to end through both workflows that sell medicine.

The rest of the category tests check one thing each. This one is the
acceptance walk: create a product in a category and find it on every screen
that should show it, move it to another category and find it moved everywhere
at once, retire the category it left and find the history intact — then run a
prescription and a POS sale through to the money and prove both reconcile
exactly as they did before any of this existed.

The two workflows it must not disturb:

    Doctor → Prescription → Pharmacy → Dispensing → Inventory deduction
    Walk-in customer → Pharmacy POS → Payment → Inventory deduction
"""
import csv
import io
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge, PatientLedger, Payment, PaymentAllocation
from apps.inventory import count_csv
from apps.inventory.models import (
    MAIN_STORE, PHARMACY, Item, ItemCategory, StockLocation, StockMovement, StockRecord,
)
from apps.inventory.testing import stock_the_pharmacy
from apps.patients.models import Patient
from apps.pharmacy.services import create_prescription, dispense_prescription
from apps.sales import services as pos

D = Decimal


class CategoryEndToEndTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)
        self.store = StockLocation.objects.get(code=MAIN_STORE)
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F")

    def client_for(self, user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    @staticmethod
    def rows(response):
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data

    # -- Test 1 ----------------------------------------------------------

    def test_1_a_product_in_a_category_appears_on_every_screen_that_needs_it(self):
        admin = self.client_for(self.admin)
        pain = ItemCategory.objects.get(name="Pain Relief / Analgesics")   # seeded
        unit = self.rows(admin.get("/api/units/"))
        # JSON, the way the administration screen posts: a form-encoded body
        # reads a missing `is_active` as False the way an HTML checkbox does.
        created = admin.post("/api/items/", {
            "name": "Paracetamol 500mg", "sku": "PARA500", "category": pain.pk,
            "strength": "500 mg", "dosage_form": "Tablet", "is_active": True,
            **({"unit": unit[0]["id"]} if unit else {}),
        }, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        item = Item.objects.get(pk=created.data["id"])
        stock_the_pharmacy(item=item, quantity=100, actor=self.pharmacist,
                           batch_no="B001", sale_price="50")

        # 1. Inventory — the stock line carries it.
        stock = self.rows(self.client_for(self.pharmacist).get("/api/stock-records/"))
        self.assertEqual(stock[0]["item_category_name"], "Pain Relief / Analgesics")

        # 2. POS — a chip for it, and the product under it.
        till = self.client_for(self.cashier).get("/api/sales/products/",
                                                {"category": pain.pk}).data
        self.assertIn("Pain Relief / Analgesics", [c["name"] for c in till["categories"]])
        self.assertEqual([row["category_name"] for row in till["results"]],
                         ["Pain Relief / Analgesics"])

        # 3. Doctor's picker — the category, never the count.
        picker = self.rows(self.client_for(self.doctor).get("/api/items/"))
        drug = next(row for row in picker if row["name"] == "Paracetamol 500mg")
        self.assertEqual(drug["category"], "Pain Relief / Analgesics")
        self.assertNotIn("total_quantity", drug)

        # 4. Dispensing queue — the category beside what was asked for.
        create_prescription(patient=self.patient, item=item, quantity=10, doctor=self.doctor)
        queue = self.rows(self.client_for(self.pharmacist).get("/api/prescriptions/?status=pending"))
        self.assertEqual(queue[0]["item_category"], "Pain Relief / Analgesics")

    # -- Test 2 ----------------------------------------------------------

    def test_2_changing_the_category_changes_it_everywhere_at_once(self):
        item = Item.objects.create(name="Coartem",
                                   category=ItemCategory.objects.get(name="Antibiotics"))
        stock_the_pharmacy(item=item, quantity=20, actor=self.pharmacist, batch_no="C1",
                           sale_price="100")
        create_prescription(patient=self.patient, item=item, quantity=2, doctor=self.doctor)

        malaria = ItemCategory.objects.get(name="Antimalarials")
        moved = self.client_for(self.admin).patch(f"/api/items/{item.pk}/",
                                                  {"category": malaria.pk})
        self.assertEqual(moved.status_code, 200, moved.data)

        # One relation, so every reader follows — nothing to update twice.
        self.assertEqual(
            self.rows(self.client_for(self.pharmacist).get("/api/stock-records/"))[0]
            ["item_category_name"], "Antimalarials")
        self.assertEqual(
            self.rows(self.client_for(self.pharmacist).get("/api/prescriptions/?status=pending"))[0]
            ["item_category"], "Antimalarials")
        self.assertEqual(
            next(row for row in self.rows(self.client_for(self.doctor).get("/api/items/"))
                 if row["name"] == "Coartem")["category"], "Antimalarials")
        self.assertEqual(
            self.client_for(self.cashier).get("/api/sales/products/").data["results"][0]
            ["category_name"], "Antimalarials")

    # -- Test 3 ----------------------------------------------------------

    def test_3_deactivating_a_category_leaves_every_historical_record_intact(self):
        pain = ItemCategory.objects.get(name="Pain Relief / Analgesics")
        item = Item.objects.create(name="Ibuprofen 400mg", category=pain)
        batch = stock_the_pharmacy(item=item, quantity=60, actor=self.pharmacist,
                                   batch_no="I1", sale_price="80")
        script = create_prescription(patient=self.patient, item=item, quantity=10,
                                     doctor=self.doctor)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)
        charge = Charge.objects.filter(patient=self.patient).latest("id")
        before = (charge.amount, charge.status, item.quantity_at(self.pharmacy))

        retired = self.client_for(self.admin).patch(f"/api/item-categories/{pain.pk}/",
                                                    {"is_active": False})
        self.assertEqual(retired.status_code, 200, retired.data)

        # The product keeps the category; the history keeps its reading.
        item.refresh_from_db(); charge.refresh_from_db()
        self.assertEqual(item.category_id, pain.pk)
        self.assertEqual((charge.amount, charge.status, item.quantity_at(self.pharmacy)), before)
        self.assertTrue(StockMovement.objects.filter(batch=batch, reason="prescription").exists())
        # And it is deleted by nobody while a product points at it.
        self.assertEqual(self.client_for(self.admin).delete(
            f"/api/item-categories/{pain.pk}/").status_code, 409)
        # Existing products can still be moved to an active category.
        moved = self.client_for(self.admin).patch(
            f"/api/items/{item.pk}/", {"category": ItemCategory.objects.get(name="Other").pk})
        self.assertEqual(moved.status_code, 200, moved.data)

    # -- Tests 4 and 5 ---------------------------------------------------

    def test_4_and_5_the_csv_round_trip_keeps_the_category_and_the_audit_trail(self):
        item = Item.objects.create(name="Paracetamol 500mg", sku="PARA500",
                                   category=ItemCategory.objects.get(
                                       name="Pain Relief / Analgesics"))
        batch = stock_the_pharmacy(item=item, quantity=100, actor=self.pharmacist,
                                   batch_no="B001", sale_price="50")

        # Test 4 — the export carries the category.
        export = self.client_for(self.admin).get("/api/stock-counts/export/")
        self.assertEqual(export.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0]["Category"], "Pain Relief / Analgesics")

        # Test 5 — a valid count applies through the ordinary stock adjustment.
        rows[0]["Counted Qty"] = "92"
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=count_csv.HEADERS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
        preview = count_csv.preview_import(content=buffer.getvalue().encode("utf-8"),
                                           filename="count.csv", user=self.admin)
        self.assertEqual(preview.errors, [])
        applied = count_csv.apply_import(stock_import=preview, user=self.admin)

        self.assertEqual(item.quantity_at(self.pharmacy), 92)
        movement = StockMovement.objects.get(batch=batch, reason="stock_count")
        self.assertEqual(movement.change, -8)
        self.assertIn(applied.reference, movement.reference)
        # The product/category relationship is exactly as it was.
        item.refresh_from_db()
        self.assertEqual(item.category.name, "Pain Relief / Analgesics")
        self.assertEqual(item.sku, "PARA500")

    # -- Test 6 ----------------------------------------------------------

    def test_6_a_pos_sale_still_reconciles_from_till_to_stock_to_report(self):
        item = Item.objects.create(name="Vitamin C 100mg",
                                   category=ItemCategory.objects.get(
                                       name="Vitamins & Supplements"))
        batch = stock_the_pharmacy(item=item, quantity=100, actor=self.pharmacist,
                                   batch_no="V1", sale_price="50")
        pos.open_register(operator=self.cashier, opening_float="1000")

        sale, _ = pos.complete_sale(operator=self.cashier,
                                    lines=[{"item": item.pk, "quantity": 4}],
                                    customer_name="Musa Bello")

        # POS → payment.
        self.assertEqual(sale.total_amount, D("200"))
        payment = Payment.objects.get(pos_sale=sale)
        self.assertEqual((payment.amount, payment.channel, payment.patient), (D("200"), "pharmacy", None))
        # A walk-in raises no charge and no ledger.
        self.assertFalse(Charge.objects.exists())
        self.assertFalse(PatientLedger.objects.exists())
        self.assertFalse(PaymentAllocation.objects.exists())

        # → inventory movement, off the dispensing shelf, through the service.
        self.assertEqual(item.quantity_at(self.pharmacy), 96)
        movement = StockMovement.objects.get(batch=batch, reason="sale")
        self.assertEqual((movement.change, movement.location), (-4, self.pharmacy))

        # → financial reporting, including the category breakdown, which is a
        #   decomposition of the till's own figures and never a second count.
        summary = self.client_for(self.cashier).get("/api/sales/summary/", {"preset": "today"}).data
        self.assertTrue(summary["reconciles"])
        self.assertEqual(D(str(summary["sales"]["total"])), D("200"))
        by_label = {row["label"]: row for row in summary["categories"]}
        self.assertEqual(D(str(by_label["Vitamins & Supplements"]["net"])), D("200"))
        self.assertEqual(sum(D(str(row["net"])) for row in summary["categories"]),
                         D(str(summary["sales"]["total"])))
