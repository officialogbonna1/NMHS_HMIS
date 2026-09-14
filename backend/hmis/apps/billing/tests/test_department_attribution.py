"""
Which department a new charge is attributed to, and who decides.

`add_charge` is the single place a `Charge` is created — every path in the
system comes through it — so it is the single place attribution belongs. What
this file holds is the precedence:

1. **The authoritative `source_type` map wins.** A prescription is pharmacy
   work whoever raised it and whatever the request body says. A supplied
   department is ignored, not merged and not rejected — the same rule
   `Payment.channel` follows, stamped from the collector's role and never
   trusted from the client.
2. **An explicit department is used for a source the map does not cover** —
   a write-in, a blank, or `investigation`, where the diagnostics catalogue
   knows which unit performs the study better than a hard-coded default.
3. **`None` for anything else.** A charge is still raised: money owed is
   never refused over a reporting field, and the report says "Other /
   Unclassified", which is the truth about it.

Nothing here infers a department from a description, a route, a timestamp or
a naming convention.
"""
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.billing.departments import REVENUE_DEPARTMENTS, department_for_source
from apps.billing.services import add_charge, resolve_department
from apps.departments.models import Department
from apps.patients.models import Patient


class SourceTypeAttributionTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)

    def bill(self, source_type, *, department=None, amount="1000"):
        return add_charge(patient=self.patient, description="Test charge",
                          amount=Decimal(amount), created_by=self.cashier,
                          source_type=source_type, department=department)

    def test_every_known_source_type_lands_on_its_own_department(self):
        expected = {
            # What the billing counter posts — the BillingItem category.
            "card": "reception",
            "consultation": "consultation",
            "laboratory": "laboratory",
            "ultrasound": "radiology",
            "eye": "eye",
            "procedure": "theatre",
            # What the services stamp.
            "lab_test": "laboratory",
            "prescription": "pharmacy",
            "appointment": "consultation",
        }
        for source_type, code in expected.items():
            with self.subTest(source_type=source_type):
                charge = self.bill(source_type)
                self.assertIsNotNone(charge.department,
                                     f"{source_type!r} was left unattributed")
                self.assertEqual(charge.department.code, code)

    def test_a_procedure_is_theatre_work(self):
        """The hospital's own classification: `procedure` is what the billing
        catalogue and the referral purposes both call theatre work."""
        self.assertEqual(self.bill("procedure").department.code, "theatre")

    def test_the_case_of_the_source_type_does_not_matter(self):
        self.assertEqual(self.bill("Prescription").department.code, "pharmacy")
        self.assertEqual(self.bill("  LAB_TEST  ").department.code, "laboratory")

    # -- precedence ---------------------------------------------------------

    def test_a_supplied_department_cannot_override_an_authoritative_source(self):
        """
        The case this rule exists for: `source_type=prescription` with
        `department=reception` must never create a Reception charge.
        """
        reception = Department.objects.get(code="reception")
        charge = self.bill("prescription", department=reception)
        self.assertEqual(charge.department.code, "pharmacy")

    def test_the_same_holds_for_every_authoritative_source(self):
        wrong = Department.objects.get(code="theatre")
        for source_type, code in [("card", "reception"), ("lab_test", "laboratory"),
                                  ("ultrasound", "radiology"), ("eye", "eye"),
                                  ("consultation", "consultation")]:
            with self.subTest(source_type=source_type):
                self.assertEqual(self.bill(source_type, department=wrong).department.code, code)

    def test_an_explicit_department_is_used_where_the_map_has_no_opinion(self):
        """A write-in charge: the counter's choice is the only attribution
        there is, so it is honoured."""
        general = Department.objects.create(code="physiotherapy", name="Physiotherapy")
        self.assertEqual(self.bill("other", department=general).department.code, "physiotherapy")
        self.assertEqual(self.bill("", department=general).department.code, "physiotherapy")

    def test_investigation_keeps_the_diagnostics_catalogues_own_department(self):
        """
        Deliberately unmapped: `diagnostics` passes `catalog.department`, and
        that row knows which unit performs the study better than one
        hard-coded guess could.
        """
        imaging = Department.objects.create(code="ct", name="CT Suite")
        self.assertEqual(self.bill("investigation", department=imaging).department.code, "ct")

    def test_an_unmapped_source_with_no_department_still_bills(self):
        """
        A reporting gap must never refuse a charge. The money is owed; the
        report shows it as "Other / Unclassified", which is the truth.
        """
        charge = self.bill("something_new")
        self.assertIsNone(charge.department)
        self.assertEqual(charge.amount, Decimal("1000.00"))

    def test_a_blank_source_with_no_department_still_bills(self):
        charge = self.bill("")
        self.assertIsNone(charge.department)
        self.assertEqual(charge.amount, Decimal("1000.00"))

    def test_attribution_survives_an_unseeded_registry(self):
        """
        If the department row is missing, the caller's own department is
        better than nothing — and a charge is raised either way.
        """
        Department.objects.filter(code="pharmacy").delete()
        fallback = Department.objects.get(code="reception")
        self.assertEqual(resolve_department("prescription", fallback), fallback)
        self.assertIsNone(resolve_department("prescription", None))
        self.assertIsNone(self.bill("prescription").department)

    def test_department_and_source_type_agree_on_every_normal_billing_flow(self):
        """
        The stored department and the reporting map must never disagree, or a
        department's revenue silently moves between the table and the chart.
        """
        from apps.billing.reporting import department_for

        for source_type, _, _ in [(s, None, None) for _, _, sources in REVENUE_DEPARTMENTS
                                  for s in sources]:
            with self.subTest(source_type=source_type):
                charge = self.bill(source_type)
                key, _label = department_for(
                    charge.source_type,
                    charge.department.code if charge.department_id else None,
                    charge.department.name if charge.department_id else None)
                self.assertEqual(charge.department.code, key,
                                 "the stored department and the report disagree")


