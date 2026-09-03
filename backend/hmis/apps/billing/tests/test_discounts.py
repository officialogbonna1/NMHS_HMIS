from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.billing.models import Adjustment, Charge, PatientLedger
from apps.billing.services import (
    add_charge, record_payment, apply_percentage_discount, discount_patient_balance, waive_charge,
)
from apps.patients.models import Patient


class PercentageDiscountTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier")
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M", created_by=self.cashier)

    def _charge(self, description, amount):
        return add_charge(patient=self.patient, description=description, amount=Decimal(amount), created_by=self.cashier)

    def outstanding(self):
        # Re-read: patient.ledger caches the instance from the first
        # refresh_ledger call, which predates the discount.
        return PatientLedger.objects.get(patient=self.patient).outstanding_balance

    def test_a_percentage_comes_off_the_charge_and_the_balance(self):
        charge = self._charge("Consultation", "10000")
        apply_percentage_discount(charge=charge, percent=20, reason="Staff family", approved_by=self.cashier)
        charge.refresh_from_db()
        self.assertEqual(charge.amount_discounted, Decimal("2000.00"))
        self.assertEqual(charge.balance, Decimal("8000.00"))
        self.assertEqual(charge.status, "partial")
        self.assertEqual(self.outstanding(), Decimal("8000.00"))

    def test_the_adjustment_records_the_percentage_reason_and_approver(self):
        charge = self._charge("Consultation", "10000")
        apply_percentage_discount(charge=charge, percent=15, reason="Hardship", approved_by=self.cashier)
        adjustment = Adjustment.objects.get()
        self.assertEqual(adjustment.kind, "discount")
        self.assertEqual(adjustment.amount, Decimal("1500.00"))
        self.assertEqual(adjustment.approved_by, self.cashier)
        self.assertIn("15%", adjustment.reason)
        self.assertIn("Hardship", adjustment.reason)

    def test_a_hundred_percent_discount_settles_the_charge(self):
        charge = self._charge("Adult card", "2000")
        apply_percentage_discount(charge=charge, percent=100, reason="Charity", approved_by=self.cashier)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid")
        self.assertEqual(charge.balance, Decimal("0.00"))
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_a_discount_after_part_payment_is_capped_at_what_is_still_owed(self):
        charge = self._charge("Consultation", "10000")
        record_payment(patient=self.patient, amount=Decimal("9000"), received_by=self.cashier)
        # 50% of face value is 5000, but only 1000 is still owed.
        apply_percentage_discount(charge=charge, percent=50, reason="Goodwill", approved_by=self.cashier)
        charge.refresh_from_db()
        self.assertEqual(charge.amount_discounted, Decimal("1000.00"))
        self.assertEqual(charge.balance, Decimal("0.00"))
        self.assertEqual(charge.status, "paid")
        # Never pushes the patient into credit.
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_a_discounted_charge_only_needs_the_rest_paying(self):
        charge = self._charge("Consultation", "10000")
        apply_percentage_discount(charge=charge, percent=25, reason="Staff", approved_by=self.cashier)
        record_payment(patient=self.patient, amount=Decimal("7500"), received_by=self.cashier)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid")
        self.assertEqual(charge.balance, Decimal("0.00"))

    def test_nonsense_percentages_are_refused(self):
        charge = self._charge("Consultation", "10000")
        for percent in (0, -5, 101):
            with self.assertRaises(ValueError, msg=percent):
                apply_percentage_discount(charge=charge, percent=percent, reason="x", approved_by=self.cashier)
        self.assertFalse(Adjustment.objects.exists())

    def test_a_discount_needs_a_reason(self):
        charge = self._charge("Consultation", "10000")
        with self.assertRaises(ValueError):
            apply_percentage_discount(charge=charge, percent=10, reason="", approved_by=self.cashier)

    def test_a_settled_charge_has_nothing_left_to_discount(self):
        charge = self._charge("Adult card", "2000")
        record_payment(patient=self.patient, amount=Decimal("2000"), received_by=self.cashier)
        with self.assertRaises(ValueError):
            apply_percentage_discount(charge=charge, percent=10, reason="late", approved_by=self.cashier)

    def test_discounting_the_whole_balance_covers_every_open_charge(self):
        self._charge("Adult card", "2000")
        self._charge("Consultation", "8000")
        paid = self._charge("Lab", "5000")
        record_payment(patient=self.patient, amount=Decimal("2000"), received_by=self.cashier)  # settles the card

        adjustments = discount_patient_balance(patient=self.patient, percent=10, reason="Staff", approved_by=self.cashier)
        self.assertEqual(len(adjustments), 2)  # the settled card is skipped
        self.assertEqual(sum(a.amount for a in adjustments), Decimal("1300.00"))
        self.assertEqual(self.outstanding(), Decimal("11700.00"))
        paid.refresh_from_db()
        self.assertEqual(paid.amount_discounted, Decimal("500.00"))


class DiscountPermissionTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier")
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M", created_by=self.cashier)
        self.charge = add_charge(patient=self.patient, description="Consultation",
                                 amount=Decimal("10000"), created_by=self.cashier)

    def test_a_cashier_discounts_through_the_api(self):
        client = APIClient(); client.force_authenticate(self.cashier)
        response = client.post(f"/api/charges/{self.charge.id}/discount/", {"percent": "20", "reason": "Staff family"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["balance"]), Decimal("8000.00"))
        self.assertEqual(Decimal(response.data["discount_percent"]), Decimal("20.0"))

    def test_reception_cannot_give_money_away(self):
        client = APIClient(); client.force_authenticate(self.reception)
        self.assertEqual(
            client.post(f"/api/charges/{self.charge.id}/discount/", {"percent": "20", "reason": "friend"}).status_code, 403
        )
        self.assertEqual(
            client.post("/api/charges/discount-balance/", {"patient": self.patient.id, "percent": "20", "reason": "friend"}).status_code, 403
        )
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount_discounted, Decimal("0"))

    def test_a_bad_percentage_comes_back_as_a_readable_error(self):
        client = APIClient(); client.force_authenticate(self.cashier)
        response = client.post(f"/api/charges/{self.charge.id}/discount/", {"percent": "150", "reason": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("between 0 and 100", response.data["detail"])

    def test_discounting_a_whole_balance_over_the_api(self):
        client = APIClient(); client.force_authenticate(self.cashier)
        response = client.post("/api/charges/discount-balance/",
                               {"patient": self.patient.id, "percent": "10", "reason": "Staff"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["charges_discounted"], 1)
        self.assertEqual(Decimal(response.data["total_discounted"]), Decimal("1000.00"))


class WaiveAfterDiscountTests(TestCase):
    """Waiving credits what is still owed. Crediting the face value again
    after a payment or a discount would put the patient into credit."""

    def setUp(self):
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M", created_by=self.cashier)
        self.charge = add_charge(patient=self.patient, description="Consultation",
                                 amount=Decimal("10000"), created_by=self.cashier)

    def outstanding(self):
        return PatientLedger.objects.get(patient=self.patient).outstanding_balance

    def test_waiving_after_a_discount_only_credits_the_rest(self):
        apply_percentage_discount(charge=self.charge, percent=30, reason="Staff", approved_by=self.cashier)
        self.charge.refresh_from_db()
        waive_charge(charge=self.charge, reason="Hardship", approved_by=self.cashier)
        self.assertEqual(Adjustment.objects.get(kind="waiver").amount, Decimal("7000.00"))
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_waiving_after_a_part_payment_only_credits_the_rest(self):
        record_payment(patient=self.patient, amount=Decimal("4000"), received_by=self.cashier)
        self.charge.refresh_from_db()
        waive_charge(charge=self.charge, reason="Hardship", approved_by=self.cashier)
        self.assertEqual(Adjustment.objects.get(kind="waiver").amount, Decimal("6000.00"))
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_a_fully_settled_charge_cannot_be_waived(self):
        apply_percentage_discount(charge=self.charge, percent=100, reason="Charity", approved_by=self.cashier)
        self.charge.refresh_from_db()
        with self.assertRaises(ValueError):
            waive_charge(charge=self.charge, reason="again", approved_by=self.cashier)


class TransactionHistoryAccessTests(TestCase):
    """Reception reads a patient's whole statement — including the money
    written off — without gaining the right to write any of it off."""

    def setUp(self):
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier")
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M", created_by=self.cashier)
        self.charge = add_charge(patient=self.patient, description="Consultation",
                                 amount=Decimal("10000"), created_by=self.cashier)
        record_payment(patient=self.patient, amount=Decimal("3000"), received_by=self.reception)
        apply_percentage_discount(charge=self.charge, percent=10, reason="Staff family", approved_by=self.cashier)
        self.client = APIClient(); self.client.force_authenticate(self.reception)

    def test_reception_sees_every_part_of_the_statement(self):
        params = {"patient": self.patient.id}
        charges = self.client.get("/api/charges/", params)
        payments = self.client.get("/api/payments/", params)
        adjustments = self.client.get("/api/adjustments/", params)
        ledgers = self.client.get("/api/ledgers/", params)
        for response in (charges, payments, adjustments, ledgers):
            self.assertEqual(response.status_code, 200)

        self.assertEqual(len(charges.data["results"]), 1)
        self.assertEqual(len(payments.data["results"]), 1)
        self.assertEqual(len(adjustments.data["results"]), 1)

        discount = adjustments.data["results"][0]
        self.assertEqual(discount["kind"], "discount")
        self.assertEqual(discount["kind_label"], "Discount")
        self.assertEqual(discount["approved_by_name"], "cashier")
        self.assertIn("10%", discount["reason"])
        self.assertEqual(discount["charge_description"], "Consultation")

    def test_the_statement_reconciles_to_what_is_owed(self):
        ledger = self.client.get("/api/ledgers/", {"patient": self.patient.id}).data["results"][0]
        charged = Decimal(ledger["total_charges"])
        paid = Decimal(ledger["total_payments"])
        credits = Decimal(ledger["total_adjustments"])
        self.assertEqual(charged - paid - credits, Decimal(ledger["outstanding_balance"]))
        self.assertEqual(Decimal(ledger["outstanding_balance"]), Decimal("6000.00"))

    def test_reception_still_cannot_create_an_adjustment(self):
        response = self.client.post("/api/adjustments/", {
            "patient": self.patient.id, "kind": "discount", "amount": "500", "reason": "friend",
        })
        self.assertEqual(response.status_code, 403)
