"""
The product category, across every workflow that reads it.

`ItemCategory` is one row per group and `Item.category` is a relation to it —
there is no category text on a batch, a prescription, a sale line or a
movement, so renaming a category renames it everywhere at once and nothing can
drift. This file holds that, and holds the boundary around it:

* a category is **configuration** — read by everyone who works stock, written
  by an admin, retired rather than deleted once products point at it;
* it is **visible** wherever it helps — the stock list, the movement log, the
  doctor's picker, the dispensing queue, the till — and **decides nothing**:
  dispensing still resolves stock, expiry, FEFO and location exactly as it did
  before the category existed;
* and it is **never inferred**. Nothing here classifies a product, and the CSV
  count recognises a category or reports it, but never creates one.

The last section is the one that matters most: a category is a label on a
product, so adding one must leave the money — charge, payment, allocation,
ledger — bit-for-bit as it was.
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
from apps.inventory.testing import (
    category, product, stock_the_pharmacy, stock_the_store,
)
from apps.patients.models import Patient
from apps.pharmacy.services import create_prescription, dispense_prescription


class CategoryTestCase(TestCase):
    """One pharmacy, two categories, and one of everything that reads them."""

    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")

        self.pharmacy = StockLocation.objects.get(code=PHARMACY)
        self.store = StockLocation.objects.get(code=MAIN_STORE)

        self.pain = category("Pain Relief / Analgesics")
        self.antibiotics = category("Antibiotics")

        self.para = product("Paracetamol 500mg", unit_name="tablet",
                            category_name="Pain Relief / Analgesics", sku="PARA500",
                            strength="500 mg", dosage_form="Tablet")
        self.amox = product("Amoxicillin 500mg", unit_name="capsule",
                            category_name="Antibiotics", sku="AMOX500")

        self.para_shelf = stock_the_pharmacy(item=self.para, quantity=100,
                                             actor=self.pharmacist, batch_no="B001")
        self.amox_shelf = stock_the_pharmacy(item=self.amox, quantity=50,
                                             actor=self.pharmacist, batch_no="B002")

        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F")
        self.api = APIClient()

    def as_user(self, user):
        self.api.force_authenticate(user)
        return self.api

    @staticmethod
    def rows(response):
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data


# ---------------------------------------------------------------- categories


class CategoryConfigurationTests(CategoryTestCase):
    """Create, edit, retire — and never delete one products are filed under."""

    def test_the_default_categories_are_seeded_and_classify_nothing(self):
        # A fresh install opens with a vocabulary, not an empty list.
        names = set(ItemCategory.objects.values_list("name", flat=True))
        for expected in ["Pain Relief / Analgesics", "Antibiotics", "Antimalarials",
                         "Medical Consumables", "Syringes & Needles", "Other"]:
            self.assertIn(expected, names)
        # And it guesses at nothing: a product created without a category has none.
        bare = Item.objects.create(name="Something nobody has classified")
        self.assertIsNone(bare.category_id)

    def test_the_seed_adopts_an_existing_spelling_rather_than_duplicating_it(self):
        # `setUp` made "Pain Relief / Analgesics" through the same name the seed
        # uses, and the seed ran before it — one row, not two.
        self.assertEqual(ItemCategory.objects.filter(name__icontains="Analgesics").count(), 1)

    def test_an_admin_creates_edits_and_retires_a_category(self):
        api = self.as_user(self.admin)
        created = api.post("/api/item-categories/",
                           {"name": "Antimalarial Injections", "description": "Parenteral."})
        self.assertEqual(created.status_code, 201, created.data)
        category_id = created.data["id"]

        renamed = api.patch(f"/api/item-categories/{category_id}/", {"name": "Antimalarials (IV)"})
        self.assertEqual(renamed.status_code, 200, renamed.data)
        self.assertEqual(ItemCategory.objects.get(pk=category_id).name, "Antimalarials (IV)")

        retired = api.patch(f"/api/item-categories/{category_id}/", {"is_active": False})
        self.assertEqual(retired.status_code, 200, retired.data)
        self.assertFalse(ItemCategory.objects.get(pk=category_id).is_active)

        # Retired is a state, not a disappearance: it is off the working list
        # and still readable, and `?all=1` is how the admin screen sees it.
        working = {row["id"] for row in self.rows(api.get("/api/item-categories/"))}
        self.assertNotIn(category_id, working)
        everything = {row["id"] for row in self.rows(api.get("/api/item-categories/?all=1"))}
        self.assertIn(category_id, everything)

    def test_a_category_products_use_cannot_be_deleted(self):
        api = self.as_user(self.admin)
        refused = api.delete(f"/api/item-categories/{self.pain.pk}/")
        self.assertEqual(refused.status_code, 409, refused.data)
        self.assertIn("deactivate", str(refused.data).lower())
        self.assertTrue(ItemCategory.objects.filter(pk=self.pain.pk).exists())
        # The product it protects is untouched.
        self.para.refresh_from_db()
        self.assertEqual(self.para.category_id, self.pain.pk)

    def test_a_category_nobody_used_is_deleted(self):
        api = self.as_user(self.admin)
        spare = ItemCategory.objects.create(name="Typed By Mistake")
        gone = api.delete(f"/api/item-categories/{spare.pk}/")
        self.assertEqual(gone.status_code, 204, getattr(gone, "data", None))
        self.assertFalse(ItemCategory.objects.filter(pk=spare.pk).exists())

    def test_retiring_a_category_leaves_its_products_and_their_history_alone(self):
        self.pain.is_active = False
        self.pain.save(update_fields=["is_active"])

        self.para.refresh_from_db()
        self.assertEqual(self.para.category_id, self.pain.pk)
        self.assertEqual(self.para.category_name, "Pain Relief / Analgesics")
        # Stock, batches and movements all still read.
        self.assertEqual(self.para.quantity_at(self.pharmacy), 100)
        self.assertTrue(StockMovement.objects.filter(batch__item=self.para).exists())
        # And the product is still dispensable — a retired category is a
        # closed list for new filing, never a block on medicine.
        script = create_prescription(patient=self.patient, item=self.para, quantity=5,
                                     doctor=self.doctor)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)
        script.refresh_from_db()
        self.assertEqual(script.status, "dispensed")

    def test_a_product_is_moved_from_one_category_to_another(self):
        api = self.as_user(self.admin)
        moved = api.patch(f"/api/items/{self.para.pk}/", {"category": self.antibiotics.pk})
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertEqual(moved.data["category_name"], "Antibiotics")
        # One relation, so every reader moves with it — no second copy to update.
        record = StockRecord.objects.get(batch=self.para_shelf, location=self.pharmacy)
        row = self.as_user(self.pharmacist).get(f"/api/stock-records/{record.pk}/").data
        self.assertEqual(row["item_category_name"], "Antibiotics")

    def test_a_pharmacist_reads_the_categories_but_cannot_change_them(self):
        api = self.as_user(self.pharmacist)
        self.assertEqual(api.get("/api/item-categories/").status_code, 200)
        refused = api.post("/api/item-categories/", {"name": "Smuggled"})
        self.assertEqual(refused.status_code, 403, refused.data)
        self.assertFalse(ItemCategory.objects.filter(name="Smuggled").exists())


# ----------------------------------------------------------------- inventory


class CategoryInInventoryTests(CategoryTestCase):
    """The category is on the stock line, and it narrows the list."""

    def test_a_stock_line_carries_the_product_category_sku_and_unit(self):
        rows = self.rows(self.as_user(self.pharmacist).get("/api/stock-records/"))
        line = next(row for row in rows if row["batch"] == self.para_shelf.pk)
        self.assertEqual(line["item_name"], "Paracetamol 500mg")
        self.assertEqual(line["item_category_name"], "Pain Relief / Analgesics")
        self.assertEqual(line["item_category"], self.pain.pk)
        self.assertEqual(line["item_sku"], "PARA500")
        self.assertEqual(line["item_unit"], "tablet")
        self.assertEqual(line["quantity"], 100)

    def test_filtering_stock_by_category_narrows_it_without_a_second_system(self):
        api = self.as_user(self.pharmacist)
        pain = self.rows(api.get(f"/api/stock-records/?batch__item__category={self.pain.pk}"))
        self.assertEqual([row["item_name"] for row in pain], ["Paracetamol 500mg"])
        # And it composes with the location filter, rather than replacing it.
        on_shelf = self.rows(api.get(
            f"/api/stock-records/?batch__item__category={self.pain.pk}"
            f"&location={self.pharmacy.pk}"))
        self.assertEqual(len(on_shelf), 1)
        in_store = self.rows(api.get(
            f"/api/stock-records/?batch__item__category={self.pain.pk}"
            f"&location={self.store.pk}"))
        self.assertEqual(in_store, [])
        # The quantities themselves are untouched by any of it.
        self.assertEqual(self.para.quantity_at(self.pharmacy), 100)
        self.assertEqual(self.amox.quantity_at(self.pharmacy), 50)

    def test_the_movement_log_reads_and_filters_by_category(self):
        api = self.as_user(self.pharmacist)
        rows = self.rows(api.get(f"/api/stock-movements/?batch__item__category={self.pain.pk}"))
        self.assertTrue(rows)
        self.assertTrue(all(row["item_name"] == "Paracetamol 500mg" for row in rows))
        self.assertEqual(rows[0]["item_category_name"], "Pain Relief / Analgesics")

    def test_stock_in_the_main_store_is_categorised_the_same_way(self):
        stock_the_store(item=self.para, quantity=400, actor=self.pharmacist, batch_no="B003")
        rows = self.rows(self.as_user(self.pharmacist).get(
            f"/api/stock-records/?location={self.store.pk}"
            f"&batch__item__category={self.pain.pk}"))
        self.assertEqual([row["quantity"] for row in rows], [400])
        # Product + batch + location still, with no total anywhere: 100 on the
        # shelf, 400 in the store, 500 owned.
        self.assertEqual(self.para.total_quantity, 500)


# -------------------------------------------------------------------- CSV


class CategoryInCountCsvTests(CategoryTestCase):
    """Export carries it; import recognises it and never invents one."""

    def exported(self, **params):
        response = self.as_user(self.admin).get("/api/stock-counts/export/", params)
        self.assertEqual(response.status_code, 200)
        return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))

    @staticmethod
    def to_csv(rows):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=count_csv.HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue().encode("utf-8")

    def upload(self, rows, user=None):
        return count_csv.preview_import(content=self.to_csv(rows), filename="count.csv",
                                        user=user or self.admin)

    def test_the_export_has_a_category_column_filled_in(self):
        rows = self.exported()
        self.assertIn("Category", count_csv.HEADERS)
        by_batch = {row["Batch"]: row for row in rows}
        self.assertEqual(by_batch["B001"]["Category"], "Pain Relief / Analgesics")
        self.assertEqual(by_batch["B001"]["SKU"], "PARA500")
        self.assertEqual(by_batch["B002"]["Category"], "Antibiotics")

    def test_the_export_can_be_taken_one_category_at_a_time(self):
        rows = self.exported(category=self.pain.pk)
        self.assertEqual([row["Product"] for row in rows], ["Paracetamol 500mg"])

    def test_a_valid_sheet_imports_and_the_category_travels_with_the_preview(self):
        rows = self.exported()
        for row in rows:
            row["Counted Qty"] = "92" if row["Batch"] == "B001" else "50"
        preview = self.upload(rows)
        self.assertEqual(preview.errors, [])
        counted = {row["batch_no"]: row for row in preview.rows}
        self.assertEqual(counted["B001"]["category_name"], "Pain Relief / Analgesics")
        self.assertEqual(counted["B001"]["difference"], -8)
        self.assertEqual(sorted(preview.summary["categories"]),
                         ["Antibiotics", "Pain Relief / Analgesics"])
        self.assertEqual(preview.summary["unknown_categories"], [])

    def test_spelling_and_punctuation_are_not_four_different_categories(self):
        # The same category, typed four ways a spreadsheet produces it.
        for spelling in ["Pain Relief / Analgesics", "pain relief / analgesics",
                         "PAIN-RELIEF / ANALGESICS", "Pain  Relief/Analgesics"]:
            with self.subTest(spelling=spelling):
                rows = self.exported(category=self.pain.pk)
                rows[0]["Category"] = spelling
                rows[0]["Counted Qty"] = "100"
                preview = self.upload(rows)
                self.assertEqual(preview.errors, [], spelling)
        # And nothing was created along the way.
        self.assertEqual(ItemCategory.objects.filter(name__icontains="analgesic").count(), 1)

    def test_a_misspelt_category_is_reported_by_name_and_never_created(self):
        rows = self.exported(category=self.pain.pk)
        rows[0]["Category"] = "Pain Relif"
        rows[0]["Counted Qty"] = "92"
        preview = self.upload(rows)

        self.assertTrue(preview.errors)
        problem = next(e for e in preview.errors if e["column"] == "Category")
        self.assertIn("Pain Relif", problem["message"])
        self.assertIn("never creates one", problem["message"])
        self.assertEqual(preview.summary["unknown_categories"], ["Pain Relif"])
        # Named, not created — and nothing is applicable from a file with a
        # problem in it, so no stock moved either.
        self.assertFalse(ItemCategory.objects.filter(name="Pain Relif").exists())
        self.assertFalse(preview.is_applicable)
        self.assertEqual(self.para.quantity_at(self.pharmacy), 100)

    def test_a_count_sheet_cannot_re_file_a_product(self):
        rows = self.exported(category=self.pain.pk)
        rows[0]["Category"] = "Antibiotics"   # a real category, the wrong product
        rows[0]["Counted Qty"] = "92"
        preview = self.upload(rows)

        problem = next(e for e in preview.errors if e["column"] == "Category")
        self.assertIn("Antibiotics", problem["message"])
        self.assertFalse(preview.is_applicable)
        self.para.refresh_from_db()
        self.assertEqual(self.para.category_id, self.pain.pk)

    def test_a_blank_category_cell_is_fine(self):
        rows = self.exported(category=self.pain.pk)
        rows[0]["Category"] = ""
        rows[0]["Counted Qty"] = "95"
        preview = self.upload(rows)
        self.assertEqual(preview.errors, [])
        self.assertTrue(preview.is_applicable)

    def test_applying_an_import_still_goes_through_the_stock_adjustment(self):
        rows = self.exported(category=self.pain.pk)
        rows[0]["Counted Qty"] = "92"
        preview = self.upload(rows)
        applied = count_csv.apply_import(stock_import=preview, user=self.admin)

        self.assertEqual(applied.status, "applied")
        self.assertEqual(self.para.quantity_at(self.pharmacy), 92)
        movement = StockMovement.objects.filter(batch=self.para_shelf,
                                                reason="stock_count").latest("created_at")
        self.assertEqual(movement.change, -8)
        # The product and its category came through unchanged — a count moves
        # a quantity and nothing else.
        self.para.refresh_from_db()
        self.assertEqual(self.para.category_id, self.pain.pk)
        self.assertEqual(self.para.sku, "PARA500")


# -------------------------------------------------- prescribing & dispensing


class CategoryInTheClinicalWorkflowTests(CategoryTestCase):
    """Doctor → prescription → pharmacy → dispensing, with a label added."""

    def test_the_doctor_picker_shows_the_category_and_still_hides_the_count(self):
        rows = self.rows(self.as_user(self.doctor).get("/api/items/"))
        para = next(row for row in rows if row["name"] == "Paracetamol 500mg")
        self.assertEqual(para["category"], "Pain Relief / Analgesics")
        self.assertTrue(para["available"])
        # Rule 7 is untouched: availability, never a quantity.
        self.assertNotIn("total_quantity", para)
        self.assertNotIn("by_location", para)

    def test_a_doctor_can_search_the_catalogue_by_category(self):
        rows = self.rows(self.as_user(self.doctor).get("/api/items/?search=Antibiotics"))
        self.assertEqual([row["name"] for row in rows], ["Amoxicillin 500mg"])

    def test_the_dispensing_queue_shows_the_category_of_what_was_prescribed(self):
        create_prescription(patient=self.patient, item=self.para, quantity=10,
                            doctor=self.doctor)
        rows = self.rows(self.as_user(self.pharmacist).get("/api/prescriptions/?status=pending"))
        line = rows[0]
        self.assertEqual(line["item_name"], "Paracetamol 500mg")
        self.assertEqual(line["item_category"], "Pain Relief / Analgesics")
        self.assertEqual(line["quantity"], 10)

    def test_a_prescription_references_the_product_not_the_category(self):
        script = create_prescription(patient=self.patient, item=self.para, quantity=10,
                                     doctor=self.doctor)
        self.assertEqual(script.item_id, self.para.pk)
        # Moving the product to another category moves what the queue reads,
        # because there is one relation and no copy of the text anywhere.
        self.para.category = self.antibiotics
        self.para.save(update_fields=["category"])
        rows = self.rows(self.as_user(self.pharmacist).get("/api/prescriptions/?status=pending"))
        self.assertEqual(rows[0]["item_category"], "Antibiotics")
        script.refresh_from_db()
        self.assertEqual(script.item_id, self.para.pk)

    def test_dispensing_still_uses_fefo_the_dispensing_shelf_and_the_expiry_rule(self):
        # A second, shorter-dated lot on the shelf, and a lot in the store that
        # must not be touched however the category filters are set.
        short = stock_the_pharmacy(item=self.para, quantity=30, actor=self.pharmacist,
                                   batch_no="B000", expiry_days=10)
        stock_the_store(item=self.para, quantity=400, actor=self.pharmacist, batch_no="B900")

        script = create_prescription(patient=self.patient, item=self.para, quantity=40,
                                     doctor=self.doctor)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

        # FEFO within the dispensing location: all 30 off the short-dated lot,
        # then 10 off the next — and nothing off the Main Store.
        self.assertEqual(short.quantity_at(self.pharmacy), 0)
        self.assertEqual(self.para_shelf.quantity_at(self.pharmacy), 90)
        self.assertEqual(self.para.quantity_at(self.store), 400)

    def test_expired_stock_is_still_refused_whatever_its_category(self):
        from apps.pharmacy.services import OutOfStockError, available_quantity

        expired = stock_the_pharmacy(item=self.amox, quantity=999, actor=self.pharmacist,
                                     batch_no="OLD", expiry_days=-5)
        # 1,049 units physically standing on the shelf, 50 of them dispensable:
        # an expired lot is stock the hospital owns and cannot give anybody.
        self.assertEqual(self.amox.quantity_at(self.pharmacy), 1049)
        self.assertEqual(available_quantity(self.amox), 50)

        with self.assertRaises(OutOfStockError):
            create_prescription(patient=self.patient, item=self.amox, quantity=60,
                                doctor=self.doctor)

        # And what *is* dispensable comes off the unexpired lot, leaving the
        # expired one for the write-off it needs.
        script = create_prescription(patient=self.patient, item=self.amox, quantity=50,
                                     doctor=self.doctor)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)
        self.assertEqual(expired.quantity_at(self.pharmacy), 999)
        self.assertEqual(self.amox_shelf.quantity_at(self.pharmacy), 0)


# ------------------------------------------------------------ financial floor


class CategoryChangesNoMoneyTests(CategoryTestCase):
    """
    A category is a label on a product. Dispensing it must raise the same
    charge, write the same movement and leave the same ledger as it did before
    categories were visible anywhere — so this dispenses once, records every
    figure, then re-files the product and proves nothing about the money moved.
    """

    def figures(self):
        charge = Charge.objects.filter(patient=self.patient).latest("id")
        ledger = PatientLedger.objects.get(patient=self.patient)
        return {
            "charge": charge.amount, "paid": charge.amount_paid,
            "status": charge.status, "source": charge.source_type,
            "department": charge.department_id,
            "ledger_charges": ledger.total_charges, "ledger_payments": ledger.total_payments,
            "payments": list(Payment.objects.filter(patient=self.patient)
                             .values_list("amount", flat=True)),
            "allocations": list(PaymentAllocation.objects
                                .filter(charge__patient=self.patient)
                                .values_list("amount", flat=True)),
        }

    def test_dispensing_bills_the_same_money_before_and_after_a_category_change(self):
        script = create_prescription(patient=self.patient, item=self.para, quantity=10,
                                     doctor=self.doctor)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

        before = self.figures()
        # 10 units × ₦20 from `testing._place`, charged to the Pharmacy.
        self.assertEqual(before["charge"], Decimal("200.00"))
        self.assertEqual(before["source"], "prescription")
        self.assertEqual(self.para.quantity_at(self.pharmacy), 90)

        # Re-file the drug, retire the category it came from, and add a new one.
        self.para.category = self.antibiotics
        self.para.save(update_fields=["category"])
        self.pain.is_active = False
        self.pain.save(update_fields=["is_active"])

        self.assertEqual(self.figures(), before)
        # And the stock is exactly where it was: no movement was written by
        # any of that.
        self.assertEqual(self.para.quantity_at(self.pharmacy), 90)
        self.assertEqual(
            StockMovement.objects.filter(batch__item=self.para).count(),
            2,  # the receipt, and the dispense
        )
