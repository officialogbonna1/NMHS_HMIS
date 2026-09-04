"""
The financial states a charge can actually be in, and who may put it there.

The arithmetic under all of it: original − discount − waiver − payments =
outstanding. Every one of those is kept as its own figure, because a bill
that only records what is left cannot be audited.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge, PatientLedger, PaymentDeferral
from apps.billing.services import add_charge, record_payment
from apps.patients.models import Patient


class WriteOffTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.charge = add_charge(patient=self.patient, description="Laboratory: FBC",
                                 amount="3500", created_by=self.cashier)
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

    def test_a_partial_waiver_leaves_the_rest_payable(self):
        self.client.post(f"/api/charges/{self.charge.pk}/waive/",
                         {"amount": "1000", "reason": "Hardship"}, format="json")
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount, Decimal("3500.00"))
        self.assertEqual(self.charge.amount_waived, Decimal("1000.00"))
        self.assertEqual(self.charge.payable, Decimal("2500.00"))
        self.assertEqual(self.charge.balance, Decimal("2500.00"))
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         Decimal("2500.00"))

    def test_a_waiver_and_a_payment_settle_the_charge_between_them(self):
        self.client.post(f"/api/charges/{self.charge.pk}/waive/",
                         {"amount": "1000", "reason": "Hardship"}, format="json")
        record_payment(patient=self.patient, amount="2500", received_by=self.cashier)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.balance, Decimal("0.00"))
        self.assertEqual(self.charge.settlement_status, "paid")

    def test_a_waiver_cannot_push_the_patient_into_credit(self):
        record_payment(patient=self.patient, amount="3000", received_by=self.cashier)
        self.client.post(f"/api/charges/{self.charge.pk}/waive/",
                         {"amount": "3000", "reason": "Too generous"}, format="json")
        self.charge.refresh_from_db()
        # Only the ₦500 that was still owed could be waived.
        self.assertEqual(self.charge.amount_waived, Decimal("500.00"))
        self.assertEqual(self.charge.balance, Decimal("0.00"))
        self.assertGreaterEqual(
            PatientLedger.objects.get(patient=self.patient).outstanding_balance, Decimal("0.00"))

    def test_a_flat_discount_and_a_percentage_discount_agree(self):
        other = add_charge(patient=self.patient, description="Laboratory: FBC (2)",
                           amount="3500", created_by=self.cashier)
        self.client.post(f"/api/charges/{self.charge.pk}/discount-amount/",
                         {"amount": "350", "reason": "flat"}, format="json")
        self.client.post(f"/api/charges/{other.pk}/discount/",
                         {"percent": "10", "reason": "percentage"}, format="json")
        self.charge.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.charge.amount_discounted, other.amount_discounted)

    def test_a_discount_needs_a_reason(self):
        response = self.client.post(f"/api/charges/{self.charge.pk}/discount-amount/",
                                    {"amount": "350"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_a_discount_is_capped_at_what_is_still_owed(self):
        self.client.post(f"/api/charges/{self.charge.pk}/discount-amount/",
                         {"amount": "9999", "reason": "typo"}, format="json")
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount_discounted, Decimal("3500.00"))
        self.assertEqual(self.charge.balance, Decimal("0.00"))

    def test_the_adjustment_records_who_approved_it(self):
        self.client.post(f"/api/charges/{self.charge.pk}/waive/",
                         {"amount": "500", "reason": "Management approval"}, format="json")
        adjustment = self.charge.adjustments.get()
        self.assertEqual(adjustment.approved_by, self.cashier)
        self.assertEqual(adjustment.kind, "waiver")
        self.assertEqual(adjustment.amount, Decimal("500.00"))
        self.assertEqual(adjustment.reason, "Management approval")


class DeferralTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.patient = Patient.objects.create(first_name="Uche", last_name="Nwosu", sex="M",
                                              created_by=self.reception)
        self.charge = add_charge(patient=self.patient, description="Laboratory: FBC",
                                 amount="3500", created_by=self.reception)
        self.client = APIClient()

    def test_deferring_moves_no_money(self):
        self.client.force_authenticate(self.reception)
        self.client.post(f"/api/charges/{self.charge.pk}/defer/",
                         {"reason": "Returning Friday"}, format="json")
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount_paid, Decimal("0.00"))
        self.assertEqual(self.charge.balance, Decimal("3500.00"))
        self.assertEqual(self.charge.settlement_status, "deferred")

    def test_a_deferred_charge_stays_on_the_debtors_list(self):
        self.client.force_authenticate(self.reception)
        self.client.post(f"/api/charges/{self.charge.pk}/defer/", {"reason": "x"}, format="json")
        self.client.force_authenticate(self.cashier)
        owing = self.client.get("/api/ledgers/", {"owing": "true"}).data
        rows = owing["results"] if "results" in owing else owing
        self.assertIn(self.patient.pk, [r["patient"] for r in rows])

    def test_a_deferred_charge_still_takes_the_next_payment(self):
        self.client.force_authenticate(self.reception)
        self.client.post(f"/api/charges/{self.charge.pk}/defer/", {"reason": "x"}, format="json")
        record_payment(patient=self.patient, amount="3500", received_by=self.reception)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.balance, Decimal("0.00"))
        self.assertEqual(self.charge.settlement_status, "paid")
        self.assertIsNotNone(PaymentDeferral.objects.get(charge=self.charge).released_at)

    def test_the_deferral_names_its_approver_on_the_charge(self):
        self.client.force_authenticate(self.reception)
        response = self.client.post(f"/api/charges/{self.charge.pk}/defer/",
                                    {"reason": "Emergency"}, format="json")
        self.assertEqual(response.data["deferral"]["approved_by"], "rec")
        self.assertEqual(response.data["deferral"]["amount_deferred"], "3500.00")
        self.assertEqual(response.data["deferral"]["reason"], "Emergency")

    def test_a_doctor_cannot_authorise_pay_later(self):
        self.client.force_authenticate(self.doctor)
        response = self.client.post(f"/api/charges/{self.charge.pk}/defer/",
                                    {"reason": "go on"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_a_settled_charge_cannot_be_deferred(self):
        record_payment(patient=self.patient, amount="3500", received_by=self.reception)
        self.client.force_authenticate(self.reception)
        response = self.client.post(f"/api/charges/{self.charge.pk}/defer/",
                                    {"reason": "x"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_a_partly_paid_charge_defers_only_what_is_left(self):
        record_payment(patient=self.patient, amount="1500", received_by=self.reception)
        self.client.force_authenticate(self.reception)
        self.client.post(f"/api/charges/{self.charge.pk}/defer/", {"reason": "rest"},
                         format="json")
        self.assertEqual(PaymentDeferral.objects.get(charge=self.charge).amount_deferred,
                         Decimal("2000.00"))
