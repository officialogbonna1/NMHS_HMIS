"""
An admin can read every notification in the system. Two things must stay
true anyway:

* their own bell still counts only their own — a badge showing the whole
  hospital's traffic never clears and stops meaning anything;
* reading everyone's mail is not the same as answering it, so an admin
  cannot mark somebody else's notification read, and "mark all as read"
  clears their own inbox and nobody else's.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Notification
from apps.core.services import notify


class NotificationScopeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="t", role="admin")
        self.doctor = User.objects.create_user(username="dera", password="t", role="doctor")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")

        notify(recipient=self.doctor, title="Laboratory result: FBC", category="clinical")
        notify(recipient=self.lab, title="Laboratory requested: Nwosu", category="routing")
        notify(recipient=self.lab, title="Laboratory requested: Obi", category="routing")
        notify(recipient=self.admin, title="Something for the admin", category="general")
        self.client = APIClient()

    def _titles(self, response):
        rows = response.data["results"] if "results" in response.data else response.data
        return [r["title"] for r in rows]

    # --- the admin's whole-system view ---------------------------------

    def test_an_admin_sees_every_notification_in_the_system(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/notifications/", {"scope": "all"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._titles(response)), 4)

    def test_each_row_says_whose_it_was(self):
        self.client.force_authenticate(self.admin)
        rows = self.client.get("/api/notifications/", {"scope": "all"}).data
        rows = rows["results"] if "results" in rows else rows
        lab_row = next(r for r in rows if r["title"] == "Laboratory requested: Obi")
        self.assertEqual(lab_row["recipient_name"], "lab")
        self.assertEqual(lab_row["recipient_role"], "laboratory")

    def test_the_admins_own_list_is_still_only_theirs(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/notifications/")
        self.assertEqual(self._titles(response), ["Something for the admin"])

    def test_the_whole_system_view_can_be_filtered_and_searched(self):
        self.client.force_authenticate(self.admin)
        by_category = self.client.get("/api/notifications/",
                                      {"scope": "all", "category": "routing"})
        self.assertEqual(len(self._titles(by_category)), 2)

        by_person = self.client.get("/api/notifications/", {"scope": "all", "search": "dera"})
        self.assertEqual(self._titles(by_person), ["Laboratory result: FBC"])

        by_recipient = self.client.get("/api/notifications/",
                                       {"scope": "all", "recipient": self.lab.pk})
        self.assertEqual(len(self._titles(by_recipient)), 2)

    def test_the_overview_counts_by_person_and_category(self):
        self.client.force_authenticate(self.admin)
        data = self.client.get("/api/notifications/overview/").data
        self.assertEqual(data["total"], 4)
        self.assertEqual(data["unread"], 4)
        lab = next(p for p in data["people"] if p["username"] == "lab")
        self.assertEqual((lab["total"], lab["unread"], lab["role"]), (2, 2, "laboratory"))
        routing = next(c for c in data["categories"] if c["category"] == "routing")
        self.assertEqual(routing["total"], 2)

    # --- the boundaries -------------------------------------------------

    def test_a_doctor_asking_for_everything_is_refused(self):
        self.client.force_authenticate(self.doctor)
        response = self.client.get("/api/notifications/", {"scope": "all"})
        self.assertEqual(response.status_code, 403)

    def test_a_doctor_still_sees_only_their_own(self):
        self.client.force_authenticate(self.doctor)
        self.assertEqual(self._titles(self.client.get("/api/notifications/")),
                         ["Laboratory result: FBC"])

    def test_only_an_admin_can_read_the_overview(self):
        self.client.force_authenticate(self.lab)
        self.assertEqual(self.client.get("/api/notifications/overview/").status_code, 403)

    def test_the_admins_bell_counts_only_their_own(self):
        """Otherwise the badge reads 4, never clears, and stops being read."""
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get("/api/notifications/unread-count/").data["unread"], 1)

    def test_mark_all_read_clears_the_admins_inbox_and_nobody_elses(self):
        self.client.force_authenticate(self.admin)
        self.client.post("/api/notifications/mark_all_read/")
        self.assertFalse(Notification.objects.filter(recipient=self.admin, is_read=False).exists())
        self.assertEqual(Notification.objects.filter(is_read=False).count(), 3)

    def test_an_admin_cannot_mark_somebody_elses_notification_read(self):
        theirs = Notification.objects.filter(recipient=self.lab).first()
        self.client.force_authenticate(self.admin)
        response = self.client.patch(f"/api/notifications/{theirs.pk}/",
                                     {"is_read": True}, format="json")
        self.assertEqual(response.status_code, 403)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)

    def test_an_admin_can_still_mark_their_own_read(self):
        mine = Notification.objects.get(recipient=self.admin)
        self.client.force_authenticate(self.admin)
        response = self.client.patch(f"/api/notifications/{mine.pk}/",
                                     {"is_read": True}, format="json")
        self.assertEqual(response.status_code, 200)
        mine.refresh_from_db()
        self.assertTrue(mine.is_read)

    def test_nobody_can_post_a_notification_through_the_api(self):
        """They are raised server-side by core.services.notify(), always."""
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/notifications/", {
            "recipient": self.doctor.pk, "title": "Fake"}, format="json")
        self.assertEqual(response.status_code, 405)
