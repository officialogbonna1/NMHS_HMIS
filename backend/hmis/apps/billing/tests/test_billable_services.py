"""
What Reception can bill.

The bug: the counter's Laboratory tab offered **one** test. Not a pagination
limit, not a filter and not a frontend cap — the desk was reading
`/billing-items/`, the price list, which held a single row called "Full Blood
Count", while the doctor was ordering from `LabTest`, a catalogue of sixty-six.
Two catalogues, one window, and the desk could not bill what had just been
ordered.

`/api/billable-services/` is that window widened to both books
(`billing/catalogue.py`). These tests hold the things that make it a fix
rather than a third list: every configured service is offered, each appears
once, the price is the catalogue's own, and billing one still raises exactly
the charge the counter has always raised.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import catalogue
from apps.billing.models import BillingItem, Charge
from apps.laboratory.models import LabTest
from apps.patients.models import Patient


class Catalogued(TestCase):
    """A hospital with a real laboratory catalogue and a real price list."""

    @classmethod
    def setUpTestData(cls):
        cls.reception = User.objects.create_user(username="rec", password="t", role="reception")
        cls.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        cls.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        cls.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        cls.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M")

        cls.tests = [
            LabTest.objects.create(code="t-fbc", name="Full Blood Count (test)",
                                   category="haematology", price=Decimal("3500"),
                                   specimen_type="Whole blood"),
            LabTest.objects.create(code="t-mp", name="Malaria Parasite (test)",
                                   category="parasitology", price=Decimal("1500")),
            LabTest.objects.create(code="t-rft", name="Renal Function (test)",
                                   category="chemistry", price=Decimal("6000")),
            LabTest.objects.create(code="t-widal", name="Widal (test)",
                                   category="serology", price=Decimal("2500")),
        ]
        cls.retired = LabTest.objects.create(code="t-old", name="Retired Assay (test)",
                                             category="chemistry", price=Decimal("900"),
                                             is_active=False)
        cls.card = BillingItem.objects.create(category="card", name="Adult Card",
                                              price=Decimal("2000"))
        cls.scan = BillingItem.objects.create(category="ultrasound", name="Abdominal Ultrasound (counter)",
                                              price=Decimal("8000"))

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def services(self, user, **params):
        response = self.api(user).get("/api/billable-services/", params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"]


class ReceptionSeesEveryLaboratoryService(Catalogued):
    def test_every_configured_laboratory_test_is_offered(self):
        """The reported bug, from the desk's side: four tests, four rows."""
        names = [row["name"] for row in self.services(self.reception, category="laboratory")]
        for test in self.tests:
            self.assertIn(test.name, names)

    def test_more_than_one_is_selectable(self):
        rows = self.services(self.reception, category="laboratory")
        self.assertGreater(len(rows), 1)
        # Each has what a picker needs to bill it: an identity, a name, a
        # price and the source type the charge is posted with.
        for row in rows:
            self.assertTrue(row["key"])
            self.assertEqual(row["source_type"], "laboratory")
            self.assertRegex(row["price"], r"^\d+\.\d{2}$")

    def test_the_price_is_the_catalogue_s_own(self):
        row = next(r for r in self.services(self.reception, category="laboratory")
                   if r["name"] == "Malaria Parasite (test)")
        self.assertEqual(row["price"], "1500.00")

    def test_a_linked_price_list_row_wins_and_is_not_listed_twice(self):
        """
        `LabTest.billing_item` is the existing rule: when a test points at a
        priced row, that price wins. So the row must not also be offered on
        its own, or the desk sees one service twice at two prices.
        """
        item = BillingItem.objects.create(category="laboratory", name="FBC (counter price)",
                                          price=Decimal("4000"))
        self.tests[0].billing_item = item
        self.tests[0].save(update_fields=["billing_item"])

        rows = self.services(self.reception, category="laboratory")
        names = [row["name"] for row in rows]
        self.assertNotIn("FBC (counter price)", names)
        fbc = next(row for row in rows if row["name"] == "Full Blood Count (test)")
        self.assertEqual(fbc["price"], "4000.00")

    def test_a_retired_test_is_not_offered(self):
        names = [row["name"] for row in self.services(self.reception, category="laboratory")]
        self.assertNotIn("Retired Assay (test)", names)

    def test_nothing_is_truncated_by_a_page(self):
        """A picker over configuration that stops at page one is the bug."""
        for index in range(60):
            LabTest.objects.create(code=f"t-bulk{index}", name=f"Bulk Test {index} (test)",
                                   category="chemistry", price=Decimal("1000"))
        response = self.api(self.reception).get("/api/billable-services/",
                                                {"category": "laboratory"})
        self.assertEqual(len(response.data["results"]), response.data["count"])
        self.assertGreaterEqual(response.data["count"], 64)

    def test_search_narrows_the_list_without_hiding_the_rest(self):
        rows = self.services(self.reception, category="laboratory", search="Malaria Parasite (test)")
        self.assertIn("Malaria Parasite (test)", [row["name"] for row in rows])
        self.assertGreater(len(self.services(self.reception, category="laboratory")), 1)


