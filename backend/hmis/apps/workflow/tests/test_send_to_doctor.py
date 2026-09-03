"""
Nursing's hand-off desk: the side-nav page a nurse uses to queue a patient
for consultation, independent of any one row in their queue.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.models import Vitals
from apps.core.models import Notification
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class SendToDoctorTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor",
                                               first_name="Ada", last_name="Obi")
        self.other_doctor = User.objects.create_user(username="doctor2", password="test", role="doctor")
        self.department = Department.objects.create(code="general-medicine", name="General Medicine")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)

        self.nurse_client = APIClient(); self.nurse_client.force_authenticate(self.nurse)
        self.doctor_client = APIClient(); self.doctor_client.force_authenticate(self.doctor)

    def _visit(self):
        return Visit.objects.create(patient=self.patient, opened_by=self.reception)

    def _vitals_route(self, visit, **kwargs):
        return PatientRoute.objects.create(visit=visit, department=self.department, purpose="vitals",
                                           routed_by=self.reception, assigned_to=self.nurse, **kwargs)

    def _record_vitals(self):
        return Vitals.objects.create(patient=self.patient, recorded_by=self.nurse,
                                     visit_time=timezone.now(), heart_rate=88)

    def _send(self, **payload):
        return self.nurse_client.post("/api/patient-routes/send-to-doctor/",
                                      {"patient": self.patient.id, "doctor": self.doctor.id, **payload})

    def test_the_doctor_is_queued_made_attending_and_told(self):
        visit = self._visit()
        route = self._vitals_route(visit, status="in_progress")
        self._record_vitals()

        response = self._send(notes="Febrile since Tuesday", priority="urgent")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["purpose"], "consultation")
        self.assertEqual(response.data["assigned_to"], self.doctor.id)
        self.assertEqual(response.data["priority"], "urgent")

        # The nurse's own work is closed with it — nobody sits in two queues.
        route.refresh_from_db()
        self.assertEqual(route.status, "completed")

        # Attending is what opens the chart to the doctor.
        visit.refresh_from_db()
        self.assertEqual(visit.attending_doctor, self.doctor)

        note = Notification.objects.filter(recipient=self.doctor, category="routing").first()
        self.assertIsNotNone(note)
        self.assertIn("Queued for consultation", note.title)
        self.assertEqual(note.action_url, f"/patients/{self.patient.id}")

    def test_it_works_after_the_nurse_has_already_closed_their_route(self):
        """The gap `forward` cannot reach: the vitals route is already done."""
        visit = self._visit()
        self._vitals_route(visit, status="completed")
        self._record_vitals()

        self.assertEqual(self._send().status_code, 201)

    def test_the_note_the_nurse_writes_is_what_the_doctor_reads(self):
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        self._send(notes="BP high, patient is anxious")
        note = Notification.objects.get(recipient=self.doctor, category="routing")
        self.assertEqual(note.message, "BP high, patient is anxious")

    def test_with_no_note_the_doctor_is_still_told_who_sent_them(self):
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        self._send()
        note = Notification.objects.get(recipient=self.doctor, category="routing")
        self.assertIn("Nursing", note.message)

    def test_the_nurse_is_warned_when_there_are_no_vitals(self):
        """A question, not a wall — but nothing moves until it is answered."""
        self._vitals_route(self._visit())
        response = self._send()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "no_vitals")
        self.assertIn(str(self.patient), response.data["detail"])
        self.assertIn("anyway", response.data["detail"])
        self.assertFalse(PatientRoute.objects.filter(purpose="consultation").exists())

    def test_the_nurse_can_go_ahead_once_warned(self):
        self._vitals_route(self._visit())
        response = self._send(acknowledge_no_vitals=True)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("No vitals recorded", response.data["notes"])

    def test_going_ahead_tells_the_doctor_what_is_missing(self):
        self._vitals_route(self._visit())
        self._send(acknowledge_no_vitals=True, notes="Patient too distressed for a reading")
        note = Notification.objects.get(recipient=self.doctor, category="routing")
        self.assertIn("No vitals", note.title)
        self.assertIn("No vitals recorded", note.message)
        self.assertIn("too distressed", note.message)

    def test_the_acknowledgement_is_not_needed_when_the_vitals_are_there(self):
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        response = self._send()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("No vitals recorded", response.data["notes"])
        note = Notification.objects.get(recipient=self.doctor, category="routing")
        self.assertNotIn("No vitals", note.title)

    def test_acknowledging_something_else_does_not_count(self):
        self._vitals_route(self._visit())
        self.assertEqual(self._send(acknowledge_no_vitals=False).status_code, 400)
        self.assertEqual(self._send(acknowledge_no_vitals="").status_code, 400)

    def test_a_patient_with_no_open_visit_cannot_be_sent(self):
        self._record_vitals()
        response = self._send()
        self.assertEqual(response.status_code, 400)
        self.assertIn("no open visit", response.data["detail"])

    def test_last_visits_vitals_do_not_count_for_this_one(self):
        self._record_vitals()          # taken during a previous attendance
        visit = self._visit()          # …then the patient comes back
        self._vitals_route(visit)
        response = self._send()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "no_vitals")

    def test_sending_to_the_same_doctor_twice_is_refused(self):
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        self.assertEqual(self._send().status_code, 201)

        again = self._send()
        self.assertEqual(again.status_code, 409)
        self.assertIn("already queued", again.data["detail"])
        self.assertEqual(PatientRoute.objects.filter(purpose="consultation").count(), 1)

    def test_moving_to_another_doctor_asks_first(self):
        """The doctor who saw them is off — the nurse is asked, not blocked."""
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        first = self._send()

        moved = self.nurse_client.post("/api/patient-routes/send-to-doctor/",
                                       {"patient": self.patient.id, "doctor": self.other_doctor.id})
        self.assertEqual(moved.status_code, 400)
        self.assertEqual(moved.data["code"], "reassign")
        self.assertEqual(moved.data["current_doctor"], "Ada Obi")
        # Nothing has moved until the question is answered.
        self.assertEqual(PatientRoute.objects.get(pk=first.data["id"]).status, "queued")

    def test_moving_the_patient_cancels_the_old_consultation_and_moves_the_chart(self):
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        first = self._send()

        moved = self.nurse_client.post("/api/patient-routes/send-to-doctor/", {
            "patient": self.patient.id, "doctor": self.other_doctor.id,
            "acknowledge_reassign": True,
        })
        self.assertEqual(moved.status_code, 201, moved.data)

        # Cancelled, not completed: the first doctor never saw the patient.
        self.assertEqual(PatientRoute.objects.get(pk=first.data["id"]).status, "cancelled")
        self.assertEqual(PatientRoute.objects.get(pk=moved.data["id"]).assigned_to, self.other_doctor)

        # Attending moves too, or the doctor who is off keeps the chart.
        visit.refresh_from_db()
        self.assertEqual(visit.attending_doctor, self.other_doctor)

    def test_the_doctor_who_loses_the_patient_is_told(self):
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        self._send()
        self.nurse_client.post("/api/patient-routes/send-to-doctor/", {
            "patient": self.patient.id, "doctor": self.other_doctor.id,
            "acknowledge_reassign": True,
        })
        note = Notification.objects.filter(recipient=self.doctor, title__startswith="Reassigned").first()
        self.assertIsNotNone(note, "the first doctor was not told the patient had moved")
        self.assertIn(self.other_doctor.username, note.message)

    def test_waving_past_no_vitals_is_not_consent_to_reassign(self):
        """Each question is answered by name."""
        visit = self._visit()
        self._vitals_route(visit)
        self._send(acknowledge_no_vitals=True)   # queued with the first doctor

        response = self.nurse_client.post("/api/patient-routes/send-to-doctor/", {
            "patient": self.patient.id, "doctor": self.other_doctor.id,
            "acknowledge_no_vitals": True,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "reassign")

    def test_it_needs_a_real_doctor(self):
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        for bad in ({"doctor": self.nurse.id}, {"doctor": ""}, {}):
            response = self.nurse_client.post("/api/patient-routes/send-to-doctor/",
                                              {"patient": self.patient.id, **bad})
            self.assertEqual(response.status_code, 400, bad)

    def test_only_nursing_sends_from_here(self):
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        reception_client = APIClient(); reception_client.force_authenticate(self.reception)
        for who, client in (("doctor", self.doctor_client), ("reception", reception_client)):
            response = client.post("/api/patient-routes/send-to-doctor/",
                                   {"patient": self.patient.id, "doctor": self.doctor.id})
            self.assertEqual(response.status_code, 403, who)
        self.assertFalse(PatientRoute.objects.filter(purpose="consultation").exists())

    def test_the_doctor_can_then_start_and_finish_the_consultation(self):
        visit = self._visit()
        self._vitals_route(visit)
        self._record_vitals()
        route_id = self._send().data["id"]

        started = self.doctor_client.post(f"/api/patient-routes/{route_id}/start/")
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.data["status"], "in_progress")

        # Completing a consultation is not gated on vitals — that rule is the
        # nurse's, and the reading is already on the chart by now.
        done = self.doctor_client.post(f"/api/patient-routes/{route_id}/complete/")
        self.assertEqual(done.status_code, 200)
        self.assertEqual(PatientRoute.objects.get(pk=route_id).status, "completed")
