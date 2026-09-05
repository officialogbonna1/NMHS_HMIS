"""
Every notification we raise carries an action_url the frontend has to be
able to route. A link to a page that no longer exists used to render a blank
screen; there is a catch-all now, but a dead link is still a dead end.

Keep FRONTEND_ROUTES in step with the <Route path=…> list in
frontend/src/main.jsx. If a page is renamed, this test names the
notification that has to move with it.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.core.models import Notification
from apps.departments.models import Department
from apps.inventory.testing import stock_the_pharmacy
from apps.inventory.models import Item, Batch
from apps.patients.models import Patient
from rest_framework.test import APIClient

FRONTEND_ROUTES = {
    "/", "/appointments", "/billing", "/billing-items", "/departments",
    "/inventory", "/login", "/notifications", "/nursing", "/patients",
    "/patients/new", "/pharmacy", "/queue", "/refer", "/send-to-doctor",
    "/transactions", "/users", "/vitals",
    "/laboratory", "/lab-catalogue", "/ultrasound", "/eye", "/admissions",
    "/outstanding", "/waivers",
    "/patients/:id", "/patients/:id/billing", "/patients/:id/notes",
    "/patients/:id/prescribe", "/patients/:id/record", "/patients/:id/vitals",
    "/patients/:id/lab", "/patients/:id/ultrasound", "/patients/:id/eye",
    "/patients/:id/procedure", "/patients/:id/admission", "/patients/:id/pharmacy",
}


def routable(url):
    """Does this URL match one of the app's routes, with ids substituted?"""
    return re.sub(r"/\d+", "/:id", url) in FRONTEND_ROUTES


class NotificationLinkTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.pharmacist = User.objects.create_user(username="pharmacist", password="test", role="pharmacist")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F", created_by=self.reception)
        self.nursing = Department.objects.create(code="nursing", name="Nursing")

    def _assert_all_routable(self):
        notifications = list(Notification.objects.all())
        self.assertTrue(notifications, "expected the action to raise a notification")
        for note in notifications:
            self.assertTrue(
                routable(note.action_url),
                f"{note.category} notification links to {note.action_url!r}, which matches no frontend route",
            )
        return notifications

    def test_routing_a_patient_links_somewhere_that_exists(self):
        client = APIClient(); client.force_authenticate(self.reception)
        visit = client.post("/api/visits/", {"patient": self.patient.id, "visit_type": "opd", "reason": "Fever"})
        client.post("/api/patient-routes/", {
            "visit": visit.data["id"], "department": self.nursing.id,
            "purpose": "vitals", "assigned_to": self.nurse.id,
        })
        notes = self._assert_all_routable()
        # A nurse is sent to their own station, not a chart they cannot open.
        self.assertEqual({n.action_url for n in notes}, {"/vitals"})

    def test_queueing_an_appointment_links_somewhere_that_exists(self):
        client = APIClient(); client.force_authenticate(self.reception)
        client.post("/api/appointments/", {
            "patient": self.patient.id, "doctor": self.doctor.id, "reason": "Fever",
        })
        self._assert_all_routable()

    def test_prescribing_and_dispensing_link_somewhere_that_exists(self):
        item = Item.objects.create(name="Paracetamol")
        stock_the_pharmacy(item=item, quantity=10, actor=self.doctor, expiry_days=60)
        Appointment.objects.create(patient=self.patient, doctor=self.doctor, reason="Fever")

        doctor_client = APIClient(); doctor_client.force_authenticate(self.doctor)
        created = doctor_client.post("/api/prescriptions/", {
            "patient": self.patient.id, "item": item.id, "quantity": 2,
        })
        self.assertEqual(created.status_code, 201)

        pharmacist_client = APIClient(); pharmacist_client.force_authenticate(self.pharmacist)
        pharmacist_client.post(f"/api/prescriptions/{created.data['id']}/dispense/")

        notes = self._assert_all_routable()
        self.assertIn("/pharmacy", {n.action_url for n in notes})

    def test_no_notification_in_the_database_points_at_a_dead_route(self):
        """Guards the stored rows, not just newly created ones."""
        for note in Notification.objects.exclude(action_url=""):
            self.assertTrue(routable(note.action_url), note.action_url)
