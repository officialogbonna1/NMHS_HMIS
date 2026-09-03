from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.patients.models import Patient


class AppointmentQueueTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.other_doctor = User.objects.create_user(username="doctor2", password="test", role="doctor")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F", created_by=self.reception)
        self.reception_client = APIClient(); self.reception_client.force_authenticate(self.reception)
        self.doctor_client = APIClient(); self.doctor_client.force_authenticate(self.doctor)

    def _queue(self):
        return self.reception_client.post("/api/appointments/", {
            "patient": self.patient.id, "doctor": self.doctor.id, "reason": "Fever",
        })

    def test_reception_queues_without_supplying_a_time(self):
        response = self._queue()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "queued")
        self.assertIsNone(response.data["start_time"])
        self.assertIsNone(response.data["end_time"])

    def test_reception_cannot_set_the_appointment_time(self):
        response = self.reception_client.post("/api/appointments/", {
            "patient": self.patient.id, "doctor": self.doctor.id, "reason": "Fever",
            "start_time": "2026-01-01T09:00:00Z", "end_time": "2026-01-01T09:30:00Z",
        })
        self.assertEqual(response.status_code, 201)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertIsNone(appointment.start_time)
        self.assertIsNone(appointment.end_time)

    def test_reception_cannot_start_an_appointment(self):
        appointment_id = self._queue().data["id"]
        for transition in ("accept", "start", "end", "cancel"):
            response = self.reception_client.post(f"/api/appointments/{appointment_id}/{transition}/")
            self.assertEqual(response.status_code, 403, transition)
        self.assertEqual(Appointment.objects.get(pk=appointment_id).status, "queued")

    def test_the_doctor_starting_stamps_the_real_time(self):
        appointment_id = self._queue().data["id"]
        self.doctor_client.post(f"/api/appointments/{appointment_id}/accept/")
        response = self.doctor_client.post(f"/api/appointments/{appointment_id}/start/")
        self.assertEqual(response.status_code, 200)
        appointment = Appointment.objects.get(pk=appointment_id)
        self.assertEqual(appointment.status, "in_progress")
        self.assertIsNotNone(appointment.start_time)
        self.assertIsNone(appointment.end_time)

        response = self.doctor_client.post(f"/api/appointments/{appointment_id}/end/")
        self.assertEqual(response.status_code, 200)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "completed")
        self.assertIsNotNone(appointment.end_time)

    def test_a_doctor_cannot_start_someone_elses_appointment(self):
        appointment_id = self._queue().data["id"]
        self.doctor_client.post(f"/api/appointments/{appointment_id}/accept/")
        other = APIClient(); other.force_authenticate(self.other_doctor)
        self.assertEqual(other.post(f"/api/appointments/{appointment_id}/start/").status_code, 404)

    def test_the_same_patient_is_not_queued_twice_to_one_doctor(self):
        self.assertEqual(self._queue().status_code, 201)
        response = self._queue()
        self.assertEqual(response.status_code, 400)
        self.assertIn("already in", response.data["detail"])
