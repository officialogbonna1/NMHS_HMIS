"""
Every dashboard card and alert has to open a page that exists.

The notification-link test guards `Notification.action_url`; nothing guarded
dashboard hrefs, and two of them pointed at routes that were never built —
the admin's "Active admissions" and the lab's "Investigations awaiting
work". Both landed on NotFound.

Keep FRONTEND_ROUTES in step with the <Route path=…> list in
frontend/src/main.jsx.
"""
import re

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User

FRONTEND_ROUTES = {
    "/", "/admissions", "/appointments", "/billing", "/billing-items", "/departments",
    "/eye", "/inventory", "/laboratory", "/lab-catalogue", "/login", "/notifications",
    "/nursing",
    "/patients", "/patients/new", "/pharmacy", "/queue", "/refer", "/send-to-doctor",
    "/transactions", "/ultrasound", "/users", "/vitals", "/outstanding", "/waivers",
    "/patients/:id", "/patients/:id/billing", "/patients/:id/notes",
    "/patients/:id/prescribe", "/patients/:id/record", "/patients/:id/vitals",
    "/patients/:id/lab", "/patients/:id/ultrasound", "/patients/:id/eye",
    "/patients/:id/procedure", "/patients/:id/admission", "/patients/:id/pharmacy",
}

# Roles whose dashboards must be reachable. Every role that can sign in gets
# cards, so every role gets checked.
ROLES = [
    "admin", "hospital_admin", "doctor", "nurse", "reception", "pharmacist",
    "laboratory", "radiology", "optometrist", "ophthalmologist", "cashier",
    "accountant", "ward_manager", "inventory_manager",
]


def routable(url):
    """Does this URL match a route, with ids and query strings stripped?"""
    return re.sub(r"/\d+", "/:id", url.split("?")[0]) in FRONTEND_ROUTES


class DashboardLinkTests(TestCase):
    def _dashboard(self, role):
        user = User.objects.create_user(username=f"{role}_user", password="test", role=role)
        client = APIClient(); client.force_authenticate(user)
        response = client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200, role)
        return response.data

    def test_every_card_on_every_dashboard_opens_a_real_page(self):
        for role in ROLES:
            data = self._dashboard(role)
            for card in data["cards"]:
                with self.subTest(role=role, card=card["label"]):
                    self.assertTrue(
                        routable(card["href"]),
                        f"{role}'s '{card['label']}' card links to {card['href']!r}, "
                        f"which matches no frontend route",
                    )

    def test_every_alert_opens_a_real_page(self):
        for role in ROLES:
            for alert in self._dashboard(role)["alerts"]:
                with self.subTest(role=role, alert=alert["label"]):
                    self.assertTrue(routable(alert["href"]), f"{role}: {alert['href']!r}")

    def test_every_queue_task_links_somewhere_real(self):
        for role in ROLES:
            for task in self._dashboard(role)["tasks"]:
                with self.subTest(role=role):
                    self.assertTrue(routable(task.get("href") or "/patients"))

    def test_the_lab_is_sent_to_its_own_station(self):
        cards = {c["key"]: c for c in self._dashboard("laboratory")["cards"] if "key" in c}
        self.assertEqual(cards["referrals_waiting"]["href"], "/laboratory")

    def test_imaging_and_the_eye_clinic_get_their_own_stations_too(self):
        for role, station in (("radiology", "/ultrasound"),
                              ("optometrist", "/eye"),
                              ("ophthalmologist", "/eye")):
            cards = {c["key"]: c for c in self._dashboard(role)["cards"] if "key" in c}
            self.assertIn("referrals_waiting", cards, role)
            self.assertEqual(cards["referrals_waiting"]["href"], station, role)

    def test_the_eye_roles_are_not_swallowed_by_the_doctor_branch(self):
        """They are clinicians and run a station; they need the station cards."""
        keys = {c.get("key") for c in self._dashboard("ophthalmologist")["cards"]}
        self.assertIn("referrals_waiting", keys)
