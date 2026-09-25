"""
Maternity's price list — **configuration, not a billing path.**

The ward had no billable catalogue until the hospital configured one. What
makes it appear is a category on `BillingItem` and seven ordinary rows under
it; what bills it is the counter's existing `bill-services`, `add_charge`,
ledger, payments, deferrals, discounts, waivers, refunds and reports. No
maternity billing code exists and these tests fail by name if any appears.

The one architectural consequence is that Maternity joined
`REVENUE_DEPARTMENTS`. It was left out while the ward raised no charges of its
own; the registry is the subset of departments that takes money, and the ward
now takes money.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import catalogue
from apps.billing.departments import REVENUE_DEPARTMENTS, department_for_source
from apps.billing.models import BillingItem, Charge
from apps.departments.models import Department
from apps.patients.models import Patient


class MaternityPriceList(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t",
                                                  role="reception")
        self.cashier = User.objects.create_user(username="till", password="t",
                                                role="cashier")
        self.mother = Patient.objects.create(first_name="Dora", last_name="Williams",
                                             sex="F", created_by=self.reception)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def offered(self, user=None):
        answer = self.api(user or self.reception).get("/api/billable-services/",
                                                      {"category": "maternity"})
        rows = answer.data
        return rows["results"] if isinstance(rows, dict) and "results" in rows else rows


class TheServicesAreConfigured(MaternityPriceList):

    def test_the_seed_put_them_in_the_catalogue(self):
        names = set(BillingItem.objects.filter(category="maternity")
                    .values_list("name", flat=True))
        for expected in ("ANC Booking Visit", "ANC Follow-up Visit",
                         "Obstetric Ultrasound", "Delivery Package — Normal",
                         "Delivery Package — Caesarean Section", "Postnatal Visit",
                         "Newborn Care"):
            self.assertIn(expected, names)

    def test_they_are_ordinary_billing_items(self):
        """Not a maternity price model — the row the counter already bills."""
        package = BillingItem.objects.get(name="Delivery Package — Normal")
        self.assertEqual(package.category, "maternity")
        self.assertEqual(package.price, Decimal("45000.00"))
        self.assertTrue(package.is_active)

    def test_maternity_is_a_category_the_counter_knows(self):
        self.assertIn(("maternity", "Maternity"), BillingItem.CATEGORY)
        self.assertEqual(catalogue.CATEGORY_LABELS["maternity"], "Maternity")

    def test_nothing_was_duplicated(self):
        """Names are unique, and the seed adopts rather than adding beside."""
        for name in BillingItem.objects.filter(category="maternity").values_list(
                "name", flat=True):
            self.assertEqual(BillingItem.objects.filter(name=name).count(), 1)

    def test_the_maternity_department_was_not_duplicated_either(self):
        self.assertEqual(Department.objects.filter(code="maternity").count(), 1)


class TheyAppearInBilling(MaternityPriceList):

    def test_the_counter_is_offered_them(self):
        names = {row["name"] for row in self.offered()}
        self.assertIn("Delivery Package — Normal", names)

    def test_each_row_carries_the_price_and_the_grouping(self):
        package = next(r for r in self.offered()
                       if r["name"] == "Delivery Package — Normal")
        self.assertEqual(package["category"], "maternity")
        self.assertEqual(package["category_label"], "Maternity")
        self.assertEqual(Decimal(package["price"]), Decimal("45000.00"))
        # `source_type` is what the charge is posted with — the category, as
        # it is for every other row in this window.
        self.assertEqual(package["source_type"], "maternity")

    def test_the_cash_desk_is_offered_them_too(self):
        self.assertTrue({row["name"] for row in self.offered(self.cashier)})

    def test_a_retired_service_drops_off_the_picker(self):
        package = BillingItem.objects.get(name="Newborn Care")
        package.is_active = False
        package.save(update_fields=["is_active"])
        self.assertNotIn("Newborn Care", {row["name"] for row in self.offered()})

    def test_the_price_is_the_catalogue_row_not_a_copy(self):
        package = BillingItem.objects.get(name="Postnatal Visit")
        package.price = Decimal("3200.00")
        package.save(update_fields=["price"])
        row = next(r for r in self.offered() if r["name"] == "Postnatal Visit")
        self.assertEqual(Decimal(row["price"]), Decimal("3200.00"))


class AMaternityChargeIsAnOrdinaryCharge(MaternityPriceList):

    def bill(self, name="Delivery Package — Normal"):
        row = next(r for r in self.offered() if r["name"] == name)
        answer = self.api(self.reception).post(
            "/api/charges/bill-services/",
            {"patient": self.mother.pk, "services": [row["key"]]}, format="json")
        self.assertEqual(answer.status_code, 201, answer.data)
        return Charge.objects.filter(patient=self.mother).latest("id")

    def test_reception_bills_it_and_the_charge_is_raised(self):
        charge = self.bill()
        self.assertEqual(charge.description, "Delivery Package — Normal")
        self.assertEqual(charge.amount, Decimal("45000.00"))
        self.assertEqual(charge.source_type, "maternity")
        self.assertEqual(charge.status, "unpaid")

    def test_the_server_prices_it_and_never_the_client(self):
        """Rule 52 — a request carries identities, never money."""
        row = next(r for r in self.offered() if r["name"] == "Delivery Package — Normal")
        self.api(self.reception).post(
            "/api/charges/bill-services/",
            {"patient": self.mother.pk, "services": [row["key"]], "amount": "5.00"},
            format="json")
        self.assertEqual(Charge.objects.filter(patient=self.mother).latest("id").amount,
                         Decimal("45000.00"))

    def test_it_lands_on_the_patients_ledger(self):
        self.bill()
        ledger = self.api(self.cashier).get("/api/ledgers/",
                                            {"patient": self.mother.pk}).data
        rows = ledger["results"] if isinstance(ledger, dict) and "results" in ledger else ledger
        self.assertTrue(rows)

    def test_it_takes_a_payment_the_ordinary_way(self):
        charge = self.bill()
        answer = self.api(self.cashier).post("/api/payments/", {
            "patient": self.mother.pk, "amount": "45000.00", "method": "cash"},
            format="json")
        self.assertEqual(answer.status_code, 201, answer.data)
        charge.refresh_from_db()
        self.assertEqual(charge.amount_paid, Decimal("45000.00"))
        self.assertEqual(charge.status, "paid")

    def test_several_maternity_services_bill_in_one_submission(self):
        keys = [r["key"] for r in self.offered()
                if r["name"] in ("ANC Booking Visit", "Obstetric Ultrasound")]
        answer = self.api(self.reception).post(
            "/api/charges/bill-services/",
            {"patient": self.mother.pk, "services": keys}, format="json")
        self.assertEqual(answer.status_code, 201, answer.data)
        self.assertEqual(Charge.objects.filter(patient=self.mother,
                                               source_type="maternity").count(), 2)


class TheMoneyIsAttributedToMaternity(MaternityPriceList):
    """
    The reason Maternity joined `REVENUE_DEPARTMENTS`: without it a ₦45,000
    delivery package reports as "Other / Unclassified".
    """

    def test_the_source_type_resolves_to_the_department(self):
        self.assertEqual(department_for_source("maternity").name, "Maternity")

    def test_the_charge_carries_the_department(self):
        row = next(r for r in self.offered() if r["name"] == "Delivery Package — Normal")
        self.api(self.reception).post(
            "/api/charges/bill-services/",
            {"patient": self.mother.pk, "services": [row["key"]]}, format="json")
        charge = Charge.objects.filter(patient=self.mother).latest("id")
        self.assertEqual(charge.department.code, "maternity")

    def test_maternity_is_in_the_registry(self):
        self.assertIn("maternity", [code for code, _, _ in REVENUE_DEPARTMENTS])

    def test_the_finance_report_has_a_maternity_row(self):
        row = next(r for r in self.offered() if r["name"] == "Delivery Package — Normal")
        self.api(self.reception).post(
            "/api/charges/bill-services/",
            {"patient": self.mother.pk, "services": [row["key"]]}, format="json")
        report = self.api(self.cashier).get("/api/finance/report/",
                                            {"preset": "today"}).data
        maternity = next((d for d in report["departments"] if d["key"] == "maternity"),
                         None)
        self.assertIsNotNone(maternity, "Maternity is missing from the report")
        self.assertEqual(Decimal(maternity["gross"]), Decimal("45000.00"))


class NothingElseWasBuilt(MaternityPriceList):

    def test_there_is_no_maternity_billing_model(self):
        from django.apps import apps as django_apps

        names = {model.__name__ for model in
                 django_apps.get_app_config("maternity").get_models()}
        for invented in ("MaternityCharge", "MaternityService", "MaternityBilling",
                         "MaternityPrice", "MaternityInvoice"):
            self.assertNotIn(invented, names)

    def test_the_catalogue_window_gained_no_new_reader(self):
        """Rule 50 — these are `BillingItem` rows, not a third catalogue."""
        row = next(r for r in self.offered() if r["name"] == "Newborn Care")
        self.assertEqual(row["source"], "billing_item")