class EveryBillableDepartmentIsReachable(Catalogued):
    def test_the_other_categories_still_answer(self):
        for category in ("card", "ultrasound"):
            with self.subTest(category=category):
                self.assertTrue(self.services(self.reception, category=category))

    def test_every_seeded_ultrasound_examination_is_offered(self):
        """Radiology's catalogue is the price list — the same rows Reception
        bills and the doctor orders from."""
        rows = self.services(self.reception, category="ultrasound")
        self.assertGreater(len(rows), 1)
        self.assertIn("Abdominal Ultrasound (counter)", [row["name"] for row in rows])

    def test_the_categories_block_says_how_many_each_holds(self):
        response = self.api(self.reception).get("/api/billable-services/")
        counts = {row["category"]: row["count"] for row in response.data["categories"]}
        self.assertGreaterEqual(counts["laboratory"], len(self.tests))
        self.assertGreaterEqual(counts["ultrasound"], 1)

    def test_no_service_is_defined_twice(self):
        keys = [row["key"] for row in self.services(self.reception)]
        self.assertEqual(len(keys), len(set(keys)))

    def test_a_doctor_and_the_desk_read_the_same_rows(self):
        """The principle: one configured service, ordered and billed."""
        desk = {row["key"] for row in self.services(self.reception, category="laboratory")}
        clinic = {row["key"] for row in self.services(self.doctor, category="laboratory")}
        self.assertEqual(desk, clinic)


