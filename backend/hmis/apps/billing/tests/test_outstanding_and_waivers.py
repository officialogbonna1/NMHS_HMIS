"""
The debtors list and the write-off register.

Both are worked as lists rather than looked up per patient, so both are
filtered and ordered in the database — a patient must not linger on the
debtors list because the page only fetched the first 25 ledgers.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Adjustment
from apps.billing.services import add_charge, record_payment, waive_charge, apply_percentage_discount
from apps.patients.models import Patient


class OutstandingListTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier")
        self.client = APIClient(); self.client.force_authenticate(self.cashier)

    def _patient(self, first, phone=""):
        return Patient.objects.create(first_name=first, last_name="Doe", sex="F",
                                      phone_number=phone, created_by=self.reception)

    def _owing(self, **params):
        response = self.client.get("/api/ledgers/", {"owing": "true", **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"]

    def test_only_patients_who_owe_are_listed(self):
        debtor = self._patient("Amaka")
        settled = self._patient("Chidi")
        add_charge(patient=debtor, description="Consultation", amount=Decimal("5000"),
                   created_by=self.reception)
        add_charge(patient=settled, description="Card", amount=Decimal("2000"),
                   created_by=self.reception)
        record_payment(patient=settled, amount=Decimal("2000"), method="cash",
                       received_by=self.cashier)

        names = [row["patient_name"] for row in self._owing()]
        self.assertEqual(names, [str(debtor)])

    def test_paying_in_full_takes_them_off_the_list(self):
        patient = self._patient("Amaka")
        add_charge(patient=patient, description="Consultation", amount=Decimal("5000"),
                   created_by=self.reception)
        self.assertEqual(len(self._owing()), 1)

        record_payment(patient=patient, amount=Decimal("5000"), method="cash",
                       received_by=self.cashier)
        self.assertEqual(self._owing(), [])

    def test_a_part_payment_leaves_them_on_it_with_the_remainder(self):
        patient = self._patient("Amaka")
        add_charge(patient=patient, description="Consultation", amount=Decimal("5000"),
                   created_by=self.reception)
        record_payment(patient=patient, amount=Decimal("2000"), method="cash",
                       received_by=self.cashier)

        row = self._owing()[0]
        self.assertEqual(Decimal(row["outstanding_balance"]), Decimal("3000.00"))
        self.assertEqual(Decimal(row["total_payments"]), Decimal("2000.00"))

    def test_waiving_the_rest_also_clears_them(self):
        patient = self._patient("Amaka")
        charge = add_charge(patient=patient, description="Consultation", amount=Decimal("5000"),
                            created_by=self.reception)
        waive_charge(charge=charge, reason="Hardship", approved_by=self.cashier)
        self.assertEqual(self._owing(), [])

    def test_the_biggest_debt_is_first(self):
        for name, amount in (("Small", "1000"), ("Biggest", "9000"), ("Middle", "4000")):
            patient = self._patient(name)
            add_charge(patient=patient, description="x", amount=Decimal(amount),
                       created_by=self.reception)
        first_names = [row["patient_name"].split(", ")[1] for row in self._owing()]
        self.assertEqual(first_names, ["Biggest", "Middle", "Small"])

    def test_the_row_carries_what_the_desk_needs_to_chase_it(self):
        patient = self._patient("Amaka", phone="08134621576")
        add_charge(patient=patient, description="x", amount=Decimal("5000"),
                   created_by=self.reception)
        row = self._owing()[0]
        self.assertEqual(row["patient_file_number"], patient.file_number)
        self.assertEqual(row["patient_phone"], "08134621576")
        self.assertEqual(row["patient"], patient.id)

    def test_it_can_be_searched_by_name_or_file_number(self):
        amaka = self._patient("Amaka")
        self._patient("Chidi")
        for p in Patient.objects.all():
            add_charge(patient=p, description="x", amount=Decimal("1000"), created_by=self.reception)

        self.assertEqual([r["patient_name"] for r in self._owing(search="Amaka")], [str(amaka)])
        self.assertEqual([r["patient_name"] for r in self._owing(search=amaka.file_number)], [str(amaka)])

    def test_reception_can_read_it_too(self):
        patient = self._patient("Amaka")
        add_charge(patient=patient, description="x", amount=Decimal("5000"), created_by=self.reception)
        client = APIClient(); client.force_authenticate(self.reception)
        response = client.get("/api/ledgers/", {"owing": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)

    def test_a_nurse_cannot(self):
        nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        client = APIClient(); client.force_authenticate(nurse)
        self.assertEqual(client.get("/api/ledgers/", {"owing": "true"}).status_code, 403)


class WaiverRegisterTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier",
                                                first_name="Ada", last_name="Bello")
        self.patient = Patient.objects.create(first_name="Amaka", last_name="Nwosu", sex="F",
                                              created_by=self.reception)
        self.client = APIClient(); self.client.force_authenticate(self.cashier)

    def _adjustments(self, **params):
        response = self.client.get("/api/adjustments/", params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"]

    def test_a_waiver_is_listed_with_its_reason_and_approver(self):
        charge = add_charge(patient=self.patient, description="Consultation",
                            amount=Decimal("5000"), created_by=self.reception)
        waive_charge(charge=charge, reason="Staff dependant", approved_by=self.cashier)

        row = self._adjustments(kind="waiver")[0]
        self.assertEqual(row["patient_name"], str(self.patient))
        self.assertEqual(Decimal(row["amount"]), Decimal("5000.00"))
        self.assertEqual(row["reason"], "Staff dependant")
        self.assertEqual(row["approved_by_name"], "Ada Bello")
        self.assertEqual(row["charge_description"], "Consultation")
        self.assertEqual(row["kind_label"], "Waiver")

    def test_waivers_discounts_and_refunds_are_kept_apart(self):
        waived = add_charge(patient=self.patient, description="Card", amount=Decimal("2000"),
                            created_by=self.reception)
        waive_charge(charge=waived, reason="Hardship", approved_by=self.cashier)
        discounted = add_charge(patient=self.patient, description="Consultation",
                                amount=Decimal("10000"), created_by=self.reception)
        apply_percentage_discount(charge=discounted, percent="10", reason="Staff",
                                  approved_by=self.cashier)
        Adjustment.objects.create(patient=self.patient, kind="refund", amount=Decimal("500"),
                                  reason="Overpaid", approved_by=self.cashier)

        self.assertEqual(len(self._adjustments(kind="waiver")), 1)
        self.assertEqual(len(self._adjustments(kind="discount")), 1)
        self.assertEqual(len(self._adjustments(kind="refund")), 1)

    def test_the_newest_write_off_is_first(self):
        for reason in ("older", "newer"):
            charge = add_charge(patient=self.patient, description=reason, amount=Decimal("1000"),
                                created_by=self.reception)
            waive_charge(charge=charge, reason=reason, approved_by=self.cashier)
        older = Adjustment.objects.get(reason="older")
        Adjustment.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=1))

        self.assertEqual([a["reason"] for a in self._adjustments(kind="waiver")], ["newer", "older"])

    def test_it_can_be_searched_by_patient_or_reason(self):
        charge = add_charge(patient=self.patient, description="Consultation",
                            amount=Decimal("5000"), created_by=self.reception)
        waive_charge(charge=charge, reason="Staff dependant", approved_by=self.cashier)

        self.assertEqual(len(self._adjustments(kind="waiver", search="Nwosu")), 1)
        self.assertEqual(len(self._adjustments(kind="waiver", search="dependant")), 1)
        self.assertEqual(len(self._adjustments(kind="waiver", search="nothing")), 0)

    def test_reception_reads_the_register_but_cannot_add_to_it(self):
        charge = add_charge(patient=self.patient, description="x", amount=Decimal("1000"),
                            created_by=self.reception)
        waive_charge(charge=charge, reason="Hardship", approved_by=self.cashier)

        client = APIClient(); client.force_authenticate(self.reception)
        self.assertEqual(client.get("/api/adjustments/", {"kind": "waiver"}).status_code, 200)
        created = client.post("/api/adjustments/", {
            "patient": self.patient.id, "kind": "waiver", "amount": "500", "reason": "made up",
        }, format="json")
        self.assertEqual(created.status_code, 403)
