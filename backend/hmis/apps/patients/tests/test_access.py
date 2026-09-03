from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.patients.models import Patient


class DepartmentIsolationTests(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M", short_note="restricted", created_by=self.doctor)

    def test_reception_receives_demographic_patient_payload_only(self):
        client = APIClient(); client.force_authenticate(self.reception)
        response = client.get(f"/api/patients/{self.patient.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("short_note", response.data)

    def test_reception_cannot_read_clinical_health_record_tiles(self):
        client = APIClient(); client.force_authenticate(self.reception)
        self.assertEqual(client.get("/api/allergies/").status_code, 403)

    def test_reception_is_denied_vitals_notes_and_prescriptions(self):
        client = APIClient(); client.force_authenticate(self.reception)
        self.assertEqual(client.get("/api/vitals/").status_code, 403)
        self.assertEqual(client.get("/api/notes/").status_code, 403)
        self.assertEqual(client.get("/api/prescriptions/").status_code, 403)
