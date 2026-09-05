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
    "/admin", "/admin/settings", "/admin/notifications", "/admin/:resource",
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


# Which roles each guarded route lets in — the mirror of `RequireAuth` in
# frontend/src/main.jsx. Only the guarded ones are listed; anything absent is
# open to every signed-in user. Admin passes everything, as it does in
# `hasRole`, so it is not repeated per row.
#
# This exists because "the route is defined" was never the whole question. A
# card pointing at a page the role's own guard bounces is as dead as one
# pointing at nothing — and that is exactly what happened when the inventory
# desk moved to Administration and the pharmacist's low-stock alert stayed
# aimed at /inventory.
ROUTE_ROLES = {
    "/inventory": {"inventory_manager"},
    "/pharmacy": {"pharmacist"},
    "/admin": set(),                     # admin only
    "/admin/settings": set(),
    "/admin/notifications": set(),
    "/admin/:resource": set(),
    "/departments": set(),
    "/users": set(),
    "/billing-items": {"cashier", "accountant"},
    "/laboratory": {"laboratory"},
    "/lab-catalogue": {"laboratory"},
    "/ultrasound": {"radiology"},
    "/eye": {"optometrist", "ophthalmologist"},
    "/vitals": {"nurse", "doctor"},
    "/queue": {"reception", "doctor", "nurse", "laboratory", "radiology",
               "optometrist", "ophthalmologist"},
}

ADMIN = {"admin", "hospital_admin"}


def normalise(url):
    """The route this URL matches, with ids and query strings stripped."""
    return re.sub(r"/\d+", "/:id", url.split("?")[0])


def routable(url):
    """Does this URL match a route, with ids and query strings stripped?"""
    return normalise(url) in FRONTEND_ROUTES


def reachable_by(url, role):
    """Would this role's own route guard let them onto the page?"""
    allowed = ROUTE_ROLES.get(normalise(url))
    if allowed is None:
        return True
    return role in ADMIN or role in allowed


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

    def test_every_card_opens_a_page_that_role_can_actually_open(self):
        """
        A link into a page the role's guard bounces is as dead as a link to
        nothing. The pharmacist's stock alert pointed at /inventory after the
        inventory desk moved to Administration; this is what catches that.
        """
        for role in ROLES:
            data = self._dashboard(role)
            for kind in ("cards", "alerts"):
                for row in data[kind]:
                    href = row.get("href")
                    if not href:
                        continue
                    with self.subTest(role=role, kind=kind, label=row.get("label")):
                        self.assertTrue(
                            reachable_by(href, role),
                            f"{role}'s '{row.get('label')}' links to {href!r}, "
                            f"which that role's own route guard refuses",
                        )

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


class StockAlertsLandInTheRightWorkspaceTests(TestCase):
    """
    The alerts only appear when there is something to warn about, so the
    general sweep above never sees them. This sets the condition up.

    Administration runs inventory from /inventory and the pharmacy works its
    own shelf from /pharmacy — a pharmacist can no longer open the inventory
    desk at all, so a stock alert aimed there would be a link into a refusal.
    """

    def setUp(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from apps.inventory.models import Batch, PHARMACY, StockLocation
        from apps.inventory.services import receive_stock
        from apps.inventory.testing import product

        keeper = User.objects.create_user(username="keeper", password="t",
                                          role="inventory_manager")
        # Low stock: a product the hospital holds none of.
        product("Paracetamol", unit_name="Tablet")
        # And something about to expire, sitting on the pharmacy shelf.
        expiring = product("Amoxicillin", unit_name="Capsule")
        batch = Batch.objects.create(
            item=expiring, batch_no="AMX-1", cost_price=Decimal("10"),
            sale_price=Decimal("20"),
            expiry_date=timezone.localdate() + timedelta(days=5))
        receive_stock(batch=batch, quantity=20, actor=keeper,
                      location=StockLocation.objects.get(code=PHARMACY))

    def _alerts(self, role):
        user = User.objects.create_user(username=f"{role}_alerts", password="t", role=role)
        client = APIClient(); client.force_authenticate(user)
        return client.get("/api/dashboard/").data["alerts"]

    def test_the_pharmacist_is_sent_to_their_own_shelf(self):
        alerts = self._alerts("pharmacist")
        self.assertTrue(alerts, "the pharmacist got no stock alerts to check")
        for alert in alerts:
            with self.subTest(alert=alert["label"]):
                self.assertEqual(normalise(alert["href"]), "/pharmacy")
                self.assertTrue(reachable_by(alert["href"], "pharmacist"))

    def test_the_store_keeper_is_sent_to_the_inventory_desk(self):
        alerts = self._alerts("inventory_manager")
        self.assertTrue(alerts, "the store keeper got no stock alerts to check")
        for alert in alerts:
            with self.subTest(alert=alert["label"]):
                self.assertEqual(normalise(alert["href"]), "/inventory")
                self.assertTrue(reachable_by(alert["href"], "inventory_manager"))
