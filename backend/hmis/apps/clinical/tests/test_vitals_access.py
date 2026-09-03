from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.clinical.models import Vitals
from apps.patients.models import Patient
from apps.workflow.models import Visit


class VitalsAccessTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.other_doctor = User.objects.create_user(username="doctor2", password="test", role="doctor")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F", created_by=self.reception)

        self.nurse_client = APIClient(); self.nurse_client.force_authenticate(self.nurse)
        self.doctor_client = APIClient(); self.doctor_client.force_authenticate(self.doctor)

    def _take_vitals(self):
        return self.nurse_client.post("/api/vitals/", {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(),
            "temperature_c": "37.5", "heart_rate": 82, "bp_systolic": 120, "bp_diastolic": 80,
        })

    def test_a_nurse_can_take_vitals(self):
        response = self._take_vitals()
        self.assertEqual(response.status_code, 201)
        vitals = Vitals.objects.get(pk=response.data["id"])
        self.assertEqual(vitals.recorded_by, self.nurse)
        # Vitals lock on save — the nurse cannot go back and change them.
        self.assertTrue(vitals.is_locked)
        self.assertEqual(
            self.nurse_client.patch(f"/api/vitals/{vitals.id}/", {"heart_rate": 60}).status_code, 403
        )

    def test_a_doctor_sees_the_vitals_of_a_patient_queued_to_them(self):
        vitals_id = self._take_vitals().data["id"]
        # Nothing links this doctor to the patient yet.
        self.assertEqual(self.doctor_client.get(f"/api/vitals/{vitals_id}/").status_code, 404)

        Appointment.objects.create(patient=self.patient, doctor=self.doctor, reason="Fever")
        response = self.doctor_client.get("/api/vitals/", {"patient": self.patient.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([v["id"] for v in response.data["results"]], [vitals_id])
        self.assertEqual(response.data["results"][0]["heart_rate"], 82)

    def test_a_doctor_sees_the_vitals_of_their_attending_patient(self):
        vitals_id = self._take_vitals().data["id"]
        Visit.objects.create(patient=self.patient, attending_doctor=self.doctor, opened_by=self.reception)
        response = self.doctor_client.get("/api/vitals/", {"patient": self.patient.id})
        self.assertEqual([v["id"] for v in response.data["results"]], [vitals_id])

    def test_an_unrelated_doctor_sees_nothing(self):
        self._take_vitals()
        Appointment.objects.create(patient=self.patient, doctor=self.doctor, reason="Fever")
        other = APIClient(); other.force_authenticate(self.other_doctor)
        response = other.get("/api/vitals/", {"patient": self.patient.id})
        self.assertEqual(response.data["results"], [])

    def test_a_doctor_cannot_record_vitals(self):
        Appointment.objects.create(patient=self.patient, doctor=self.doctor, reason="Fever")
        response = self.doctor_client.post("/api/vitals/", {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(), "heart_rate": 70,
        })
        self.assertEqual(response.status_code, 403)
