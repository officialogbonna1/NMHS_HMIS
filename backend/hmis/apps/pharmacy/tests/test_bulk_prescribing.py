"""
A whole script in one action: one patient, several drugs.

The point of writing them together is that a refusal on one drug must not
leave the others queued — the doctor would have no way to tell which of the
five reached the pharmacy.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge
from apps.core.models import Notification
from apps.inventory.models import Item, Batch, StockMovement
from apps.patients.models import Patient
from apps.pharmacy.models import Prescription


class BulkPrescribingTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.pharmacist = User.objects.create_user(username="pharmacist", password="test", role="pharmacist")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)

        self.paracetamol = self._stocked("Paracetamol", quantity=100, sale_price="20")
        self.amoxicillin = self._stocked("Amoxicillin", quantity=50, sale_price="150")
        self.scarce = self._stocked("Morphine", quantity=2, sale_price="500")

        self.client = APIClient(); self.client.force_authenticate(self.doctor)

    def _stocked(self, name, quantity, sale_price):
        item = Item.objects.create(name=name, unit="tablet")
        Batch.objects.create(item=item, batch_no=f"B-{name}", quantity=quantity,
                             cost_price=Decimal("10"), sale_price=Decimal(sale_price),
                             expiry_date=timezone.localdate() + timedelta(days=180))
        return item

    def _send(self, lines, patient=None):
        return self.client.post("/api/prescriptions/bulk/", {
            "patient": (patient or self.patient).id, "lines": lines,
        }, format="json")

    def test_several_drugs_go_over_as_one_prescription(self):
        response = self._send([
            {"item": self.paracetamol.id, "quantity": 20, "dosage_instructions": "1 tds after meals"},
            {"item": self.amoxicillin.id, "quantity": 15, "dosage_instructions": "1 bd"},
        ])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(Prescription.objects.filter(patient=self.patient).count(), 2)
        self.assertEqual({p.status for p in Prescription.objects.all()}, {"pending"})
        written = Prescription.objects.get(item=self.paracetamol)
        self.assertEqual(written.dosage_instructions, "1 tds after meals")
        self.assertEqual(written.doctor, self.doctor)

    def test_writing_the_script_moves_no_stock(self):
        """Prescribing queues a request; dispensing is what deducts."""
        self._send([{"item": self.paracetamol.id, "quantity": 20}])
        self.assertEqual(Batch.objects.get(item=self.paracetamol).quantity, 100)
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(Charge.objects.exists())

    def test_one_drug_short_of_stock_takes_the_whole_script_down(self):
        response = self._send([
            {"item": self.paracetamol.id, "quantity": 20},
            {"item": self.scarce.id, "quantity": 10},        # only 2 on the shelf
            {"item": self.amoxicillin.id, "quantity": 15},
        ])
        self.assertEqual(response.status_code, 400)
        self.assertIn("Morphine", response.data["detail"])
        # Nothing queued — not the two that would have succeeded either.
        self.assertEqual(Prescription.objects.count(), 0)

    def test_the_same_drug_twice_is_refused_by_name(self):
        response = self._send([
            {"item": self.paracetamol.id, "quantity": 10},
            {"item": self.paracetamol.id, "quantity": 5},
        ])
        self.assertEqual(response.status_code, 400)
        self.assertIn("twice", response.data["detail"])
        self.assertEqual(Prescription.objects.count(), 0)

    def test_an_empty_script_is_refused(self):
        self.assertEqual(self._send([]).status_code, 400)

    def test_a_missing_quantity_is_refused_naming_the_drug(self):
        response = self._send([{"item": self.paracetamol.id}])
        self.assertEqual(response.status_code, 400)
        self.assertIn("Paracetamol", response.data["lines"])

    def test_the_pharmacy_gets_one_notification_for_the_whole_script(self):
        self._send([
            {"item": self.paracetamol.id, "quantity": 20},
            {"item": self.amoxicillin.id, "quantity": 15},
        ])
        notes = Notification.objects.filter(recipient=self.pharmacist, category="pharmacy")
        self.assertEqual(notes.count(), 1, "one errand at the counter, one notification")
        note = notes.first()
        self.assertIn("2 drugs", note.title)
        self.assertIn("Paracetamol ×20", note.message)
        self.assertIn("Amoxicillin ×15", note.message)
        self.assertEqual(note.action_url, "/pharmacy")

    def test_only_a_doctor_can_write_one(self):
        for role_user in (self.pharmacist, self.reception):
            client = APIClient(); client.force_authenticate(role_user)
            response = client.post("/api/prescriptions/bulk/", {
                "patient": self.patient.id,
                "lines": [{"item": self.paracetamol.id, "quantity": 5}],
            }, format="json")
            self.assertEqual(response.status_code, 403, role_user.role)
        self.assertEqual(Prescription.objects.count(), 0)

    def test_dispensing_then_deducts_stock_and_raises_the_charge(self):
        """The whole point of the queue: the pharmacy is what moves money
        and stock."""
        created = self._send([
            {"item": self.paracetamol.id, "quantity": 20},
            {"item": self.amoxicillin.id, "quantity": 10},
        ])
        pharmacy = APIClient(); pharmacy.force_authenticate(self.pharmacist)
        for prescription in created.data:
            self.assertEqual(
                pharmacy.post(f"/api/prescriptions/{prescription['id']}/dispense/").status_code, 200)

        self.assertEqual(Batch.objects.get(item=self.paracetamol).quantity, 80)
        self.assertEqual(Batch.objects.get(item=self.amoxicillin).quantity, 40)
        # Every unit that left a batch has an audit row behind it.
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(sum(m.change for m in StockMovement.objects.all()), -30)

        charges = Charge.objects.filter(patient=self.patient)
        self.assertEqual(charges.count(), 2)
        # 20 × 20 + 10 × 150
        self.assertEqual(sum(c.amount for c in charges), Decimal("1900"))

    def test_the_money_is_then_collectable_at_the_counter(self):
        created = self._send([{"item": self.paracetamol.id, "quantity": 20}])
        pharmacy = APIClient(); pharmacy.force_authenticate(self.pharmacist)
        pharmacy.post(f"/api/prescriptions/{created.data[0]['id']}/dispense/")

        charge = Charge.objects.get(patient=self.patient)
        self.assertEqual(charge.balance, Decimal("400"))

        paid = pharmacy.post("/api/payments/", {
            "patient": self.patient.id, "amount": "400", "method": "cash",
        }, format="json")
        self.assertEqual(paid.status_code, 201, paid.data)
        charge.refresh_from_db()
        self.assertEqual(charge.balance, Decimal("0"))
        self.assertEqual(charge.status, "paid")
