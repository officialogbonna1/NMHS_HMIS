"""
The doctor → pharmacy workflow with its directions: a script carries the dose,
frequency, duration, route and notes to the pharmacy counter, prescribing moves
no stock, dispensing still deducts FEFO, and the doctor's drug picker shows a
product's strength and form — never a count (rule 7).
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import StockRecord
from apps.inventory.testing import product, stock_the_pharmacy
from apps.patients.models import Patient
from apps.pharmacy.models import Prescription


class PrescriptionDirectionsTests(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F", created_by=self.doctor)
        self.drug = product("Amoxicillin", unit_name="capsule", category_name="Antibiotics",
                            strength="500 mg", dosage_form="Capsule")
        self.batch = stock_the_pharmacy(item=self.drug, quantity=30, actor=self.pharmacist,
                                        batch_no="AMX-1", sale_price="100")
        self.api = APIClient()

    def rows(self, response):
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data

    def test_a_script_carries_its_directions_to_the_pharmacy_and_still_dispenses_fefo(self):
        self.api.force_authenticate(self.doctor)
        response = self.api.post("/api/prescriptions/bulk/", {"patient": self.patient.pk, "lines": [{
            "item": self.drug.pk, "quantity": 21, "dosage_instructions": "1 capsule",
            "frequency": "Three times daily", "duration": "7 days", "route": "oral",
            "notes": "Complete the course"}]}, format="json")
        self.assertIn(response.status_code, (200, 201), response.data)

        prescription = Prescription.objects.get()
        self.assertEqual((prescription.dosage_instructions, prescription.frequency, prescription.duration,
                          prescription.route, prescription.notes, prescription.status),
                         ("1 capsule", "Three times daily", "7 days", "oral", "Complete the course", "pending"))
        self.assertEqual(StockRecord.objects.get(batch=self.batch).quantity, 30)   # prescribing moves nothing

        self.api.force_authenticate(self.pharmacist)
        row = self.rows(self.api.get("/api/prescriptions/", {"status": "pending"}))[0]
        self.assertEqual((row["route_label"], row["item_strength"], row["item_form"], row["frequency"]),
                         ("Oral", "500 mg", "Capsule", "Three times daily"))
        self.assertEqual(self.api.post(f"/api/prescriptions/{prescription.pk}/dispense/").status_code, 200)
        self.assertEqual(StockRecord.objects.get(batch=self.batch).quantity, 9)

    def test_an_unknown_route_refuses_the_whole_script(self):
        self.api.force_authenticate(self.doctor)
        response = self.api.post("/api/prescriptions/bulk/", {"patient": self.patient.pk, "lines": [
            {"item": self.drug.pk, "quantity": 1, "route": "oral"},
            {"item": product("Ibuprofen").pk, "quantity": 1, "route": "by osmosis"}]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Prescription.objects.exists())

    def test_a_script_without_directions_still_reads_as_it_always_did(self):
        self.api.force_authenticate(self.doctor)
        response = self.api.post("/api/prescriptions/bulk/", {"patient": self.patient.pk, "lines": [
            {"item": self.drug.pk, "quantity": 2, "dosage_instructions": "Twice daily"}]}, format="json")
        self.assertIn(response.status_code, (200, 201), response.data)
        prescription = Prescription.objects.get()
        self.assertEqual((prescription.frequency, prescription.route), ("", ""))

    def test_the_doctors_picker_shows_strength_and_form_but_never_a_count(self):
        self.api.force_authenticate(self.doctor)
        row = next(r for r in self.rows(self.api.get("/api/items/", {"search": "Amoxicillin"}))
                   if r["id"] == self.drug.pk)
        self.assertEqual((row["strength"], row["dosage_form"], row["available"]), ("500 mg", "Capsule", True))
        for leaked in ("total_quantity", "by_location", "quantity"):
            self.assertNotIn(leaked, row)
