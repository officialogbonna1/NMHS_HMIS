from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.billing.models import Payment
from apps.inventory.models import PHARMACY, Item, Batch, StockLocation, StockMovement, StockRecord
from apps.inventory.services import receive_stock
from apps.patients.models import Patient
from apps.pharmacy.models import Prescription
from apps.pharmacy.services import (
    create_prescription, dispense_prescription, cancel_prescription,
    create_prescription_and_dispense, OutOfStockError, AlreadyDispensedError,
)


class PharmacyTestCase(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.pharmacist = User.objects.create_user(username="pharmacist", password="test", role="pharmacist")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F", created_by=self.doctor)
        self.item = Item.objects.create(name="Paracetamol")
        # Stock stands on the pharmacy's own shelf: that is the only place a
        # patient's drugs can come off (see apps/inventory/tests/test_locations).
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)
        self.early = self._stock("EARLY", quantity=3, days=7, sale_price="20")
        self.late = self._stock("LATE", quantity=10, days=30, sale_price="25")

    def _stock(self, batch_no, *, quantity, days, sale_price):
        batch = Batch.objects.create(
            item=self.item, batch_no=batch_no, cost_price=Decimal("10"),
            sale_price=Decimal(sale_price),
            expiry_date=timezone.localdate() + timedelta(days=days))
        receive_stock(batch=batch, quantity=quantity, actor=self.pharmacist,
                      location=self.pharmacy)
        return batch

    def held(self, batch):
        """What the pharmacy is holding of this lot."""
        record = StockRecord.objects.filter(batch=batch, location=self.pharmacy).first()
        return record.quantity if record else 0

    def empty_the_shelf(self):
        StockRecord.objects.filter(location=self.pharmacy).update(quantity=0)


class DispensingTests(PharmacyTestCase):
    def test_dispensing_uses_fefo_and_creates_ledger_charge(self):
        prescription = create_prescription_and_dispense(patient=self.patient, doctor=self.doctor, item=self.item, quantity=5)
        self.assertEqual(prescription.status, "dispensed")
        self.assertEqual(self.held(self.early), 0)
        self.assertEqual(self.held(self.late), 8)
        self.assertEqual(StockMovement.objects.filter(reference=f"prescription:{prescription.id}").count(), 2)
        self.assertEqual(self.patient.ledger.total_charges, Decimal("110"))

    def test_prescribing_does_not_move_stock_until_the_pharmacy_dispenses(self):
        prescription = create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=5)
        self.assertEqual(prescription.status, "pending")
        self.assertEqual((self.held(self.early), self.held(self.late)), (3, 10))
        # The two receipts, and nothing since: prescribing moves no stock.
        self.assertFalse(StockMovement.objects.exclude(reason="received").exists())
        # No charge either — the patient is billed for what is actually handed over.
        self.assertFalse(self.patient.charges.exists())

    def test_dispensing_records_the_pharmacist_not_the_prescriber(self):
        prescription = create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=5)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        prescription.refresh_from_db()
        self.assertEqual(prescription.dispensed_by, self.pharmacist)
        self.assertIsNotNone(prescription.dispensed_at)
        self.assertEqual(prescription.dispensed_value, Decimal("110"))
        self.assertEqual(
            set(StockMovement.objects.filter(reason="prescription")
                .values_list("performed_by", flat=True)),
            {self.pharmacist.id},
        )

    def test_a_prescription_cannot_be_dispensed_twice(self):
        prescription = create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=2)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        with self.assertRaises(AlreadyDispensedError):
            dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        self.assertEqual(self.held(self.early), 1)  # deducted once, not twice

    def test_dispensing_fails_when_stock_ran_out_after_prescribing(self):
        prescription = create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=13)
        self.empty_the_shelf()
        with self.assertRaises(OutOfStockError):
            dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        prescription.refresh_from_db()
        self.assertEqual(prescription.status, "pending")

    def test_expired_batches_are_never_dispensed(self):
        Batch.objects.filter(pk=self.early.pk).update(expiry_date=timezone.localdate() - timedelta(days=1))
        prescription = create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=10)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        self.assertEqual(self.held(self.early), 3)
        self.assertEqual(self.held(self.late), 0)

    def test_cannot_prescribe_more_than_is_in_stock(self):
        with self.assertRaises(OutOfStockError):
            create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=14)
        self.assertFalse(Prescription.objects.exists())

    def test_cancelled_prescription_leaves_stock_alone(self):
        prescription = create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=5)
        cancel_prescription(prescription=prescription, actor=self.pharmacist, reason="Patient declined")
        prescription.refresh_from_db()
        self.assertEqual(prescription.status, "cancelled")
        self.assertEqual(self.held(self.early), 3)
        with self.assertRaises(AlreadyDispensedError):
            dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)


class PharmacyApiTests(PharmacyTestCase):
    def test_doctor_prescribes_pharmacist_dispenses_and_takes_payment(self):
        doctor_client = APIClient(); doctor_client.force_authenticate(self.doctor)
        response = doctor_client.post("/api/prescriptions/", {
            "patient": self.patient.id, "item": self.item.id, "quantity": 5,
            "dosage_instructions": "1 tablet twice daily",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "pending")
        prescription_id = response.data["id"]

        pharmacist_client = APIClient(); pharmacist_client.force_authenticate(self.pharmacist)
        response = pharmacist_client.post(f"/api/prescriptions/{prescription_id}/dispense/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "dispensed")

        response = pharmacist_client.post("/api/payments/", {"patient": self.patient.id, "amount": "110", "method": "cash"})
        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get(pk=response.data["id"])
        # Stamped from the collector's role so pharmacy takings reconcile separately.
        self.assertEqual(payment.channel, "pharmacy")
        self.assertEqual(payment.received_by, self.pharmacist)
        self.patient.ledger.refresh_from_db()
        self.assertEqual(self.patient.ledger.outstanding_balance, Decimal("0"))

    def test_doctor_cannot_dispense_their_own_prescription(self):
        prescription = create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=2)
        client = APIClient(); client.force_authenticate(self.doctor)
        response = client.post(f"/api/prescriptions/{prescription.id}/dispense/")
        self.assertEqual(response.status_code, 403)
        prescription.refresh_from_db()
        self.assertEqual(prescription.status, "pending")

    def test_pharmacist_cannot_write_a_prescription(self):
        client = APIClient(); client.force_authenticate(self.pharmacist)
        response = client.post("/api/prescriptions/", {"patient": self.patient.id, "item": self.item.id, "quantity": 1})
        self.assertEqual(response.status_code, 403)

    def test_pharmacist_cannot_waive_a_charge(self):
        prescription = create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=2)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        charge = self.patient.charges.get()
        client = APIClient(); client.force_authenticate(self.pharmacist)
        response = client.post(f"/api/charges/{charge.id}/waive/", {"reason": "no"})
        self.assertEqual(response.status_code, 403)
