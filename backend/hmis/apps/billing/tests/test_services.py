from decimal import Decimal
from django.test import TestCase
from apps.accounts.models import User
from apps.patients.models import Patient
from apps.billing.services import add_charge, record_payment


class LedgerServiceTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M", created_by=self.cashier)

    def test_part_payment_keeps_correct_outstanding_balance(self):
        add_charge(patient=self.patient, description="Consultation", amount=Decimal("10000"), created_by=self.cashier)
        record_payment(patient=self.patient, amount=Decimal("5000"), received_by=self.cashier)
        self.patient.ledger.refresh_from_db()
        ledger = self.patient.ledger
        self.assertEqual(ledger.total_charges, Decimal("10000"))
        self.assertEqual(ledger.total_payments, Decimal("5000"))
        self.assertEqual(ledger.outstanding_balance, Decimal("5000"))
