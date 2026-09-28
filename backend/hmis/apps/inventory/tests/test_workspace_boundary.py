"""
Administration configures inventory; Pharmacy operates pharmacy stock.

The navigation draws that line, but navigation is not a control — a
pharmacist who types `/api/items/` into a client has bypassed it. So the
boundary is enforced here, and this file is what holds it:

* **configuration** — products, categories, units, locations — is read by
  everyone who works stock and written only by an admin;
* **operations that change stock** — receiving, transferring, posting or
  applying a count, writing off, marking a batch expired — are inventory
  administration (STOCK_CONTROL_ROLES: inventory manager + both admins);
* **the counter** — dispensing, the POS, reading the shelf and counting it
  (the sheet, the CSV export and its preview) — stays the pharmacist's;
* and the catalogue an admin configures is available to the pharmacy
  immediately, without the pharmacy needing the configuration screen at all.

The last one is the point of keeping one shared inventory rather than giving
each workspace its own.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.pharmacy.testing import pay_for
from apps.accounts.models import User
from apps.inventory.models import (
    MAIN_STORE, PHARMACY, Batch, Item, ItemCategory, StockLocation, StockMovement, StockRecord,
    UnitOfMeasure,
)
from apps.inventory.services import receive_stock
from apps.inventory.testing import product
from apps.patients.models import Patient
from apps.pharmacy.services import create_prescription, dispense_prescription

# The three configuration endpoints the pharmacy workspace no longer links to.
CONFIG_ENDPOINTS = ["/api/items/", "/api/item-categories/", "/api/units/",
                    "/api/stock-locations/"]


class WorkspaceBoundaryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.pharmacist = User.objects.create_user(username="pharm", password="t",
                                                   role="pharmacist")
        self.store_keeper = User.objects.create_user(username="stores", password="t",
                                                     role="inventory_manager")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")

        self.store = StockLocation.objects.get(code=MAIN_STORE)
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)
        self.category = ItemCategory.objects.create(name="Analgesics")
        self.unit = UnitOfMeasure.objects.create(name="Tablet", abbreviation="tab")

        self.admin_client = self._client(self.admin)
        self.pharmacy_client = self._client(self.pharmacist)
        self.store_client = self._client(self.store_keeper)

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client


class ConfigurationIsAdministrationsTests(WorkspaceBoundaryTests):
    """A pharmacist cannot configure the catalogue by calling the API."""

    def test_a_pharmacist_cannot_create_a_product_category_or_unit(self):
        attempts = [
            ("/api/items/", {"name": "Smuggled drug"}),
            ("/api/item-categories/", {"name": "Smuggled category"}),
            ("/api/units/", {"name": "Smuggled unit"}),
            ("/api/stock-locations/", {"code": "smuggled", "name": "Smuggled store"}),
        ]
        for endpoint, payload in attempts:
            with self.subTest(endpoint=endpoint):
                response = self.pharmacy_client.post(endpoint, payload, format="json")
                self.assertEqual(response.status_code, 403, response.data)

        self.assertFalse(Item.objects.filter(name="Smuggled drug").exists())
        self.assertFalse(ItemCategory.objects.filter(name="Smuggled category").exists())
        self.assertFalse(UnitOfMeasure.objects.filter(name="Smuggled unit").exists())

    def test_a_pharmacist_cannot_edit_or_retire_a_product(self):
        item = product("Paracetamol", unit_name="Tablet")
        self.assertEqual(
            self.pharmacy_client.patch(f"/api/items/{item.pk}/",
                                       {"name": "Renamed"}, format="json").status_code, 403)
        self.assertEqual(
            self.pharmacy_client.patch(f"/api/items/{item.pk}/",
                                       {"is_active": False}, format="json").status_code, 403)
        self.assertEqual(self.pharmacy_client.delete(f"/api/items/{item.pk}/").status_code, 403)

        item.refresh_from_db()
        self.assertEqual(item.name, "Paracetamol")
        self.assertTrue(item.is_active)

    def test_the_store_keeper_cannot_either(self):
        """
        Configuration is Administration's, not "anyone near stock". The store
        keeper runs the store; what a product *is* is set up for them.
        """
        response = self.store_client.post("/api/item-categories/", {"name": "Mine"},
                                          format="json")
        self.assertEqual(response.status_code, 403)

    def test_an_admin_configures_all_of_it(self):
        created = self.admin_client.post("/api/items/", {
            "name": "Paracetamol 500mg", "category": self.category.pk, "unit": self.unit.pk,
        }, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        # "Oncology" rather than "Antibiotics": the seeded catalogue
        # (`inventory/0008`) already has the latter, and this case is about who
        # may configure, not about which names exist.
        for endpoint, payload in [("/api/item-categories/", {"name": "Oncology"}),
                                  ("/api/units/", {"name": "Bottle"}),
                                  ("/api/stock-locations/", {"code": "theatre",
                                                             "name": "Theatre Store"})]:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.admin_client.post(endpoint, payload, format="json").status_code, 201)

    def test_the_pharmacy_still_reads_everything_it_dispenses_against(self):
        """
        Reads stay wide. Every dispense resolves a product, a unit and a
        price, so shutting the pharmacy out of the catalogue would stop the
        counter working — the point is that it cannot *change* it.
        """
        for endpoint in CONFIG_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.pharmacy_client.get(endpoint).status_code, 200)


class _StockedTests(WorkspaceBoundaryTests):
    def setUp(self):
        super().setUp()
        self.hospital_admin = User.objects.create_user(username="ha", password="t",
                                                       role="hospital_admin")
        self.hospital_admin_client = self._client(self.hospital_admin)
        self.item = product("Paracetamol", unit_name="Tablet")
        self.batch = Batch.objects.create(
            item=self.item, batch_no="PCM001", cost_price=Decimal("10"),
            sale_price=Decimal("20"),
            expiry_date=timezone.localdate() + timedelta(days=180))
        receive_stock(batch=self.batch, quantity=500, actor=self.store_keeper)

    def _held(self, location):
        record = StockRecord.objects.filter(batch=self.batch, location=location).first()
        return record.quantity if record else 0

    def _stock_the_counter(self, quantity=100):
        response = self.store_client.post("/api/stock-transfers/", {
            "source": self.store.pk, "destination": self.pharmacy.pk,
            "lines": [{"batch": self.batch.pk, "quantity": quantity}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)


class InventoryAdministrationIsNotCounterWorkTests(_StockedTests):
    """
    A pharmacist works the counter: dispensing, the POS, taking money, and
    reading and counting the shelf. Changing stock outside those —
    receiving, transferring, posting or applying a count, writing off, marking
    a batch expired, editing a lot — is inventory administration
    (STOCK_CONTROL_ROLES), and a Pharmacy posting does not make anybody that.
    """

    def test_a_pharmacist_cannot_change_stock_by_calling_the_api(self):
        self._stock_the_counter()
        movements = StockMovement.objects.count()
        expired = Batch.objects.create(
            item=self.item, batch_no="OLD", cost_price=Decimal("10"), sale_price=Decimal("20"),
            expiry_date=timezone.localdate() - timedelta(days=3))
        attempts = [
            ("post", "/api/stock-transfers/", {
                "source": self.store.pk, "destination": self.pharmacy.pk,
                "lines": [{"batch": self.batch.pk, "quantity": 10}]}),
            ("post", "/api/stock-counts/", {
                "location": self.pharmacy.pk,
                "lines": [{"batch": self.batch.pk, "counted_quantity": 97}]}),
            ("post", "/api/batches/", {
                "item": self.item.pk, "batch_no": "NEW", "quantity": 10, "cost_price": "1",
                "sale_price": "2",
                "expiry_date": (timezone.localdate() + timedelta(days=90)).isoformat()}),
            ("post", f"/api/batches/{self.batch.pk}/receive/", {"quantity": 10}),
            ("post", f"/api/batches/{self.batch.pk}/count/",
             {"location": self.pharmacy.pk, "counted_quantity": 1}),
            ("post", f"/api/batches/{expired.pk}/write_off/", {}),
            ("post", f"/api/batches/{self.batch.pk}/mark-expired/", {"reason": "Damaged"}),
            ("patch", f"/api/batches/{self.batch.pk}/", {"expiry_date": "2030-01-01"}),
            ("delete", f"/api/batches/{self.batch.pk}/", {}),
        ]
        for method, endpoint, payload in attempts:
            with self.subTest(endpoint=endpoint, method=method):
                response = getattr(self.pharmacy_client, method)(endpoint, payload,
                                                                 format="json")
                self.assertEqual(response.status_code, 403, response.data)

        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual((self._held(self.store), self._held(self.pharmacy)), (400, 100))
        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.marked_expired_at)
        self.assertFalse(Batch.objects.filter(batch_no="NEW").exists())

    def test_a_pharmacist_still_reads_the_shelf_the_ledger_and_the_count_sheet(self):
        for endpoint in ["/api/stock-records/", "/api/stock-movements/", "/api/batches/",
                         "/api/stock-transfers/", "/api/stock-counts/",
                         f"/api/stock-counts/sheet/?location={self.pharmacy.pk}",
                         f"/api/stock-counts/export/?location={self.pharmacy.pk}",
                         "/api/stock-count-imports/"]:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.pharmacy_client.get(endpoint).status_code, 200)

    def test_the_expired_items_register_is_inventory_administrations(self):
        self.assertEqual(self.pharmacy_client.get("/api/stock-records/expired/").status_code, 403)
        self.assertEqual(self.store_client.get("/api/stock-records/expired/").status_code, 200)
        self.assertEqual(
            self.hospital_admin_client.get("/api/stock-records/expired/").status_code, 200)

    def test_a_pharmacy_department_posting_grants_no_inventory_authority(self):
        """Role and department: the department grants nothing on its own."""
        from apps.departments.models import Department
        pharmacy_department, _ = Department.objects.get_or_create(
            code="pharmacy", defaults={"name": "Pharmacy"})
        pharmacy_department.staff.add(self.pharmacist)
        response = self.pharmacy_client.post("/api/stock-transfers/", {
            "source": self.store.pk, "destination": self.pharmacy.pk,
            "lines": [{"batch": self.batch.pk, "quantity": 10}]}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.pharmacy_client.post("/api/items/", {"name": "X"}, format="json").status_code,
            403)


class OperationsStayWithInventoryAdministrationTests(_StockedTests):
    """The operations still work — for the people who administer stock."""

    def test_the_store_keeper_and_the_hospital_admin_transfer_stock(self):
        self._stock_the_counter(100)
        response = self.hospital_admin_client.post("/api/stock-transfers/", {
            "source": self.store.pk, "destination": self.pharmacy.pk,
            "lines": [{"batch": self.batch.pk, "quantity": 50}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((self._held(self.store), self._held(self.pharmacy)), (350, 150))

    def test_a_count_of_the_pharmacy_shelf_is_posted_by_inventory_administration(self):
        self._stock_the_counter()
        response = self.store_client.post("/api/stock-counts/", {
            "location": self.pharmacy.pk,
            "lines": [{"batch": self.batch.pk, "counted_quantity": 97}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self._held(self.pharmacy), 97)
        self.assertEqual(self._held(self.store), 400)   # untouched

    def test_the_hospital_admin_receives_a_delivery_and_writes_off_expired_stock(self):
        response = self.hospital_admin_client.post(f"/api/batches/{self.batch.pk}/receive/",
                                                   {"quantity": 10}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        Batch.objects.filter(pk=self.batch.pk).update(
            expiry_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(
            self.hospital_admin_client.post(f"/api/batches/{self.batch.pk}/write_off/")
            .status_code, 200)

    def test_dispensing_is_untouched(self):
        patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                         created_by=self.admin)
        self._stock_the_counter()

        prescription = create_prescription(patient=patient, doctor=self.doctor,
                                           item=self.item, quantity=10)
        dispense_prescription(prescription=pay_for(prescription, by=self.pharmacist), pharmacist=self.pharmacist)

        self.assertEqual(self._held(self.pharmacy), 90)
        self.assertEqual(self._held(self.store), 400)


class AdminConfiguresAndPharmacyConsumesTests(WorkspaceBoundaryTests):
    """
    The whole reason the inventory stays shared: what Administration sets up
    is what the pharmacy dispenses, with no second catalogue and no wait.
    """

    def test_a_product_created_by_admin_is_dispensable_the_same_moment(self):
        created = self.admin_client.post("/api/items/", {
            "name": "Paracetamol 500mg", "category": self.category.pk, "unit": self.unit.pk,
        }, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        item = Item.objects.get(pk=created.data["id"])

        # The pharmacy sees it, with the category and unit the admin chose.
        listed = self.pharmacy_client.get("/api/items/", {"search": "Paracetamol"}).data
        rows = listed.get("results", listed)
        self.assertEqual(rows[0]["name"], "Paracetamol 500mg")
        self.assertEqual(rows[0]["category_name"], "Analgesics")
        self.assertEqual(rows[0]["unit_label"], "tab")

        # And the whole flow runs against it, with no configuration access.
        batch = Batch.objects.create(item=item, batch_no="NEW-1", cost_price=Decimal("10"),
                                     sale_price=Decimal("20"),
                                     expiry_date=timezone.localdate() + timedelta(days=90))
        receive_stock(batch=batch, quantity=50, actor=self.store_keeper)
        self.store_client.post("/api/stock-transfers/", {
            "source": self.store.pk, "destination": self.pharmacy.pk,
            "lines": [{"batch": batch.pk, "quantity": 20}],
        }, format="json")

        patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                         created_by=self.admin)
        prescription = create_prescription(patient=patient, doctor=self.doctor,
                                           item=item, quantity=5)
        dispense_prescription(prescription=pay_for(prescription, by=self.pharmacist), pharmacist=self.pharmacist)

        record = StockRecord.objects.get(batch=batch, location=self.pharmacy)
        self.assertEqual(record.quantity, 15)

    def test_a_product_retired_by_admin_stops_being_offered_but_keeps_its_history(self):
        item = product("Old drug", unit_name="Tablet")
        batch = Batch.objects.create(item=item, batch_no="OLD-1", cost_price=Decimal("10"),
                                     sale_price=Decimal("20"),
                                     expiry_date=timezone.localdate() + timedelta(days=90))
        receive_stock(batch=batch, quantity=5, actor=self.store_keeper)

        self.assertEqual(
            self.admin_client.patch(f"/api/items/{item.pk}/", {"is_active": False},
                                    format="json").status_code, 200)

        # Still on every record that names it — the pharmacy can still read
        # what it dispensed last year.
        response = self.pharmacy_client.get(f"/api/items/{item.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_active"])
        self.assertEqual(response.data["total_quantity"], 5)
