from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.clinical.models import Vitals, ConsultationNote, NursingNote
from apps.inventory.testing import stock_the_pharmacy
from apps.inventory.models import Item, Batch
from apps.patients.models import Patient, Allergy, MedicalCondition, Medication
from apps.pharmacy.services import create_prescription


class PatientOverviewTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.other_doctor = User.objects.create_user(username="doctor2", password="test", role="doctor")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F", created_by=self.reception)

        Allergy.objects.create(patient=self.patient, name="Penicillin", reactions=["Difficulty breathing"], is_dangerous=True)
        Allergy.objects.create(patient=self.patient, name="Dust", reactions=["Hives"])
        MedicalCondition.objects.create(patient=self.patient, name="Asthma")
        Medication.objects.create(patient=self.patient, name="Salbutamol", strength="100mcg")
        self.vitals = Vitals.objects.create(
            patient=self.patient, recorded_by=self.nurse, visit_time=timezone.now(),
            temperature_c=Decimal("38.2"), heart_rate=96, bp_systolic=130, bp_diastolic=85,
        )
        NursingNote.objects.create(patient=self.patient, nurse=self.nurse, observation="Febrile, alert.")
        ConsultationNote.objects.create(
            patient=self.patient, doctor=self.doctor, visit_time=timezone.now(),
            reason_for_visit="Fever", diagnosis="Malaria",
        )
        self.item = Item.objects.create(name="Artemether")
        # On the pharmacy shelf: a prescription can only be written against
        # stock the counter can actually hand over.
        stock_the_pharmacy(item=self.item, quantity=20, actor=self.doctor,
                           sale_price="25", expiry_days=90)
        create_prescription(patient=self.patient, doctor=self.doctor, item=self.item, quantity=6)
        Appointment.objects.create(patient=self.patient, doctor=self.doctor, reason="Fever")

        self.client = APIClient(); self.client.force_authenticate(self.doctor)

    def test_the_overview_gathers_the_whole_chart_in_one_response(self):
        response = self.client.get(f"/api/patients/{self.patient.id}/overview/")
        self.assertEqual(response.status_code, 200)
        data = response.data

        self.assertEqual(data["patient"]["file_number"], self.patient.file_number)
        # Dangerous allergies are flagged so the drug picker isn't the first warning.
        self.assertEqual({a["label"] for a in data["alerts"]}, {"Penicillin", "Dust"})
        self.assertTrue(next(a for a in data["alerts"] if a["label"] == "Penicillin")["is_dangerous"])

        self.assertEqual(data["counts"]["allergies"], 2)
        self.assertEqual(data["counts"]["conditions"], 1)

        self.assertEqual(data["latest_vitals"]["id"], self.vitals.id)
        self.assertEqual(data["latest_vitals"]["recorded_by"], "nurse")
        self.assertIn(("Blood pressure", "130/85"), [(r["label"], r["value"]) for r in data["latest_vitals"]["readings"]])

        self.assertEqual(len(data["nursing_notes"]), 1)
        self.assertEqual(data["consultation_notes"][0]["diagnosis"], "Malaria")
        self.assertEqual(data["prescriptions"][0]["item"], "Artemether")
        self.assertEqual(data["prescriptions"][0]["status"], "pending")
        self.assertEqual(len(data["appointments"]), 1)

    def test_a_doctor_the_patient_is_not_assigned_to_gets_nothing(self):
        client = APIClient(); client.force_authenticate(self.other_doctor)
        self.assertEqual(client.get(f"/api/patients/{self.patient.id}/overview/").status_code, 404)

    def test_reception_and_nurses_cannot_read_the_full_chart(self):
        for user in (self.reception, self.nurse):
            client = APIClient(); client.force_authenticate(user)
            self.assertEqual(
                client.get(f"/api/patients/{self.patient.id}/overview/").status_code, 403, user.role
            )

    def test_the_billing_block_is_withheld_from_doctors(self):
        response = self.client.get(f"/api/patients/{self.patient.id}/overview/")
        self.assertNotIn("billing", response.data)
