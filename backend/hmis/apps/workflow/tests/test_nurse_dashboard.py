from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.clinical.models import Vitals, NursingNote
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class NurseDashboardTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.nursing = Department.objects.create(code="nursing", name="Nursing")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F", created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception, reason="Fever")
        self.client = APIClient(); self.client.force_authenticate(self.nurse)

    def _route(self, **kwargs):
        return PatientRoute.objects.create(
            visit=self.visit, department=self.nursing, assigned_to=self.nurse,
            purpose="vitals", routed_by=self.reception, **kwargs,
        )

    def cards(self):
        response = self.client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        return {card["key"]: card["value"] for card in response.data["cards"]}

    def test_the_nurse_dashboard_is_built_around_the_vitals_queue(self):
        self._route()
        self._route(status="in_progress")
        Vitals.objects.create(patient=self.patient, recorded_by=self.nurse, visit_time=timezone.now(), heart_rate=80)
        NursingNote.objects.create(patient=self.patient, nurse=self.nurse, observation="Stable.")

        cards = self.cards()
        self.assertEqual(cards["vitals_waiting"], 1)
        self.assertEqual(cards["vitals_in_progress"], 1)
        self.assertEqual(cards["vitals_today"], 1)
        self.assertEqual(cards["nursing_notes_today"], 1)
        # The generic "My queue → /patients" card is gone; every card lands
        # on the station the nurse actually works from — the two "today"
        # cards on its day list, since the live queue has by then emptied of
        # the very patients they are counting.
        response = self.client.get("/api/dashboard/")
        hrefs = {card["href"] for card in response.data["cards"]}
        self.assertEqual(hrefs, {"/vitals", "/vitals?tab=today", "/notifications"})
        by_key = {card["key"]: card["href"] for card in response.data["cards"]}
        self.assertEqual(by_key["vitals_waiting"], "/vitals")
        self.assertEqual(by_key["vitals_today"], "/vitals?tab=today")
        self.assertEqual(by_key["nursing_notes_today"], "/vitals?tab=today")

    def test_queue_tasks_carry_the_patient_and_point_at_the_vitals_station(self):
        self._route()
        response = self.client.get("/api/dashboard/")
        task = response.data["tasks"][0]
        self.assertEqual(task["patient"], "Doe, Jane")
        self.assertEqual(task["patient_id"], self.patient.id)
        self.assertEqual(task["purpose"], "Vitals")
        self.assertEqual(task["href"], "/vitals")
        # A staff account with no first/last name used to render as blank.
        self.assertEqual(task["assigned_to"], "nurse")

    def test_a_long_wait_raises_an_alert(self):
        route = self._route()
        self.assertEqual(self.client.get("/api/dashboard/").data["alerts"], [])

        PatientRoute.objects.filter(pk=route.pk).update(created_at=timezone.now() - timedelta(minutes=45))
        alerts = self.client.get("/api/dashboard/").data["alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("waiting over 30 minutes", alerts[0]["label"])
        self.assertEqual(alerts[0]["href"], "/vitals")

    def test_a_nurse_with_an_empty_queue_still_gets_a_dashboard(self):
        cards = self.cards()
        self.assertEqual(cards["vitals_waiting"], 0)
        self.assertEqual(self.client.get("/api/dashboard/").data["tasks"], [])
