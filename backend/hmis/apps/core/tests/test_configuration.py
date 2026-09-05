"""
One set of models, two administration interfaces.

The HMIS administration screens and Django admin are two front doors onto the
same rows. This file is the proof — for every major configuration entity it
writes through one door and reads through the other, in both directions:

    HMIS API → ORM (what Django admin renders)
    ORM      → HMIS API

If either interface ever grew its own copy of the configuration, one of these
assertions is what would fail.

It also holds the two safety rules that must be the same on both sides:
**delete what was never used, deactivate what history points at**, and
**stock quantities never move without a movement** — including from Django
admin, which is otherwise allowed to edit almost anything.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.contrib.admin.sites import site as admin_site
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import BillingItem
from apps.core.models import HospitalSettings, Notification, NotificationSetting
from apps.core.services import notify
from apps.departments.models import Department, Service
from apps.inpatient.models import Bed, Ward
from apps.inventory.models import (
    PHARMACY, Batch, Item, ItemCategory, StockLocation, StockRecord, UnitOfMeasure,
)
from apps.inventory.services import receive_stock
from apps.inventory.testing import product
from apps.laboratory.models import LabTest
from apps.patients.models import Patient


class ConfigurationTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="t", role="admin",
                                              is_staff=True, is_superuser=True)
        self.pharmacist = User.objects.create_user(username="pharm", password="t",
                                                   role="pharmacist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)


class HmisWritesReachDjangoAdmin(ConfigurationTestCase):
    """Create through the API; read the row the ORM (and so Django admin) sees."""

    def test_a_department_created_in_the_hmis_is_the_same_row(self):
        response = self.client.post("/api/departments/",
                                    {"name": "Physiotherapy", "code": "physio"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)

        department = Department.objects.get(code="physio")
        self.assertEqual(department.name, "Physiotherapy")
        self.assertTrue(department.is_active)
        self.assertEqual(department.pk, response.data["id"])

    def test_a_product_created_in_the_hmis_is_the_same_row(self):
        category = ItemCategory.objects.create(name="Analgesics")
        unit = UnitOfMeasure.objects.create(name="Tablet", abbreviation="tab")

        response = self.client.post("/api/items/", {
            "name": "Paracetamol 500mg", "category": category.id, "unit": unit.id,
            "reorder_threshold": 25,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)

        item = Item.objects.get(name="Paracetamol 500mg")
        self.assertEqual(item.category, category)
        self.assertEqual(item.unit, unit)
        self.assertEqual(item.reorder_threshold, 25)
        self.assertEqual(item.unit_label, "tab")

    def test_categories_units_services_and_locations_all_land_as_rows(self):
        department = Department.objects.create(name="Radiology", code="radiology")
        cases = [
            ("/api/item-categories/", {"name": "Antibiotics"}, ItemCategory, "name", "Antibiotics"),
            ("/api/units/", {"name": "Bottle", "abbreviation": "btl"},
             UnitOfMeasure, "name", "Bottle"),
            ("/api/services/", {"name": "Chest X-ray", "code": "cxr",
                                "department": department.id, "price": "5000"},
             Service, "code", "cxr"),
            ("/api/billing-items/", {"name": "Registration card", "category": "card",
                                     "price": "1500"}, BillingItem, "name", "Registration card"),
            ("/api/wards/", {"name": "Maternity Ward"}, Ward, "name", "Maternity Ward"),
            ("/api/stock-locations/", {"code": "theatre", "name": "Theatre Store",
                                       "kind": "store"}, StockLocation, "code", "theatre"),
        ]
        for endpoint, payload, model, field, value in cases:
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, payload, format="json")
                self.assertEqual(response.status_code, 201, response.data)
                row = model.objects.get(**{field: value})
                self.assertEqual(row.pk, response.data["id"])

    def test_a_bed_created_in_the_hmis_hangs_off_its_ward(self):
        ward = Ward.objects.create(name="Male Ward")
        response = self.client.post("/api/beds/", {"ward": ward.id, "number": "M-01"},
                                    format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Bed.objects.get(number="M-01").ward, ward)


class DjangoAdminWritesReachTheHmis(ConfigurationTestCase):
    """Change the row directly — as Django admin does — and read the API."""

    def test_a_department_renamed_in_django_admin_shows_in_the_hmis(self):
        department = Department.objects.create(name="Physio", code="physio")
        department.name = "Physiotherapy & Rehabilitation"
        department.save()

        listed = self.client.get("/api/departments/").data
        rows = listed.get("results", listed)
        self.assertIn("Physiotherapy & Rehabilitation", [d["name"] for d in rows])

    def test_a_product_deactivated_in_django_admin_shows_as_inactive_in_the_hmis(self):
        item = product("Amoxicillin 250mg", unit_name="capsule", category_name="Antibiotics")
        Item.objects.filter(pk=item.pk).update(is_active=False)

        response = self.client.get(f"/api/items/{item.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_active"])
        self.assertEqual(response.data["category_name"], "Antibiotics")
        self.assertEqual(response.data["unit_name"], "capsule")

    def test_a_category_renamed_in_django_admin_renames_it_everywhere(self):
        """
        The point of the relation: the name lives in one row, so a rename
        reaches every product at once instead of needing a bulk edit.
        """
        item = product("Ibuprofen", category_name="Painkillers")
        ItemCategory.objects.filter(name="Painkillers").update(name="Analgesics")

        response = self.client.get(f"/api/items/{item.pk}/")
        self.assertEqual(response.data["category_name"], "Analgesics")

    def test_a_unit_abbreviation_set_in_django_admin_reaches_the_labels(self):
        item = product("Metronidazole", unit_name="Tablet")
        UnitOfMeasure.objects.filter(name="Tablet").update(abbreviation="tab")

        item.refresh_from_db()
        self.assertEqual(item.unit_label, "tab")
        # And it is what a prescription row prints.
        doctor_view = self.client.get("/api/items/", {"search": "Metronidazole"}).data
        rows = doctor_view.get("results", doctor_view)
        self.assertEqual(rows[0]["unit_label"], "tab")

    def test_a_service_repriced_in_django_admin_shows_in_the_hmis(self):
        department = Department.objects.create(name="Physio", code="physio")
        service = Service.objects.create(department=department, name="Session",
                                         code="session", price=Decimal("3000"))
        Service.objects.filter(pk=service.pk).update(price=Decimal("4500"))

        response = self.client.get(f"/api/services/{service.pk}/")
        self.assertEqual(Decimal(response.data["price"]), Decimal("4500"))

    def test_a_ward_and_bed_added_in_django_admin_appear_on_the_bed_board(self):
        ward = Ward.objects.create(name="Postnatal Ward")
        Bed.objects.create(ward=ward, number="P-07")

        beds = self.client.get("/api/beds/", {"ward": ward.pk}).data
        rows = beds.get("results", beds)
        self.assertEqual([b["number"] for b in rows], ["P-07"])


class DeleteVersusDeactivate(ConfigurationTestCase):
    """
    The same answer from both interfaces: unused rows go, used ones are
    retired. The API refuses with 409; Django admin hides the delete button.
    """

    def test_an_unused_category_can_simply_be_deleted(self):
        category = ItemCategory.objects.create(name="Typo")
        response = self.client.delete(f"/api/item-categories/{category.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(ItemCategory.objects.filter(pk=category.pk).exists())

    def test_a_category_in_use_is_refused_and_told_to_deactivate(self):
        item = product("Paracetamol", category_name="Analgesics")
        category = item.category

        response = self.client.delete(f"/api/item-categories/{category.pk}/")
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["code"], "in_use")
        self.assertIn("Deactivate", response.data["detail"])
        self.assertTrue(ItemCategory.objects.filter(pk=category.pk).exists())

        # And the way out works.
        self.assertEqual(
            self.client.patch(f"/api/item-categories/{category.pk}/",
                              {"is_active": False}, format="json").status_code, 200)
        category.refresh_from_db()
        self.assertFalse(category.is_active)

    def test_a_product_with_stock_cannot_be_deleted(self):
        item = product("Paracetamol")
        batch = Batch.objects.create(item=item, batch_no="B1", cost_price=Decimal("10"),
                                     sale_price=Decimal("20"),
                                     expiry_date=timezone.localdate() + timedelta(days=90))
        receive_stock(batch=batch, quantity=10, actor=self.admin)

        response = self.client.delete(f"/api/items/{item.pk}/")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())

    def test_a_bed_somebody_has_occupied_cannot_be_deleted(self):
        from apps.inpatient.models import Admission

        ward = Ward.objects.create(name="Ward A")
        bed = Bed.objects.create(ward=ward, number="A-1")
        patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                         created_by=self.admin)
        Admission.objects.create(patient=patient, bed=bed, admitted_by=self.admin)

        self.assertEqual(self.client.delete(f"/api/beds/{bed.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/wards/{ward.pk}/").status_code, 409)

    def test_a_location_holding_stock_cannot_be_deleted(self):
        item = product("Paracetamol")
        batch = Batch.objects.create(item=item, batch_no="B1", cost_price=Decimal("10"),
                                     sale_price=Decimal("20"),
                                     expiry_date=timezone.localdate() + timedelta(days=90))
        receive_stock(batch=batch, quantity=5, actor=self.admin)
        store = StockLocation.objects.get(is_default_receiving=True)

        self.assertEqual(self.client.delete(f"/api/stock-locations/{store.pk}/").status_code, 409)

    def test_django_admin_hides_delete_for_the_same_rows(self):
        """
        Not a different rule in the other interface: `ProtectedConfigAdmin`
        reads the same relations, so the button is gone where the API says no.
        """
        used = product("Paracetamol", category_name="Analgesics").category
        unused = ItemCategory.objects.create(name="Typo")
        model_admin = admin_site._registry[ItemCategory]

        request = type("R", (), {"user": self.admin, "method": "GET"})()
        self.assertFalse(model_admin.has_delete_permission(request, used))
        self.assertTrue(model_admin.has_delete_permission(request, unused))


class StockStaysBehindTheLedgerInDjangoAdmin(ConfigurationTestCase):
    """
    The inventory warning: Django admin must not be a way to type
    `quantity = 500` and have the balance change.
    """

    def setUp(self):
        super().setUp()
        self.item = product("Paracetamol")
        self.batch = Batch.objects.create(
            item=self.item, batch_no="B1", cost_price=Decimal("10"), sale_price=Decimal("20"),
            expiry_date=timezone.localdate() + timedelta(days=90))
        receive_stock(batch=self.batch, quantity=100, actor=self.admin)
        self.record = StockRecord.objects.get(batch=self.batch)

    def test_the_stock_record_admin_is_read_only(self):
        model_admin = admin_site._registry[StockRecord]
        request = type("R", (), {"user": self.admin, "method": "GET"})()
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request, self.record))

    def test_saving_a_quantity_from_anywhere_else_is_refused(self):
        """Even from a shell or a form: the model itself is the last line."""
        self.record.quantity = 500
        with self.assertRaises(PermissionDenied):
            self.record.save()
        self.record.refresh_from_db()
        self.assertEqual(self.record.quantity, 100)

    def test_the_batch_admin_offers_no_quantity_field(self):
        model_admin = admin_site._registry[Batch]
        editable = {field for group in model_admin.fieldsets for field in group[1]["fields"]}
        self.assertNotIn("quantity", editable)
        self.assertNotIn("total_quantity", editable)


class HospitalSettingsTests(ConfigurationTestCase):
    def test_the_settings_row_is_a_singleton(self):
        first = HospitalSettings.load()
        second = HospitalSettings.load()
        self.assertEqual(first.pk, second.pk)

        HospitalSettings.objects.create(name="Second hospital")
        self.assertEqual(HospitalSettings.objects.count(), 1)

    def test_everyone_reads_the_settings_and_only_an_admin_writes_them(self):
        reader = APIClient()
        reader.force_authenticate(self.doctor)
        self.assertEqual(reader.get("/api/hospital-settings/current/").status_code, 200)
        self.assertEqual(
            reader.patch("/api/hospital-settings/current/", {"name": "Nope"},
                         format="json").status_code, 403)

        response = self.client.patch("/api/hospital-settings/current/",
                                     {"name": "NMHS", "expiry_warning_days": 45},
                                     format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(HospitalSettings.load().expiry_warning_days, 45)

    def test_the_expiry_threshold_actually_drives_the_stock_alert(self):
        """A setting that changes no behaviour would be a lie on the form."""
        from apps.workflow.views import DashboardView

        item = product("Paracetamol")
        batch = Batch.objects.create(item=item, batch_no="B1", cost_price=Decimal("10"),
                                     sale_price=Decimal("20"),
                                     expiry_date=timezone.localdate() + timedelta(days=40))
        receive_stock(batch=batch, quantity=5, actor=self.admin)

        inventory_manager = User.objects.create_user(username="stores", password="t",
                                                     role="inventory_manager")
        alerts = DashboardView()._alerts_for(inventory_manager, False)
        self.assertFalse([a for a in alerts if "expire" in a["label"]])

        settings_row = HospitalSettings.load()
        settings_row.expiry_warning_days = 60
        settings_row.save()

        alerts = DashboardView()._alerts_for(inventory_manager, False)
        expiring = [a for a in alerts if "expire" in a["label"]]
        self.assertEqual(len(expiring), 1)
        self.assertIn("60 days", expiring[0]["label"])

    def test_a_nonsense_threshold_is_refused(self):
        response = self.client.patch("/api/hospital-settings/current/",
                                     {"expiry_warning_days": 0}, format="json")
        self.assertEqual(response.status_code, 400)


class NotificationSettingsTests(ConfigurationTestCase):
    def test_every_category_the_application_raises_has_a_row(self):
        self.assertEqual(
            set(NotificationSetting.objects.values_list("category", flat=True)),
            {"clinical", "routing", "pharmacy", "billing", "general"})
        self.assertTrue(all(NotificationSetting.objects.values_list("is_enabled", flat=True)))

    def test_switching_a_category_off_actually_stops_those_notifications(self):
        NotificationSetting.objects.filter(category="billing").update(is_enabled=False)

        self.assertIsNone(notify(recipient=self.doctor, title="A charge", category="billing"))
        self.assertFalse(Notification.objects.filter(category="billing").exists())
        # And everything else still goes.
        notify(recipient=self.doctor, title="A referral", category="routing")
        self.assertTrue(Notification.objects.filter(category="routing").exists())

    def test_clinical_notifications_cannot_be_switched_off(self):
        clinical = NotificationSetting.objects.get(category="clinical")
        response = self.client.patch(f"/api/notification-settings/{clinical.pk}/",
                                     {"is_enabled": False}, format="json")
        self.assertEqual(response.status_code, 400, response.data)

        # Even forced in the database, `notify()` ignores it: a result has to
        # reach the clinician who ordered it.
        NotificationSetting.objects.filter(category="clinical").update(is_enabled=False)
        notify(recipient=self.doctor, title="Lab result", category="clinical")
        self.assertTrue(Notification.objects.filter(category="clinical").exists())

    def test_an_unknown_category_still_delivers(self):
        """The default has to be "send it", or a new kind of notification
        would go quiet until somebody remembered to add a row."""
        notify(recipient=self.doctor, title="Something new", category="handover")
        self.assertTrue(Notification.objects.filter(category="handover").exists())

    def test_only_an_admin_changes_them(self):
        reader = APIClient()
        reader.force_authenticate(self.pharmacist)
        setting = NotificationSetting.objects.get(category="pharmacy")
        self.assertEqual(reader.get("/api/notification-settings/").status_code, 200)
        self.assertEqual(
            reader.patch(f"/api/notification-settings/{setting.pk}/",
                         {"is_enabled": False}, format="json").status_code, 403)


class LabCatalogueIsTheSameRows(ConfigurationTestCase):
    def test_a_test_added_in_the_hmis_is_the_row_django_admin_shows(self):
        response = self.client.post("/api/lab-tests/", {
            "code": "esr-manual", "name": "ESR (manual)", "category": "haematology",
            "price": "1500",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(LabTest.objects.get(code="esr-manual").name, "ESR (manual)")

    def test_a_price_changed_in_django_admin_shows_in_the_hmis(self):
        test = LabTest.objects.get(code="fbc")
        LabTest.objects.filter(pk=test.pk).update(price=Decimal("4500"))
        response = self.client.get(f"/api/lab-tests/{test.pk}/")
        self.assertEqual(Decimal(response.data["price"]), Decimal("4500"))