class ServiceAttributionTests(TestCase):
    """
    The two services that raise their own charges. They pass the department at
    the call site as well as relying on the chokepoint, so the code reads as
    what it is — these prove the charge that actually comes out is right.
    """

    def setUp(self):
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.pharmacist = User.objects.create_user(username="pharm", password="t", role="pharmacist")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)

    def test_a_real_laboratory_charge_is_laboratory_revenue(self):
        from apps.laboratory.models import LabOrder, LabTest
        from apps.laboratory.services import add_tests

        test = LabTest.objects.filter(is_active=True, price__gt=0).first()
        self.assertIsNotNone(test, "the seeded catalogue has no priced test to order")
        order = LabOrder.objects.create(patient=self.patient, requested_by=self.doctor,
                                        created_by=self.doctor)
        add_tests(order=order, tests=[test], author=self.doctor)

        charge = order.items.first().charge
        self.assertIsNotNone(charge, "ordering a priced test raised no charge")
        self.assertEqual(charge.source_type, "lab_test")
        self.assertIsNotNone(charge.department)
        self.assertEqual(charge.department.code, "laboratory")

    def test_a_real_pharmacy_charge_is_pharmacy_revenue(self):
        from apps.billing.models import Charge
        from apps.inventory.testing import product, stock_the_pharmacy
        from apps.pharmacy.services import create_prescription, dispense_prescription

        item = product(name="Paracetamol 500mg")
        stock_the_pharmacy(item=item, quantity=100, actor=self.pharmacist,
                           sale_price="20")
        prescription = create_prescription(
            patient=self.patient, doctor=self.doctor, item=item, quantity=5,
            dosage_instructions="1 tds",
        )
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)

        charge = Charge.objects.get(source_type="prescription", source_id=prescription.pk)
        self.assertIsNotNone(charge.department)
        self.assertEqual(charge.department.code, "pharmacy")


class RegistryHelperTests(TestCase):
    def test_department_for_source_returns_the_row(self):
        self.assertEqual(department_for_source("lab_test").code, "laboratory")
        self.assertEqual(department_for_source("PRESCRIPTION").code, "pharmacy")

    def test_it_answers_none_for_a_source_it_does_not_own(self):
        for source in ("", "other", "investigation", "made_up", None):
            with self.subTest(source=source):
                self.assertIsNone(department_for_source(source))
