"""
Hospital Admin manages the product catalogue from the HMIS front end.

One catalogue — `inventory.Item`, the row Django admin's Add item form writes —
reached through `/api/items/`. What this file holds:

* a Hospital Admin creates, edits, archives, restores and (where nothing
  points at it) deletes a product, and every one of those is an `AuditLog` row;
* a product created there is immediately what the existing Receive a delivery
  form lists and accepts;
* SKU and barcode stay unique, and optional;
* archiving is `is_active=False`: the row and its history stay, and it is
  refused for anything new — receiving, transferring, prescribing, the till;
* a product with history is never deleted, and nothing cascades;
* a pharmacist can do none of it, and loses nothing at the counter.
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
    MAIN_STORE, PHARMACY, Batch, Item, StockLocation, StockMovement, StockRecord,
)
from apps.inventory.services import receive_stock, transfer_stock
from apps.inventory.testing import category, product, stock_the_pharmacy, stock_the_store, unit
from apps.patients.models import Patient
from apps.pharmacy.models import Prescription
from apps.pharmacy.services import create_prescription, dispense_prescription


class ItemManagementTestCase(TestCase):
    def setUp(self):
        self.hospital_admin = User.objects.create_user(username="ha", password="t",
                                                       role="hospital_admin")
        self.super_admin = User.objects.create_user(username="sa", password="t", role="admin")
        self.store_keeper = User.objects.create_user(username="stores", password="t",
                                                     role="inventory_manager")
        self.pharmacist = User.objects.create_user(username="ph", password="t",
                                                   role="pharmacist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.store = StockLocation.objects.get(code=MAIN_STORE)
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)
        self.analgesics = category("Analgesics")
        self.tablet = unit("Tablet", "tab")

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def create(self, user=None, **fields):
        payload = {"name": "Paracetamol", "strength": "500 mg", "dosage_form": "Tablet",
                   "category": self.analgesics.pk, "unit": self.tablet.pk, "is_active": True,
                   "sku": "PH-PARA-500", "barcode": "6001234500012", "reorder_threshold": 25,
                   **fields}
        return self.client_for(user or self.hospital_admin).post("/api/items/", payload,
                                                                 format="json")

    def audit(self, item, action):
        return AuditLog.objects.filter(action=action, object_id=item.pk)


class CreatingAProductTests(ItemManagementTestCase):
    def test_a_hospital_admin_creates_a_product_with_every_admin_form_field(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        item = Item.objects.get(pk=response.data["id"])
        self.assertEqual(
            (item.name, item.strength, item.dosage_form, item.category, item.unit, item.is_active,
             item.sku, item.barcode, item.reorder_threshold),
            ("Paracetamol", "500 mg", "Tablet", self.analgesics, self.tablet, True,
             "PH-PARA-500", "6001234500012", 25))
        # One catalogue: there is exactly the row Django admin lists.
        self.assertEqual(Item.objects.filter(name="Paracetamol").count(), 1)

    def test_the_optional_fields_are_optional(self):
        response = self.client_for(self.hospital_admin).post(
            "/api/items/", {"name": "Crepe bandage"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        item = Item.objects.get(pk=response.data["id"])
        self.assertEqual((item.sku, item.barcode, item.strength, item.category),
                         (None, None, "", None))
        self.assertEqual(item.reorder_threshold, 10)   # the model's own default

    def test_creating_is_audited(self):
        item = Item.objects.get(pk=self.create().data["id"])
        row = self.audit(item, "inventory.item_created").get()
        self.assertEqual(row.actor, self.hospital_admin)
        self.assertEqual(row.details["source"], "hmis")
        self.assertEqual(row.details["sku"], "PH-PARA-500")

    def test_the_super_admin_keeps_the_same_authority(self):
        self.assertEqual(self.create(self.super_admin).status_code, 201)

    def test_a_created_product_is_immediately_receivable_through_receive_a_delivery(self):
        """
        The Receive a delivery form lists `/api/items/` and posts `/api/batches/`.
        Nothing else is needed for a new product to arrive.
        """
        item_id = self.create().data["id"]
        stores = self.client_for(self.store_keeper)
        listed = stores.get("/api/items/", {"page_size": 500}).data
        self.assertIn(item_id, [row["id"] for row in listed.get("results", listed)])

        response = stores.post("/api/batches/", {
            "item": item_id, "batch_no": "PCM-01", "quantity": 120, "cost_price": "10",
            "sale_price": "20", "supplier": "Emzor",
            "expiry_date": (timezone.localdate() + timedelta(days=365)).isoformat(),
            "location": self.store.pk,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Item.objects.get(pk=item_id).quantity_at(self.store), 120)
        self.assertEqual(Item.objects.filter(name="Paracetamol").count(), 1)


class UniquenessTests(ItemManagementTestCase):
    def test_sku_stays_unique(self):
        self.create()
        response = self.create(name="Other", barcode="999")
        self.assertEqual(response.status_code, 400)
        self.assertIn("sku", response.data)

    def test_barcode_stays_unique(self):
        self.create()
        response = self.create(name="Other", sku="OTHER")
        self.assertEqual(response.status_code, 400)
        self.assertIn("barcode", response.data)

    def test_two_products_without_codes_do_not_collide(self):
        self.assertEqual(self.create(sku="", barcode="").status_code, 201)
        self.assertEqual(self.create(name="Other", sku="", barcode="").status_code, 201)

    def test_an_edit_cannot_take_another_products_code(self):
        self.create()
        other = product("Ibuprofen", sku="PH-IBU")
        response = self.client_for(self.hospital_admin).patch(
            f"/api/items/{other.pk}/", {"sku": "PH-PARA-500"}, format="json")
        self.assertEqual(response.status_code, 400)


class EditingAProductTests(ItemManagementTestCase):
    def setUp(self):
        super().setUp()
        self.item = Item.objects.get(pk=self.create().data["id"])
        self.batch = stock_the_pharmacy(item=self.item, quantity=40, actor=self.store_keeper)

    def test_editing_changes_the_row_and_leaves_its_history_pointing_at_it(self):
        movements = list(StockMovement.objects.values_list("pk", "batch_id", "change"))
        response = self.client_for(self.hospital_admin).patch(
            f"/api/items/{self.item.pk}/",
            {"name": "Paracetamol BP", "strength": "1 g", "reorder_threshold": 50}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.item.refresh_from_db()
        self.assertEqual((self.item.name, self.item.strength, self.item.reorder_threshold),
                         ("Paracetamol BP", "1 g", 50))
        # The same batch, the same stock, the same movements — nothing rewritten.
        self.assertEqual(self.batch.item_id, self.item.pk)
        self.assertEqual(self.item.quantity_at(self.pharmacy), 40)
        self.assertEqual(list(StockMovement.objects.values_list("pk", "batch_id", "change")),
                         movements)

    def test_an_edit_is_audited_with_what_changed(self):
        self.client_for(self.hospital_admin).patch(
            f"/api/items/{self.item.pk}/", {"reorder_threshold": 50}, format="json")
        row = self.audit(self.item, "inventory.item_updated").get()
        self.assertEqual(row.details["changes"], {"reorder_threshold": [25, 50]})

    def test_quantities_are_not_editable_on_a_product(self):
        self.client_for(self.hospital_admin).patch(
            f"/api/items/{self.item.pk}/", {"total_quantity": 9999}, format="json")
        self.assertEqual(self.item.total_quantity, 40)


class ArchivingTests(ItemManagementTestCase):
    def setUp(self):
        super().setUp()
        self.item = Item.objects.get(pk=self.create().data["id"])
        self.batch = stock_the_pharmacy(item=self.item, quantity=40, actor=self.store_keeper)
        self.admin_api = self.client_for(self.hospital_admin)

    def archive(self):
        response = self.admin_api.post(f"/api/items/{self.item.pk}/archive/")
        self.assertEqual(response.status_code, 200, response.data)
        self.item.refresh_from_db()

    def test_archiving_keeps_the_row_and_its_history(self):
        self.archive()
        self.assertFalse(self.item.is_active)
        self.assertTrue(Item.objects.filter(pk=self.item.pk).exists())
        self.assertEqual(self.item.quantity_at(self.pharmacy), 40)
        self.assertTrue(StockMovement.objects.filter(batch=self.batch).exists())
        # Still openable by id, and still on the administration list.
        self.assertEqual(self.admin_api.get(f"/api/items/{self.item.pk}/").status_code, 200)
        listed = self.admin_api.get("/api/items/", {"all": 1}).data
        self.assertIn(self.item.pk, [row["id"] for row in listed.get("results", listed)])

    def test_archiving_and_restoring_are_audited(self):
        self.archive()
        self.assertTrue(self.audit(self.item, "inventory.item_archived").exists())
        self.assertEqual(self.admin_api.post(f"/api/items/{self.item.pk}/restore/").status_code,
                         200)
        self.item.refresh_from_db()
        self.assertTrue(self.item.is_active)
        self.assertTrue(self.audit(self.item, "inventory.item_restored").exists())

    def test_the_generic_toggle_is_the_same_audited_event(self):
        self.admin_api.patch(f"/api/items/{self.item.pk}/", {"is_active": False}, format="json")
        self.assertTrue(self.audit(self.item, "inventory.item_archived").exists())

    def test_archiving_twice_is_refused_readably(self):
        self.archive()
        response = self.admin_api.post(f"/api/items/{self.item.pk}/archive/")
        self.assertEqual((response.status_code, response.data["code"]), (400, "no_change"))

    def test_an_archived_product_is_not_offered_for_new_work(self):
        self.archive()
        for user in (self.store_keeper, self.pharmacist, self.doctor):
            with self.subTest(role=user.role):
                listed = self.client_for(user).get("/api/items/").data
                self.assertNotIn(self.item.pk,
                                 [row["id"] for row in listed.get("results", listed)])

    def test_an_archived_product_cannot_be_received(self):
        self.archive()
        response = self.client_for(self.store_keeper).post("/api/batches/", {
            "item": self.item.pk, "batch_no": "NEW", "quantity": 5, "cost_price": "1",
            "sale_price": "2",
            "expiry_date": (timezone.localdate() + timedelta(days=90)).isoformat(),
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Batch.objects.filter(batch_no="NEW").exists())
        response = self.client_for(self.store_keeper).post(
            f"/api/batches/{self.batch.pk}/receive/", {"quantity": 5}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.item.total_quantity, 40)

    def test_an_archived_product_cannot_be_transferred(self):
        self.archive()
        with self.assertRaises(ValidationError):
            transfer_stock(source=self.pharmacy, destination=self.store,
                           lines=[{"batch": self.batch, "quantity": 5}], actor=self.store_keeper)
        self.assertEqual(self.item.quantity_at(self.pharmacy), 40)

    def test_an_archived_product_cannot_be_newly_prescribed(self):
        self.archive()
        patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                         created_by=self.super_admin)
        with self.assertRaises(ValidationError):
            create_prescription(patient=patient, doctor=self.doctor, item=self.item, quantity=1)
        self.assertFalse(Prescription.objects.exists())

    def test_a_script_written_before_the_archive_is_still_dispensed(self):
        """Archived is 'nothing new'. A script already at the counter is not new."""
        patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                         created_by=self.super_admin)
        prescription = create_prescription(patient=patient, doctor=self.doctor, item=self.item,
                                           quantity=4)
        self.archive()
        dispense_prescription(prescription=pay_for(prescription, by=self.pharmacist), pharmacist=self.pharmacist)
        self.assertEqual(self.item.quantity_at(self.pharmacy), 36)


class DeletingTests(ItemManagementTestCase):
    def test_a_product_nothing_points_at_is_deleted_and_audited(self):
        item = Item.objects.get(pk=self.create().data["id"])
        pk = item.pk
        response = self.client_for(self.hospital_admin).delete(f"/api/items/{pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Item.objects.filter(pk=pk).exists())
        row = AuditLog.objects.get(action="inventory.item_deleted", object_id=pk)
        self.assertEqual(row.details["record"]["sku"], "PH-PARA-500")

    def test_a_product_with_history_is_refused_and_nothing_cascades(self):
        item = Item.objects.get(pk=self.create().data["id"])
        batch = stock_the_store(item=item, quantity=10, actor=self.store_keeper)
        before = (Batch.objects.count(), StockRecord.objects.count(),
                  StockMovement.objects.count())
        admin_api = self.client_for(self.hospital_admin)

        # The screen is told before anybody presses.
        detail = admin_api.get(f"/api/items/{item.pk}/").data
        self.assertFalse(detail["is_deletable"])
        self.assertEqual(detail["references"], {"batches": 1})

        response = admin_api.delete(f"/api/items/{item.pk}/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "in_use")
        self.assertIn("Deactivate it instead", response.data["detail"])
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        self.assertTrue(Batch.objects.filter(pk=batch.pk).exists())
        self.assertEqual((Batch.objects.count(), StockRecord.objects.count(),
                          StockMovement.objects.count()), before)
        self.assertFalse(AuditLog.objects.filter(action="inventory.item_deleted").exists())

    def test_a_product_on_a_prescription_is_refused_too(self):
        item = Item.objects.get(pk=self.create().data["id"])
        stock_the_pharmacy(item=item, quantity=10, actor=self.store_keeper)
        patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                         created_by=self.super_admin)
        create_prescription(patient=patient, doctor=self.doctor, item=item, quantity=1)
        response = self.client_for(self.hospital_admin).delete(f"/api/items/{item.pk}/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("prescription_set", response.data["references"])

    def test_delete_is_not_silently_an_archive(self):
        item = Item.objects.get(pk=self.create().data["id"])
        stock_the_store(item=item, quantity=10, actor=self.store_keeper)
        self.client_for(self.hospital_admin).delete(f"/api/items/{item.pk}/")
        item.refresh_from_db()
        self.assertTrue(item.is_active)


class ThePharmacyCounterTests(ItemManagementTestCase):
    """A pharmacist manages none of the catalogue and loses none of the counter."""

    def test_a_pharmacist_cannot_create_edit_archive_restore_or_delete(self):
        item = Item.objects.get(pk=self.create().data["id"])
        api = self.client_for(self.pharmacist)
        attempts = [
            api.post("/api/items/", {"name": "Smuggled"}, format="json"),
            api.patch(f"/api/items/{item.pk}/", {"name": "Renamed"}, format="json"),
            api.post(f"/api/items/{item.pk}/archive/"),
            api.post(f"/api/items/{item.pk}/restore/"),
            api.delete(f"/api/items/{item.pk}/"),
        ]
        self.assertEqual([r.status_code for r in attempts], [403] * 5)
        item.refresh_from_db()
        self.assertEqual((item.name, item.is_active), ("Paracetamol", True))
        self.assertFalse(Item.objects.filter(name="Smuggled").exists())

    def test_a_pharmacist_still_dispenses_sells_and_takes_money(self):
        item = Item.objects.get(pk=self.create().data["id"])
        stock_the_pharmacy(item=item, quantity=50, actor=self.store_keeper)
        patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                         created_by=self.super_admin)
        prescription = create_prescription(patient=patient, doctor=self.doctor, item=item,
                                           quantity=10)
        api = self.client_for(self.pharmacist)

        # The counter takes the money for this line (rule 58), then hands it over.
        paid = api.post("/api/payments/", {"patient": patient.pk, "amount": "200",
                                           "method": "cash", "charge": prescription.charge_id},
                        format="json")
        self.assertEqual(paid.status_code, 201, paid.data)
        self.assertEqual(paid.data["channel"], "pharmacy")

        dispensed = api.post(f"/api/prescriptions/{prescription.pk}/dispense/")
        self.assertEqual(dispensed.status_code, 200, dispensed.data)

        self.assertEqual(api.post("/api/pos-registers/open/", {"opening_float": "0"},
                                  format="json").status_code, 201)
        sold = api.post("/api/sales/complete/", {
            "lines": [{"item": item.pk, "quantity": 5}], "payment_method": "cash",
            "amount_tendered": "1000"}, format="json")
        self.assertEqual(sold.status_code, 201, sold.data)

        self.assertEqual(item.quantity_at(self.pharmacy), 35)


class DjangoAdminIsTheSameCatalogueTests(ItemManagementTestCase):
    """Django admin's Add item writes the same row and the same audit history."""

    def test_adding_and_archiving_in_django_admin_are_audited(self):
        root = User.objects.create_superuser(username="root", password="t", email="r@x.test",
                                             role="admin")
        self.client.force_login(root)
        response = self.client.post("/admin/inventory/item/add/", {
            "name": "Amoxicillin", "strength": "500 mg", "dosage_form": "Capsule",
            "category": self.analgesics.pk, "unit": self.tablet.pk, "is_active": "on",
            "sku": "PH-AMOX", "barcode": "", "reorder_threshold": "15",
        })
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(sku="PH-AMOX")
        self.assertEqual(self.audit(item, "inventory.item_created").get().details["source"],
                         "django-admin")

        # The HMIS list shows it at once — one catalogue.
        listed = self.client_for(self.hospital_admin).get("/api/items/", {"all": 1}).data
        self.assertIn(item.pk, [row["id"] for row in listed.get("results", listed)])

        self.client.post("/admin/inventory/item/", {
            "action": "deactivate", "_selected_action": [item.pk]})
        item.refresh_from_db()
        self.assertFalse(item.is_active)
        self.assertTrue(self.audit(item, "inventory.item_archived").exists())
