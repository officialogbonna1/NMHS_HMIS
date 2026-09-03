"""
Telling the doctor a fresh reading has landed.

The hand-off says "this patient is coming to you". This is the other case:
a patient already on the doctor's list, whose figures have just changed.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.core.models import Notification
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class VitalsNotificationTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse",
                                              first_name="Ada", last_name="Bello")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.stranger = User.objects.create_user(username="doctor2", password="test", role="doctor")
        self.department = Department.objects.create(code="general-medicine", name="General Medicine")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.nurse_client = APIClient(); self.nurse_client.force_authenticate(self.nurse)

    def _record(self, **overrides):
        payload = {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(),
            "temperature_c": "38.4", "heart_rate": 104, "bp_systolic": 150, "bp_diastolic": 95,
            "bp_position": "", "bp_extremity": "", "glucose_time_of_day": "",
        }
        payload.update(overrides)
        return self.nurse_client.post("/api/vitals/", payload, format="json")

    def _notes(self, user):
        return Notification.objects.filter(recipient=user, category="clinical")

    def test_the_attending_doctor_is_told(self):
        Visit.objects.create(patient=self.patient, opened_by=self.reception, attending_doctor=self.doctor)
        self.assertEqual(self._record().status_code, 201)

        note = self._notes(self.doctor).first()
        self.assertIsNotNone(note, "the doctor holding this chart was not told")
        self.assertIn("New vitals", note.title)
        self.assertIn(str(self.patient), note.title)
        # The figures are in the message, so the doctor can triage from the list.
        self.assertIn("150/95", note.message)
        self.assertIn("Ada Bello", note.message)
        self.assertEqual(note.action_url, f"/patients/{self.patient.id}/vitals")

    def test_a_doctor_with_the_patient_queued_is_told(self):
        visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        PatientRoute.objects.create(visit=visit, department=self.department, purpose="consultation",
                                    assigned_to=self.doctor, routed_by=self.reception, status="queued")
        self._record()
        self.assertTrue(self._notes(self.doctor).exists())

    def test_a_doctor_with_an_open_appointment_is_told(self):
        Appointment.objects.create(patient=self.patient, doctor=self.doctor, reason="Fever")
        self._record()
        self.assertTrue(self._notes(self.doctor).exists())

    def test_a_doctor_the_patient_is_not_in_front_of_is_not_told(self):
        Visit.objects.create(patient=self.patient, opened_by=self.reception, attending_doctor=self.doctor)
        self._record()
        self.assertFalse(self._notes(self.stranger).exists())

    def test_finished_work_is_not_a_claim_on_the_doctors_attention(self):
        """A completed route is history — it must not keep pinging them."""
        visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        PatientRoute.objects.create(visit=visit, department=self.department, purpose="consultation",
                                    assigned_to=self.doctor, routed_by=self.reception, status="completed")
        self._record()
        self.assertFalse(self._notes(self.doctor).exists())

    def test_a_patient_with_no_doctor_yet_notifies_nobody(self):
        self.assertEqual(self._record().status_code, 201)
        self.assertFalse(Notification.objects.filter(category="clinical").exists())

    def test_a_disabled_doctor_is_not_notified(self):
        Visit.objects.create(patient=self.patient, opened_by=self.reception, attending_doctor=self.doctor)
        self.doctor.is_active = False
        self.doctor.save(update_fields=["is_active"])
        self._record()
        self.assertFalse(self._notes(self.doctor).exists())

    def test_each_reading_is_its_own_notification(self):
        Visit.objects.create(patient=self.patient, opened_by=self.reception, attending_doctor=self.doctor)
        self._record()
        self._record(temperature_c="37.1", heart_rate=88)
        self.assertEqual(self._notes(self.doctor).count(), 2)

    def test_the_link_lands_on_a_page_that_exists(self):
        Visit.objects.create(patient=self.patient, opened_by=self.reception, attending_doctor=self.doctor)
        self._record()
        doctor_client = APIClient(); doctor_client.force_authenticate(self.doctor)
        # The action_url is the chart's Vitals tab; the data behind it must load.
        response = doctor_client.get("/api/vitals/", {"patient": self.patient.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)


class UnreadCountTests(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.other = User.objects.create_user(username="doctor2", password="test", role="doctor")
        self.client = APIClient(); self.client.force_authenticate(self.doctor)

    def _make(self, recipient, count, read=False):
        for i in range(count):
            Notification.objects.create(recipient=recipient, title=f"n{i}", is_read=read)

    def test_it_counts_only_my_unread(self):
        self._make(self.doctor, 3)
        self._make(self.doctor, 2, read=True)
        self._make(self.other, 5)
        response = self.client.get("/api/notifications/unread-count/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"unread": 3})

    def test_marking_all_read_clears_the_badge(self):
        self._make(self.doctor, 4)
        self.client.post("/api/notifications/mark_all_read/")
        self.assertEqual(self.client.get("/api/notifications/unread-count/").data["unread"], 0)

    def test_it_needs_a_login(self):
        anon = APIClient()
        self.assertIn(anon.get("/api/notifications/unread-count/").status_code, (401, 403))
