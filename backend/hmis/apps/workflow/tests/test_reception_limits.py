from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class ReceptionBoundaryTests(TestCase):
    """Reception queues work and can call it off. Saying the work happened
    belongs to whoever did it."""

    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F", created_by=self.reception)
        self.nursing = Department.objects.create(code="nursing", name="Nursing")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        self.route = PatientRoute.objects.create(
            visit=self.visit, department=self.nursing, assigned_to=self.nurse,
            purpose="vitals", routed_by=self.reception,
        )
        self.client = APIClient(); self.client.force_authenticate(self.reception)

    def test_reception_cannot_start_or_complete_a_route(self):
        for action in ("start", "complete"):
            response = self.client.post(f"/api/patient-routes/{self.route.id}/{action}/")
            self.assertEqual(response.status_code, 403, action)
        self.route.refresh_from_db()
        self.assertEqual(self.route.status, "queued")

    def test_reception_cannot_write_the_status_directly_either(self):
        response = self.client.patch(f"/api/patient-routes/{self.route.id}/", {"status": "completed"})
        self.assertEqual(response.status_code, 200)  # the write is accepted…
        self.route.refresh_from_db()
        self.assertEqual(self.route.status, "queued")  # …but status is read-only

    def test_reception_can_call_a_visit_off(self):
        response = self.client.post(f"/api/patient-routes/{self.route.id}/cancel/")
        self.assertEqual(response.status_code, 200)
        self.route.refresh_from_db()
        self.assertEqual(self.route.status, "cancelled")

    def test_the_nurse_it_was_sent_to_still_completes_it(self):
        client = APIClient(); client.force_authenticate(self.nurse)
        client.post("/api/vitals/", {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(),
            "temperature_c": "37.1", "heart_rate": 80,
        })
        self.assertEqual(client.post(f"/api/patient-routes/{self.route.id}/complete/").status_code, 200)
        self.route.refresh_from_db()
        self.assertEqual(self.route.status, "completed")

    def test_reception_queues_appointments_but_never_closes_them(self):
        created = self.client.post("/api/appointments/", {
            "patient": self.patient.id, "doctor": self.doctor.id, "reason": "Fever",
        })
        self.assertEqual(created.status_code, 201)
        appointment_id = created.data["id"]

        for action in ("accept", "start", "end", "cancel"):
            self.assertEqual(
                self.client.post(f"/api/appointments/{appointment_id}/{action}/").status_code, 403, action,
            )
        self.assertEqual(Appointment.objects.get(pk=appointment_id).status, "queued")
