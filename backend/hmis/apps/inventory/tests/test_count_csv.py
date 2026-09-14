"""
The stock count as a spreadsheet: export → count → import → preview → confirm.

What this file holds:

* the export is the count sheet — product, SKU, category, unit, location,
  batch, expiry, system quantity — and moves nothing;
* a preview validates every row and moves nothing;
* confirming posts ordinary count documents whose movements are the
  *differences* (100 → 92 is a −8 movement, never an overwrite);
* a file with one problem in it applies nothing, stock that moved since the
  export is a conflict, an import applies once, and a failure part-way through
  leaves no partial count behind;
* and a person counts only where they may.
"""
import csv
import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.inventory import count_csv
from apps.inventory.models import (MAIN_STORE, PHARMACY, StockCount, StockCountImport, StockCountLine,
                                   StockLocation, StockMovement, StockRecord)
from apps.inventory.services import receive_stock
from apps.inventory.testing import product, stock_the_pharmacy, stock_the_store


class CountCsvTestCase(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username="inv", password="t", role="inventory_manager")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)
        self.store = StockLocation.objects.get(code=MAIN_STORE)
        self.para = product("Paracetamol 500mg", unit_name="tablet", category_name="Analgesics", sku="PARA-500")
        self.amox = product("Amoxicillin 250mg", unit_name="capsule", category_name="Antibiotics")
        self.para_shelf = stock_the_pharmacy(item=self.para, quantity=100, actor=self.manager, batch_no="P1")
        self.amox_store = stock_the_store(item=self.amox, quantity=100, actor=self.manager, batch_no="A1")
        self.api = APIClient()

    # --- helpers ---------------------------------------------------------

    def quantity(self, batch, location):
        return StockRecord.objects.get(batch=batch, location=location).quantity

    def export(self, user, **params):
        self.api.force_authenticate(user)
        return self.api.get("/api/stock-counts/export/", params)

    def exported(self, user=None, **params):
        response = self.export(user or self.manager, **params)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))

    @staticmethod
    def row(rows, batch_no):
        return next(row for row in rows if row["Batch"] == batch_no)

    def counted(self, rows, **by_batch):
        """Fill in Counted Qty by batch number: `counted(rows, P1=92)`."""
        for batch_no, value in by_batch.items():
            self.row(rows, batch_no)["Counted Qty"] = str(value)
        return rows

    @staticmethod
    def to_csv(rows):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=count_csv.HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue().encode("utf-8")

    def upload(self, user, content, name="count.csv"):
        self.api.force_authenticate(user)
        return self.api.post("/api/stock-count-imports/",
                             {"file": SimpleUploadedFile(name, content, content_type="text/csv")},
                             format="multipart")

    def preview(self, rows, user=None):
        response = self.upload(user or self.manager, self.to_csv(rows))
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def apply(self, preview, user=None):
        self.api.force_authenticate(user or self.manager)
        return self.api.post(f"/api/stock-count-imports/{preview['id']}/apply/")

    def assertRefused(self, content, fragment, column=None, user=None):
        """The file previews with this error, cannot be applied, and moves nothing."""
        response = self.upload(user or self.manager, content)
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data
        self.assertFalse(data["is_applicable"])
        messages = [error["message"] for error in data["errors"]]
        self.assertTrue(any(fragment in message for message in messages), messages)
        if column:
            self.assertIn(column, [error["column"] for error in data["errors"]])
        before = StockMovement.objects.count()
        self.assertEqual(self.apply(data).status_code, 400)
        self.assertEqual(StockMovement.objects.count(), before)
        return data


# ------------------------------------------------------------------ export


