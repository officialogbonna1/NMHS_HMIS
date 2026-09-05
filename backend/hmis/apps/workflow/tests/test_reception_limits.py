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


class ReceptionSeesOnlyItsOwnRoutingTests(TestCase):
    """
    The front desk's queue is the work the front desk raised.

    It used to be every route in the hospital, so the reception screen showed
    the clinical routing: a doctor's laboratory referral reading "Do malaria
    test", a nurse's hand-off reading "No vitals recorded for this visit".
    Those notes are one clinician writing to another — the chart, not the
    desk's queue.
    """

    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.other_reception = User.objects.create_user(username="rec2", password="t",
                                                        role="reception")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nurse", password="t", role="nurse")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")

        self.department = Department.objects.create(code="general", name="General Medicine")
        self.patient = Patient.objects.create(first_name="Sunday", last_name="Ogbonna",
                                              sex="M", created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)

        # What the desk raised: a patient sent for vitals.
        self.desk_route = PatientRoute.objects.create(
            visit=self.visit, department=self.department, purpose="vitals",
            routed_by=self.reception, notes="Walk-in, please take vitals.")
        # What the clinicians raised, which the desk was seeing.
        self.lab_referral = PatientRoute.objects.create(
            visit=self.visit, department=self.department, purpose="laboratory",
            routed_by=self.doctor, notes="Do malaria test")
        self.handoff = PatientRoute.objects.create(
            visit=self.visit, department=self.department, purpose="consultation",
            routed_by=self.nurse, notes="No vitals recorded for this visit.")

        self.client = APIClient()
        self.client.force_authenticate(self.reception)

    def _queue(self, client):
        data = client.get("/api/patient-routes/").data
        return {row["id"] for row in data.get("results", data)}

    def test_the_desk_sees_the_work_it_raised(self):
        self.assertEqual(self._queue(self.client), {self.desk_route.id})

    def test_a_doctors_referral_never_reaches_the_front_desk_queue(self):
        visible = self._queue(self.client)
        self.assertNotIn(self.lab_referral.id, visible)
        self.assertNotIn(self.handoff.id, visible)

    def test_the_clinical_note_does_not_come_back_in_the_payload_either(self):
        """Not merely filtered out of a list — the text is not in the response."""
        body = str(self.client.get("/api/patient-routes/").data)
        self.assertNotIn("Do malaria test", body)
        self.assertNotIn("No vitals recorded", body)

    def test_the_desk_is_shared_so_a_colleagues_route_is_still_workable(self):
        """A route raised on the morning shift has to be cancellable in the
        afternoon, so the boundary is the desk rather than one person."""
        colleague_route = PatientRoute.objects.create(
            visit=self.visit, department=self.department, purpose="vitals",
            routed_by=self.other_reception, notes="Second walk-in.")
        self.assertIn(colleague_route.id, self._queue(self.client))

    def test_reception_cannot_cancel_a_referral_it_cannot_see(self):
        response = self.client.post(f"/api/patient-routes/{self.lab_referral.id}/cancel/")
        self.assertEqual(response.status_code, 404)
        self.lab_referral.refresh_from_db()
        self.assertEqual(self.lab_referral.status, "queued")

    def test_reception_still_cancels_its_own(self):
        response = self.client.post(f"/api/patient-routes/{self.desk_route.id}/cancel/")
        self.assertEqual(response.status_code, 200, response.data)
        self.desk_route.refresh_from_db()
        self.assertEqual(self.desk_route.status, "cancelled")

    def test_the_units_and_the_admin_are_unaffected(self):
        """The change is reception's view only — nobody else loses work."""
        lab_client = APIClient(); lab_client.force_authenticate(self.lab)
        self.assertIn(self.lab_referral.id, self._queue(lab_client))

        admin_client = APIClient(); admin_client.force_authenticate(self.admin)
        self.assertEqual(
            self._queue(admin_client),
            {self.desk_route.id, self.lab_referral.id, self.handoff.id})

    def test_the_desks_dashboard_counts_only_its_own_queue(self):
        cards = self.client.get("/api/dashboard/").data["cards"]
        queue_card = next((c for c in cards if c.get("key") == "queue"
                           or "queue" in c["label"].lower()), None)
        if queue_card is not None:
            self.assertEqual(queue_card["value"], 1)