class BillingOneIsTheChargeItAlwaysWas(Catalogued):
    def test_the_counter_raises_an_ordinary_charge_at_the_catalogue_price(self):
        row = next(r for r in self.services(self.reception, category="laboratory")
                   if r["name"] == "Widal (test)")
        response = self.api(self.reception).post("/api/charges/", {
            "patient": self.patient.pk, "description": row["name"],
            "amount": row["price"], "source_type": row["source_type"],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)

        charge = Charge.objects.get(pk=response.data["id"])
        self.assertEqual(charge.amount, Decimal("2500.00"))
        self.assertEqual(charge.source_type, "laboratory")
        # Attribution is unchanged: the source type still resolves through
        # `billing/departments.py`, so the money lands on the laboratory.
        self.assertEqual(getattr(charge.department, "code", None), "laboratory")


class WhoReadsIt(Catalogued):
    def test_the_pharmacy_does_not(self):
        """It works a different catalogue, and this one carries the
        laboratory's names."""
        response = self.api(self.pharmacist).get("/api/billable-services/")
        self.assertEqual(response.status_code, 403)

    def test_an_anonymous_caller_does_not(self):
        self.assertIn(APIClient().get("/api/billable-services/").status_code, (401, 403))

    def test_the_cash_desk_does(self):
        self.assertEqual(
            self.api(self.cashier).get("/api/billable-services/").status_code, 200)


class TheModuleDefinesNothing(Catalogued):
    """`billing/catalogue.py` is a reader. If it ever starts holding services
    of its own, this is what says so."""

    def test_an_empty_hospital_offers_nothing(self):
        LabTest.objects.all().delete()
        BillingItem.objects.all().delete()
        self.assertEqual(catalogue.services(), [])

    def test_a_service_added_to_the_catalogue_appears_with_no_deployment(self):
        LabTest.objects.create(code="t-new", name="Brand New Assay (test)", category="chemistry",
                               price=Decimal("7500"))
        names = [row["name"] for row in catalogue.services(category="laboratory")]
        self.assertIn("Brand New Assay (test)", names)


class BillingSeveralServicesAtOnce(Catalogued):
    """
    `POST /api/charges/bill-services/` — the counter's multi-select, which is
    what a patient arriving with a doctor's list of five tests actually needs.

    The rule these hold is that the **request carries identities, not money**:
    the desk sends keys, the server prices them from the catalogue, and a
    figure in a request body is never what the patient is charged.
    """

    def bill(self, user, keys, patient=None):
        return self.api(user).post("/api/charges/bill-services/", {
            "patient": (patient or self.patient).pk, "services": keys,
        }, format="json")

    def keys_for(self, *names):
        rows = {row["name"]: row["key"] for row in self.services(self.reception)}
        return [rows[name] for name in names]

    def test_one_service_raises_one_charge(self):
        response = self.bill(self.reception, self.keys_for("Widal (test)"))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["total"], "2500.00")
        charge = Charge.objects.get(patient=self.patient)
        self.assertEqual((charge.description, charge.amount), ("Widal (test)", Decimal("2500.00")))

    def test_five_services_raise_five_charges_in_one_submission(self):
        names = ["Full Blood Count (test)", "Malaria Parasite (test)", "Renal Function (test)",
                 "Widal (test)", "Adult Card"]
        response = self.bill(self.reception, self.keys_for(*names))
        self.assertEqual(response.status_code, 201, response.data)

        charges = Charge.objects.filter(patient=self.patient)
        self.assertEqual(charges.count(), 5)
        self.assertEqual(sorted(c.description for c in charges), sorted(names))
        # 3500 + 1500 + 6000 + 2500 + 2000
        self.assertEqual(response.data["total"], "15500.00")
        self.assertEqual(sum(c.amount for c in charges), Decimal("15500.00"))

    def test_nothing_is_silently_dropped(self):
        keys = self.keys_for("Full Blood Count (test)", "Malaria Parasite (test)")
        response = self.bill(self.reception, keys)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual({row["description"] for row in response.data["charges"]},
                         {"Full Blood Count (test)", "Malaria Parasite (test)"})

    def test_the_same_service_twice_in_one_request_is_billed_once(self):
        key = self.keys_for("Widal (test)")[0]
        response = self.bill(self.reception, [key, key])
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(Charge.objects.filter(patient=self.patient).count(), 1)

    def test_the_price_is_the_catalogue_s_whatever_the_client_believes(self):
        """The screen's figure is for the person reading it, never for the bill."""
        response = self.api(self.reception).post("/api/charges/bill-services/", {
            "patient": self.patient.pk, "services": self.keys_for("Widal (test)"),
            "amount": "5.00", "total": "5.00", "price": "5.00",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Charge.objects.get(patient=self.patient).amount, Decimal("2500.00"))

    def test_a_service_that_has_since_been_retired_bills_nothing_at_all(self):
        keys = self.keys_for("Full Blood Count (test)", "Widal (test)")
        self.tests[0].is_active = False
        self.tests[0].save(update_fields=["is_active"])

        response = self.bill(self.reception, keys)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "unknown_service")
        # Five charges where the desk meant six is worse than an error.
        self.assertFalse(Charge.objects.filter(patient=self.patient).exists())

    def test_an_empty_selection_is_refused(self):
        response = self.bill(self.reception, [])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "no_services")

    def test_the_charges_are_attributed_the_way_a_single_one_is(self):
        self.bill(self.reception, self.keys_for("Malaria Parasite (test)", "Adult Card"))
        by_description = {c.description: c for c in Charge.objects.filter(patient=self.patient)}
        self.assertEqual(by_description["Malaria Parasite (test)"].department.code, "laboratory")
        self.assertEqual(by_description["Adult Card"].department.code, "reception")
        self.assertEqual(by_description["Adult Card"].source_type, "card")

    def test_the_ledger_carries_the_whole_bill(self):
        self.bill(self.reception, self.keys_for("Full Blood Count (test)", "Widal (test)"))
        self.patient.ledger.refresh_from_db()
        self.assertEqual(self.patient.ledger.total_charges, Decimal("6000.00"))
        self.assertEqual(self.patient.ledger.outstanding_balance, Decimal("6000.00"))

    def test_one_decision_is_one_bell(self):
        """Rule 35: a bill of four services is one line on the cash desk's
        notification list, not four."""
        from apps.core.models import Notification

        self.bill(self.reception, self.keys_for(
            "Full Blood Count (test)", "Malaria Parasite (test)", "Widal (test)", "Adult Card"))
        raised = Notification.objects.filter(recipient=self.cashier, category="billing")
        self.assertEqual(raised.count(), 1, [n.title for n in raised])
        self.assertIn("9,500", raised.first().message)

    def test_the_audit_row_names_every_service_billed(self):
        from apps.core.models import AuditLog

        self.bill(self.reception, self.keys_for("Widal (test)", "Adult Card"))
        entry = AuditLog.objects.filter(action="billing.services_billed").first()
        self.assertIsNotNone(entry)
        # In the order the desk ticked them — the request's own order is kept.
        self.assertEqual(entry.details["descriptions"], ["Widal (test)", "Adult Card"])
        self.assertEqual(entry.details["total"], "4500.00")

    def test_ultrasound_examinations_bill_the_same_way(self):
        keys = [row["key"] for row in self.services(self.reception, category="ultrasound")][:3]
        response = self.bill(self.reception, keys)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["count"], 3)
        self.assertTrue(all(c.department.code == "radiology"
                            for c in Charge.objects.filter(patient=self.patient)))

    def test_a_clinician_may_not_bill_at_the_counter(self):
        self.assertEqual(self.bill(self.doctor, self.keys_for("Widal (test)")).status_code, 403)
        self.assertFalse(Charge.objects.exists())

    def test_the_patient_must_be_named(self):
        response = self.api(self.reception).post("/api/charges/bill-services/",
                                                 {"services": self.keys_for("Widal (test)")},
                                                 format="json")
        self.assertEqual(response.status_code, 400)