class ExportTests(CountCsvTestCase):
    def test_the_export_is_a_count_sheet_with_the_system_quantity(self):
        response = self.export(self.manager)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment;", response["Content-Disposition"])
        header = next(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(header, count_csv.HEADERS)

        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        para = self.row(rows, "P1")
        self.assertEqual(
            {key: para[key] for key in ("SKU", "Product", "Category", "Unit", "Location", "System Qty",
                                        "Counted Qty", "Difference")},
            {"SKU": "PARA-500", "Product": "Paracetamol 500mg", "Category": "Analgesics", "Unit": "tablet",
             "Location": "Pharmacy", "System Qty": "100", "Counted Qty": "", "Difference": ""})
        self.assertEqual(para["Line ID"], str(StockRecord.objects.get(batch=self.para_shelf).pk))
        self.assertEqual(para["Expiry"], self.para_shelf.expiry_date.isoformat())

    def test_the_export_narrows_to_one_location(self):
        self.assertEqual([row["Batch"] for row in self.exported(location=self.store.pk)], ["A1"])

    def test_a_pharmacist_exports_only_the_dispensing_shelf(self):
        self.assertEqual({row["Location"] for row in self.exported(self.pharmacist)}, {"Pharmacy"})
        self.assertEqual(self.export(self.pharmacist, location=self.store.pk).status_code, 403)

    def test_roles_that_do_not_count_stock_cannot_export(self):
        for role in ("cashier", "doctor", "reception", "accountant", "nurse"):
            user = User.objects.create_user(username=f"u-{role}", password="t", role=role)
            with self.subTest(role=role):
                self.assertEqual(self.export(user).status_code, 403)
        self.api.force_authenticate(None)
        self.assertIn(self.api.get("/api/stock-counts/export/").status_code, (401, 403))

    def test_exporting_moves_nothing_and_is_audited(self):
        before = StockMovement.objects.count()
        self.exported()
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertTrue(AuditLog.objects.filter(action="stock.count_exported").exists())


# ------------------------------------------------------------------ import


class ImportTests(CountCsvTestCase):
    def test_a_preview_validates_and_moves_nothing(self):
        movements = StockMovement.objects.count()
        preview = self.preview(self.counted(self.exported(), P1=92, A1=108))
        self.assertEqual((preview["status"], preview["is_applicable"], preview["errors"]),
                         ("previewed", True, []))
        summary = preview["summary"]
        self.assertEqual((summary["counted"], summary["units_removed"], summary["units_added"],
                          summary["decrease_lines"], summary["increase_lines"]), (2, 8, 8, 1, 1))
        self.assertEqual({(r["batch_no"], r["system_quantity"], r["counted_quantity"], r["difference"])
                          for r in preview["rows"]},
                         {("P1", 100, 92, -8), ("A1", 100, 108, 8)})
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(self.quantity(self.para_shelf, self.pharmacy), 100)

    def test_confirming_posts_an_auditable_adjustment_for_each_difference(self):
        preview = self.preview(self.counted(self.exported(), P1=92, A1=108))
        response = self.apply(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "applied")
        self.assertEqual(self.quantity(self.para_shelf, self.pharmacy), 92)
        self.assertEqual(self.quantity(self.amox_store, self.store), 108)

        down = StockMovement.objects.get(batch=self.para_shelf, reason="stock_count")
        up = StockMovement.objects.get(batch=self.amox_store, reason="stock_count")
        self.assertEqual((down.change, down.location, down.performed_by), (-8, self.pharmacy, self.manager))
        self.assertEqual((up.change, up.location), (8, self.store))
        self.assertIn(preview["reference"], down.reference)

        self.assertEqual(StockCount.objects.filter(imports__pk=preview["id"]).count(), 2)  # one per location
        line = StockCountLine.objects.get(batch=self.para_shelf)
        self.assertEqual((line.system_quantity, line.counted_quantity), (100, 92))
        self.assertTrue(AuditLog.objects.filter(action="stock.count_import_previewed").exists())
        self.assertTrue(AuditLog.objects.filter(action="stock.count_imported").exists())

    def test_a_line_left_blank_is_not_counted_and_not_changed(self):
        preview = self.preview(self.counted(self.exported(), P1=100))
        self.assertEqual((preview["summary"]["not_counted"], preview["summary"]["unchanged"]), (1, 1))
        self.assertEqual(self.apply(preview).status_code, 200)
        self.assertEqual(self.quantity(self.amox_store, self.store), 100)
        self.assertFalse(StockMovement.objects.filter(reason="stock_count").exists())

    def test_an_import_applies_once(self):
        rows = self.counted(self.exported(), P1=92)
        preview = self.preview(rows)
        self.assertEqual(self.apply(preview).status_code, 200)
        self.assertEqual(self.apply(preview).status_code, 400)
        self.assertEqual(self.quantity(self.para_shelf, self.pharmacy), 92)
        again = self.upload(self.manager, self.to_csv(rows)).data
        self.assertFalse(again["is_applicable"])
        self.assertIn("already applied", again["errors"][0]["message"])

    def test_a_preview_can_be_discarded_and_then_never_applied(self):
        preview = self.preview(self.counted(self.exported(), P1=90))
        self.api.force_authenticate(self.manager)
        self.assertEqual(self.api.post(f"/api/stock-count-imports/{preview['id']}/discard/").data["status"],
                         "discarded")
        self.assertEqual(self.apply(preview).status_code, 400)
        self.assertEqual(self.quantity(self.para_shelf, self.pharmacy), 100)

    # --- a broken file applies nothing ------------------------------------

    def test_a_file_that_is_not_a_utf8_csv(self):
        self.assertRefused(b"\xff\xfe\x00L\x00i\x00n\x00e", "not a UTF-8 CSV")

    def test_missing_columns(self):
        self.assertRefused(b"Product,Batch\nParacetamol 500mg,P1\n", "Missing column")

    def test_a_file_with_nothing_counted(self):
        self.assertRefused(self.to_csv(self.exported()), "No line has a Counted Qty")

    def test_an_unknown_stock_line(self):
        rows = self.counted(self.exported(), P1=90)
        self.row(rows, "P1")["Line ID"] = "999999"
        self.assertRefused(self.to_csv(rows), "is not a stock line", "Line ID")

    def test_an_unknown_product(self):
        rows = self.counted(self.exported(), P1=90)
        self.row(rows, "P1")["Product"] = "Ibuprofen 400mg"
        self.assertRefused(self.to_csv(rows), "does not match line", "Product")

    def test_an_sku_that_is_not_the_products(self):
        rows = self.counted(self.exported(), P1=90)
        self.row(rows, "P1")["SKU"] = "NOPE-1"
        self.assertRefused(self.to_csv(rows), "is not the SKU", "SKU")

    def test_invalid_counted_quantities(self):
        for bad in ("abc", "-3", "2.5", "5000000"):
            with self.subTest(bad=bad):
                rows = self.counted(self.exported(), P1=bad)
                self.assertRefused(self.to_csv(rows), "Counted Qty", "Counted Qty")

    def test_a_duplicated_row(self):
        rows = self.counted(self.exported(), P1=90)
        rows.append(dict(self.row(rows, "P1")))
        self.assertRefused(self.to_csv(rows), "appears twice", "Line ID")

    def test_a_batch_or_expiry_that_does_not_match(self):
        for column, value, fragment in (("Batch", "P9", "does not match line"),
                                        ("Expiry", "2099-01-01", "does not match batch"),
                                        ("Expiry", "next year", "is not a date")):
            with self.subTest(column=column, value=value):
                rows = self.counted(self.exported(), P1=90)
                self.row(rows, "P1")[column] = value
                self.assertRefused(self.to_csv(rows), fragment, column)

    def test_stock_that_moved_since_the_export_is_a_conflict(self):
        rows = self.counted(self.exported(), P1=92)
        receive_stock(batch=self.para_shelf, quantity=5, actor=self.manager, location=self.pharmacy)
        self.assertRefused(self.to_csv(rows), "moved after the export", "System Qty")
        self.assertEqual(self.quantity(self.para_shelf, self.pharmacy), 105)

    # --- all or nothing ----------------------------------------------------

    def test_stock_moving_after_the_preview_rolls_the_whole_import_back(self):
        # Main Store is posted first and would succeed; the Pharmacy line then
        # finds its shelf changed. Neither may be applied.
        preview = self.preview(self.counted(self.exported(), P1=92, A1=108))
        receive_stock(batch=self.para_shelf, quantity=1, actor=self.manager, location=self.pharmacy)
        movements = StockMovement.objects.count()
        response = self.apply(preview)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Nothing was applied", response.data["detail"])
        self.assertEqual(self.quantity(self.amox_store, self.store), 100)
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertFalse(StockCount.objects.exists())
        self.assertEqual(StockCountImport.objects.get(pk=preview["id"]).status, "previewed")

    def test_a_failure_part_way_through_leaves_no_partial_count(self):
        stock_import = StockCountImport.objects.get(
            pk=self.preview(self.counted(self.exported(), P1=92, A1=108))["id"])
        real = count_csv.post_stock_count

        def fail_on_the_second_location(**kwargs):
            if StockCount.objects.exists():
                raise RuntimeError("the database went away")
            return real(**kwargs)

        with mock.patch.object(count_csv, "post_stock_count", side_effect=fail_on_the_second_location):
            with self.assertRaises(RuntimeError):
                count_csv.apply_import(stock_import=stock_import, user=self.manager)
        stock_import.refresh_from_db()
        self.assertEqual(stock_import.status, "previewed")
        self.assertFalse(StockCount.objects.exists())
        self.assertFalse(StockMovement.objects.filter(reason="stock_count").exists())
        self.assertEqual((self.quantity(self.para_shelf, self.pharmacy), self.quantity(self.amox_store, self.store)),
                         (100, 100))

    # --- who counts where --------------------------------------------------

    def test_a_pharmacist_cannot_count_the_main_store_from_a_file(self):
        rows = self.counted(self.exported(self.manager), P1=95, A1=90)
        self.assertRefused(self.to_csv(rows), "not authorised to count Main Store", "Location",
                           user=self.pharmacist)

    def test_whoever_confirms_must_be_allowed_to_count_every_shelf_in_it(self):
        preview = self.preview(self.counted(self.exported(), A1=90))
        self.assertEqual(self.apply(preview, user=self.pharmacist).status_code, 403)
        self.assertEqual(self.quantity(self.amox_store, self.store), 100)
        self.assertEqual(StockCountImport.objects.get(pk=preview["id"]).status, "previewed")

    def test_a_pharmacist_counts_their_own_shelf(self):
        preview = self.preview(self.counted(self.exported(self.pharmacist), P1=97), user=self.pharmacist)
        self.assertEqual(self.apply(preview, user=self.pharmacist).status_code, 200)
        self.assertEqual(self.quantity(self.para_shelf, self.pharmacy), 97)

    def test_roles_that_do_not_count_cannot_import(self):
        cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        content = self.to_csv(self.counted(self.exported(), P1=90))
        self.assertEqual(self.upload(cashier, content).status_code, 403)
