from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.clinical.models import Vitals
from apps.core.models import Notification
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class NurseHandoffTests(TestCase):
    """Reception sends a patient for vitals → a nurse is told, accepts, takes
    the vitals, and passes the patient to a doctor."""

    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        # Deliberately not a member of any department, and with no legacy
        # `department` string — the setup that used to make routes invisible.
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.other_nurse = User.objects.create_user(username="nurse2", password="test", role="nurse")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.department = Department.objects.create(code="general-medicine", name="General Medicine")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F", created_by=self.reception)

        self.reception_client = APIClient(); self.reception_client.force_authenticate(self.reception)
        self.nurse_client = APIClient(); self.nurse_client.force_authenticate(self.nurse)
        self.doctor_client = APIClient(); self.doctor_client.force_authenticate(self.doctor)

    def _route_for_vitals(self, assigned_to=None):
        visit = self.reception_client.post("/api/visits/", {
            "patient": self.patient.id, "visit_type": "opd", "reason": "Fever",
        })
        payload = {"visit": visit.data["id"], "department": self.department.id,
                   "purpose": "vitals", "priority": "routine"}
        if assigned_to:
            payload["assigned_to"] = assigned_to.id
        response = self.reception_client.post("/api/patient-routes/", payload)
        self.assertEqual(response.status_code, 201)
        return response.data["id"]

    def _queue(self, client):
        return [r["id"] for r in client.get("/api/patient-routes/").data["results"]]

    def test_sending_to_anyone_reaches_every_nurse(self):
        route_id = self._route_for_vitals()
        # Neither nurse is in the department's staff list, and neither has the
        # legacy department field set — they must still see the work.
        self.assertEqual(self._queue(self.nurse_client), [route_id])
        other = APIClient(); other.force_authenticate(self.other_nurse)
        self.assertEqual(self._queue(other), [route_id])

        for nurse in (self.nurse, self.other_nurse):
            note = Notification.objects.filter(recipient=nurse, category="routing").first()
            self.assertIsNotNone(note, f"{nurse.username} was not notified")
            self.assertEqual(note.action_url, "/vitals")
            self.assertIn("Vitals requested", note.title)

    def test_a_vitals_request_does_not_land_in_a_doctors_queue(self):
        # The doctor is a member of the department the patient was routed to,
        # which used to be enough to put nursing work in their queue.
        self.department.staff.add(self.doctor)
        route_id = self._route_for_vitals()
        self.assertEqual(self._queue(self.doctor_client), [])
        self.assertFalse(Notification.objects.filter(recipient=self.doctor).exists())
        self.assertEqual(self._queue(self.nurse_client), [route_id])

    def test_sending_to_a_particular_nurse_reaches_only_them(self):
        route_id = self._route_for_vitals(assigned_to=self.nurse)
        self.assertEqual(self._queue(self.nurse_client), [route_id])
        other = APIClient(); other.force_authenticate(self.other_nurse)
        self.assertEqual(self._queue(other), [])
        self.assertFalse(Notification.objects.filter(recipient=self.other_nurse).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.nurse).exists())

    def test_accepting_claims_the_patient_from_the_other_nurses(self):
        route_id = self._route_for_vitals()
        response = self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "in_progress")
        self.assertEqual(response.data["assigned_to"], self.nurse.id)

        other = APIClient(); other.force_authenticate(self.other_nurse)
        self.assertEqual(self._queue(other), [])
        self.assertEqual(self._queue(self.nurse_client), [route_id])

    def test_a_second_nurse_cannot_take_an_accepted_patient(self):
        route_id = self._route_for_vitals()
        self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        other = APIClient(); other.force_authenticate(self.other_nurse)
        response = other.post(f"/api/patient-routes/{route_id}/accept/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("already accepted", response.data["detail"])
        self.assertEqual(PatientRoute.objects.get(pk=route_id).assigned_to, self.nurse)

    def test_reception_is_told_who_accepted(self):
        route_id = self._route_for_vitals()
        self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        note = Notification.objects.filter(recipient=self.reception).first()
        self.assertIsNotNone(note)
        self.assertIn("accepted", note.title)

    def test_the_nurse_is_warned_before_sending_with_no_vitals(self):
        """Not a wall — a question. Nothing moves until they answer it."""
        route_id = self._route_for_vitals()
        self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        response = self.nurse_client.post(f"/api/patient-routes/{route_id}/forward/", {"doctor": self.doctor.id})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "no_vitals")
        self.assertIn("anyway", response.data["detail"])
        self.assertEqual(PatientRoute.objects.get(pk=route_id).status, "in_progress")
        self.assertFalse(PatientRoute.objects.filter(purpose="consultation").exists())

    def test_the_nurse_can_send_anyway_and_the_doctor_is_told_what_is_missing(self):
        route_id = self._route_for_vitals()
        self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        response = self.nurse_client.post(f"/api/patient-routes/{route_id}/forward/", {
            "doctor": self.doctor.id, "acknowledge_no_vitals": True,
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("No vitals recorded", response.data["notes"])

        note = Notification.objects.filter(recipient=self.doctor, category="routing").first()
        self.assertIsNotNone(note)
        self.assertIn("No vitals", note.title)

    def test_a_vitals_route_cannot_be_marked_done_with_nothing_recorded(self):
        route_id = self._route_for_vitals()
        self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        response = self.nurse_client.post(f"/api/patient-routes/{route_id}/complete/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("vitals before", response.data["detail"])
        self.assertEqual(PatientRoute.objects.get(pk=route_id).status, "in_progress")

    def test_last_visits_vitals_do_not_count_for_this_one(self):
        # A reading taken before reception raised the route belongs to the
        # patient's previous attendance, not this one.
        Vitals.objects.create(patient=self.patient, recorded_by=self.nurse,
                              visit_time=timezone.now(), heart_rate=70)
        route_id = self._route_for_vitals()
        self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        self.assertEqual(
            self.nurse_client.post(f"/api/patient-routes/{route_id}/complete/").status_code, 400)
        self.assertEqual(
            self.nurse_client.post(f"/api/patient-routes/{route_id}/forward/",
                                   {"doctor": self.doctor.id}).status_code, 400)

    def test_marking_done_works_once_the_vitals_are_in(self):
        route_id = self._route_for_vitals()
        self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        self.nurse_client.post("/api/vitals/", {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(),
            "temperature_c": "38.2", "heart_rate": 96,
        })
        self.assertEqual(
            self.nurse_client.post(f"/api/patient-routes/{route_id}/complete/").status_code, 200)
        self.assertEqual(PatientRoute.objects.get(pk=route_id).status, "completed")

    def test_forwarding_hands_the_patient_and_the_chart_to_the_doctor(self):
        route_id = self._route_for_vitals()
        self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        self.nurse_client.post("/api/vitals/", {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(),
            "temperature_c": "38.2", "heart_rate": 96,
        })

        response = self.nurse_client.post(f"/api/patient-routes/{route_id}/forward/", {
            "doctor": self.doctor.id, "notes": "Febrile, complains of headache",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["purpose"], "consultation")
        self.assertEqual(response.data["assigned_to"], self.doctor.id)

        # The nurse's own work is closed and off their queue.
        self.assertEqual(PatientRoute.objects.get(pk=route_id).status, "completed")
        self.assertEqual(self._queue(self.nurse_client), [])

        # The doctor is now attending, which is what opens the chart.
        visit = Visit.objects.get(patient=self.patient)
        self.assertEqual(visit.attending_doctor, self.doctor)
        self.assertEqual(self.doctor_client.get(f"/api/patients/{self.patient.id}/overview/").status_code, 200)
        vitals = self.doctor_client.get("/api/vitals/", {"patient": self.patient.id}).data["results"]
        self.assertEqual(len(vitals), 1)

        note = Notification.objects.filter(recipient=self.doctor, category="routing").first()
        self.assertIsNotNone(note)
        # Both hand-off paths run through _queue_consultation now, so the
        # doctor gets one wording whichever way the patient reached them.
        self.assertIn("Queued for consultation", note.title)
        self.assertIn(str(self.patient), note.title)
        self.assertEqual(note.message, "Febrile, complains of headache")
        self.assertEqual(note.action_url, f"/patients/{self.patient.id}")

    def test_only_a_nurse_can_forward(self):
        route_id = self._route_for_vitals()
        self.assertEqual(
            self.reception_client.post(f"/api/patient-routes/{route_id}/forward/", {"doctor": self.doctor.id}).status_code,
            403,
        )

    def test_forwarding_needs_a_real_doctor(self):
        route_id = self._route_for_vitals()
        self.nurse_client.post(f"/api/patient-routes/{route_id}/accept/")
        Vitals.objects.create(patient=self.patient, recorded_by=self.nurse, visit_time=timezone.now(), heart_rate=80)
        for bad in ({}, {"doctor": self.other_nurse.id}, {"doctor": 9999}):
            response = self.nurse_client.post(f"/api/patient-routes/{route_id}/forward/", bad)
            self.assertEqual(response.status_code, 400, bad)
