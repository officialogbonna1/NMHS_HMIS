from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.patients.models import Patient
from apps.inpatient.models import Ward, Bed


class AdmissionApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.client = APIClient(); self.client.force_authenticate(self.user)
        self.patient = Patient.objects.create(first_name="Ada", last_name="Okafor", sex="F", created_by=self.user)
        self.bed = Bed.objects.create(ward=Ward.objects.create(name="Medical"), number="1")

    def test_bed_cannot_have_two_active_admissions(self):
        payload = {"patient": self.patient.id, "bed": self.bed.id}
        self.assertEqual(self.client.post("/api/admissions/", payload).status_code, 201)
        other = Patient.objects.create(first_name="Emeka", last_name="Okafor", sex="M", created_by=self.user)
        self.assertEqual(self.client.post("/api/admissions/", {"patient": other.id, "bed": self.bed.id}).status_code, 400)
