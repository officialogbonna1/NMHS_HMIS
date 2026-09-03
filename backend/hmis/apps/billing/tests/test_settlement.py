from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.billing.models import BillingItem, Charge
from apps.billing.services import add_charge, record_payment
from apps.patients.models import Patient


class ChargeSettlementTests(TestCase):
    """A payment has to move the charges it settles, or every charge reads
    'unpaid' forever and the outstanding figures are fiction."""

    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M", created_by=self.reception)

    def _charge(self, description, amount):
        return add_charge(patient=self.patient, description=description, amount=Decimal(amount), created_by=self.reception)

    def test_paying_in_full_marks_the_charge_paid(self):
        charge = self._charge("Adult card", "2000")
        record_payment(patient=self.patient, amount=Decimal("2000"), received_by=self.reception)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid")
        self.assertEqual(charge.amount_paid, Decimal("2000"))
        self.assertEqual(charge.balance, Decimal("0"))

    def test_paying_half_marks_the_charge_part_paid(self):
        charge = self._charge("Consultation", "10000")
        record_payment(patient=self.patient, amount=Decimal("5000"), received_by=self.reception)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "partial")
        self.assertEqual(charge.amount_paid, Decimal("5000"))
        self.assertEqual(charge.balance, Decimal("5000"))

        record_payment(patient=self.patient, amount=Decimal("5000"), received_by=self.reception)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid")
        self.assertEqual(charge.balance, Decimal("0"))

    def test_pay_later_leaves_a_real_debt(self):
        charge = self._charge("Adult card", "2000")
        self.assertEqual(charge.status, "unpaid")
        self.assertEqual(charge.amount_paid, Decimal("0"))
        self.assertEqual(self.patient.ledger.outstanding_balance, Decimal("2000"))

    def test_a_payment_settles_the_oldest_charges_first(self):
        first = self._charge("Adult card", "2000")
        second = self._charge("Consultation", "5000")
        record_payment(patient=self.patient, amount=Decimal("3000"), received_by=self.reception)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(first.status, "paid")
        self.assertEqual(second.status, "partial")
        self.assertEqual(second.amount_paid, Decimal("1000"))
        self.assertEqual(second.balance, Decimal("4000"))

    def test_a_payment_cannot_exceed_what_is_owed(self):
        self._charge("Adult card", "2000")
        with self.assertRaises(ValueError):
            record_payment(patient=self.patient, amount=Decimal("2500"), received_by=self.reception)
        self.assertEqual(Charge.objects.get().status, "unpaid")

    def test_nothing_owed_means_nothing_to_pay(self):
        with self.assertRaises(ValueError):
            record_payment(patient=self.patient, amount=Decimal("100"), received_by=self.reception)


class ReceptionBillingApiTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M", created_by=self.reception)
        self.card = BillingItem.objects.create(category="card", name="Adult Card", price=Decimal("2000"))
        self.client = APIClient(); self.client.force_authenticate(self.reception)

    def test_reception_bills_a_card_and_takes_half(self):
        charge = self.client.post("/api/charges/", {
            "patient": self.patient.id, "description": self.card.name, "amount": self.card.price,
        })
        self.assertEqual(charge.status_code, 201)

        payment = self.client.post("/api/payments/", {"patient": self.patient.id, "amount": "1000", "method": "cash"})
        self.assertEqual(payment.status_code, 201)

        row = self.client.get("/api/charges/", {"patient": self.patient.id}).data["results"][0]
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["status_label"], "Part paid")
        self.assertEqual(Decimal(row["balance"]), Decimal("1000.00"))
        # Front-desk money is stamped as such, so it reconciles apart from
        # the pharmacy counter's takings.
        self.assertEqual(self.client.get("/api/payments/").data["results"][0]["channel"], "front_desk")

    def test_reception_cannot_waive_what_a_patient_owes(self):
        self.client.post("/api/charges/", {
            "patient": self.patient.id, "description": self.card.name, "amount": self.card.price,
        })
        charge_id = self.client.get("/api/charges/").data["results"][0]["id"]
        response = self.client.post(f"/api/charges/{charge_id}/waive/", {"reason": "friend"})
        self.assertEqual(response.status_code, 403)
