from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.patients.models import Patient
from apps.workflow.models import Visit


class DashboardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="test", role="admin")
        self.patient = Patient.objects.create(first_name="Admin", last_name="Patient", sex="F", created_by=self.admin)
        Visit.objects.create(patient=self.patient, opened_by=self.admin)

    def test_admin_gets_hospital_level_dashboard_cards(self):
        client = APIClient(); client.force_authenticate(self.admin)
        response = client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        labels = {card["label"] for card in response.data["cards"]}
        self.assertIn("Registered patients", labels)
        self.assertIn("Today’s collections", labels)

    def test_legacy_department_field_is_used_for_staff_queue(self):
        staff = User.objects.create_user(username="legacy-nurse", password="test", role="nurse", department="Nursing")
        client = APIClient(); client.force_authenticate(staff)
        response = client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_admin"])

    def test_superuser_assigned_nurse_role_gets_nurse_dashboard(self):
        nurse = User.objects.create_superuser(username="nurse-superuser", password="test", role="nurse")
        client = APIClient(); client.force_authenticate(nurse)
        response = client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_admin"])
        self.assertNotIn("Registered patients", {card["label"] for card in response.data["cards"]})
