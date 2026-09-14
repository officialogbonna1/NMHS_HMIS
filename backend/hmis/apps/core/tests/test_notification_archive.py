"""
Archiving notifications — how one leaves an inbox without leaving the database.

There is no delete, by design: not over the API, not from Django admin. What a
member of staff was told, and when, is part of how a patient's care was handed
about, so a notification is archived instead — out of the inbox and off the
bell, still stored, readable under Archived, and restorable.

The boundaries `test_notification_scope.py` draws for reading hold for
archiving too: only your own, and "archive all" is your inbox and nobody
else's.
"""
from django.contrib import admin
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Notification
from apps.core.services import notify


class ArchivingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="t", role="admin")
        self.doctor = User.objects.create_user(username="dera", password="t", role="doctor")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.result = notify(recipient=self.doctor, title="Laboratory result: FBC",
                             category="clinical")
        self.referral = notify(recipient=self.doctor, title="Referral accepted",
                               category="routing")
        self.vitals = notify(recipient=self.doctor, title="Vitals recorded", category="clinical")
        self.labs = notify(recipient=self.lab, title="Laboratory requested: Obi",
                           category="routing")
        self.client = APIClient()
        self.client.force_authenticate(self.doctor)

    def _titles(self, params=None):
        response = self.client.get("/api/notifications/", params or {})
        self.assertEqual(response.status_code, 200)
        rows = response.data["results"] if "results" in response.data else response.data
        return [row["title"] for row in rows]

    def _archive(self, notification):
        return self.client.post(f"/api/notifications/{notification.pk}/archive/")

    # --- one at a time -------------------------------------------------

    def test_archiving_takes_it_out_of_the_inbox_and_keeps_it(self):
        response = self._archive(self.result)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_archived"])
        self.assertNotIn("Laboratory result: FBC", self._titles())
        self.assertEqual(self._titles({"archived": "true"}), ["Laboratory result: FBC"])
        self.result.refresh_from_db()
        self.assertIsNotNone(self.result.archived_at)

    def test_archiving_twice_keeps_the_first_time(self):
        self._archive(self.result)
        self.result.refresh_from_db()
        first = self.result.archived_at
        self.assertEqual(self._archive(self.result).status_code, 200)
        self.result.refresh_from_db()
        self.assertEqual(self.result.archived_at, first)

    def test_an_archived_notification_can_be_restored(self):
        self._archive(self.result)
        response = self.client.post(f"/api/notifications/{self.result.pk}/unarchive/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_archived"])
        self.assertIn("Laboratory result: FBC", self._titles())
        self.assertEqual(self._titles({"archived": "true"}), [])

    def test_read_and_unread_still_work_on_an_archived_notification(self):
        """The archive filter belongs to the list, never to `get_object()`."""
        self._archive(self.result)
        url = f"/api/notifications/{self.result.pk}/"
        self.assertEqual(self.client.patch(url, {"is_read": True}, format="json").status_code, 200)
        self.result.refresh_from_db()
        self.assertTrue(self.result.is_read)
        self.assertEqual(self.client.patch(url, {"is_read": False}, format="json").status_code, 200)
        self.result.refresh_from_db()
        self.assertFalse(self.result.is_read)

    def test_archived_at_cannot_be_written_directly(self):
        response = self.client.patch(f"/api/notifications/{self.result.pk}/",
                                     {"archived_at": "2026-01-01T00:00:00Z"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.result.refresh_from_db()
        self.assertIsNone(self.result.archived_at)

    # --- many at once --------------------------------------------------

    def test_archiving_the_selected_ones(self):
        response = self.client.post("/api/notifications/archive_selected/",
                                    {"ids": [self.result.pk, self.vitals.pk]}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["archived"], 2)
        self.assertEqual(self._titles(), ["Referral accepted"])

    def test_a_selection_holding_somebody_elses_archives_nothing(self):
        response = self.client.post("/api/notifications/archive_selected/",
                                    {"ids": [self.result.pk, self.labs.pk]}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Notification.objects.archived().exists())

    def test_a_selection_must_name_something(self):
        for body in ({}, {"ids": []}, {"ids": "all"}, {"ids": ["x"]}):
            with self.subTest(body=body):
                response = self.client.post("/api/notifications/archive_selected/", body,
                                            format="json")
                self.assertEqual(response.status_code, 400)

    def test_archive_all_empties_the_inbox_and_keeps_every_row(self):
        self._archive(self.result)
        response = self.client.post("/api/notifications/archive_all/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["archived"], 2)
        self.assertEqual(self._titles(), [])
        self.assertEqual(len(self._titles({"archived": "true"})), 3)
        self.assertEqual(Notification.objects.count(), 4)

    # --- the bell and the overview -------------------------------------

    def test_the_bell_does_not_count_an_archived_notification(self):
        self.assertEqual(self.client.get("/api/notifications/unread-count/").data["unread"], 3)
        self._archive(self.result)
        self.assertEqual(self.client.get("/api/notifications/unread-count/").data["unread"], 2)

    def test_the_dashboard_lists_only_the_inbox(self):
        self._archive(self.result)
        response = self.client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        titles = [row["action"] for row in response.data["recent_activity"]]
        self.assertNotIn("Laboratory result: FBC", titles)
        self.assertIn("Referral accepted", titles)

    def test_the_overview_counts_the_archive_apart(self):
        self._archive(self.result)
        self.client.force_authenticate(self.admin)
        data = self.client.get("/api/notifications/overview/").data
        self.assertEqual((data["total"], data["unread"], data["archived"]), (4, 3, 1))
        dera = next(p for p in data["people"] if p["username"] == "dera")
        self.assertEqual((dera["total"], dera["unread"], dera["archived"]), (3, 2, 1))

    def test_the_admins_whole_system_view_has_an_archive_too(self):
        self._archive(self.result)
        self.client.force_authenticate(self.admin)
        self.assertEqual(len(self._titles({"scope": "all"})), 3)
        self.assertEqual(self._titles({"scope": "all", "archived": "true"}),
                         ["Laboratory result: FBC"])

    # --- the boundaries ------------------------------------------------

    def test_nobody_archives_or_restores_somebody_elses(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self._archive(self.labs).status_code, 403)
        self.assertEqual(
            self.client.post(f"/api/notifications/{self.labs.pk}/unarchive/").status_code, 403)
        self.labs.refresh_from_db()
        self.assertIsNone(self.labs.archived_at)

    def test_an_admins_archive_all_is_their_own_inbox_and_nobody_elses(self):
        notify(recipient=self.admin, title="For the admin")
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post("/api/notifications/archive_all/").data["archived"], 1)
        self.assertEqual(Notification.objects.archived().count(), 1)
        self.assertFalse(Notification.objects.filter(recipient=self.doctor).archived().exists())

    # --- no delete, anywhere -------------------------------------------

    def test_there_is_no_delete_over_the_api(self):
        for user, notification in ((self.doctor, self.result), (self.admin, self.labs)):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user)
                response = self.client.delete(f"/api/notifications/{notification.pk}/")
                self.assertEqual(response.status_code, 405)
        self.client.force_authenticate(self.doctor)
        self.assertEqual(self.client.delete("/api/notifications/").status_code, 405)
        self.assertEqual(Notification.objects.count(), 4)

    def test_django_admin_cannot_delete_one_either(self):
        root = User.objects.create_superuser(username="root", password="t", role="admin",
                                             email="root@example.com")
        request = RequestFactory().get("/admin/")
        request.user = root
        model_admin = admin.site._registry[Notification]
        self.assertFalse(model_admin.has_delete_permission(request, self.result))
        self.assertNotIn("delete_selected", model_admin.get_actions(request))

    def test_django_admin_archives_and_restores_instead(self):
        root = User.objects.create_superuser(username="root", password="t", role="admin",
                                             email="root@example.com")
        browser = Client()
        browser.force_login(root)
        url = reverse("admin:core_notification_changelist")

        browser.post(url, {"action": "archive", "_selected_action": [self.result.pk]})
        self.result.refresh_from_db()
        self.assertIsNotNone(self.result.archived_at)

        browser.post(url, {"action": "unarchive", "_selected_action": [self.result.pk]})
        self.result.refresh_from_db()
        self.assertIsNone(self.result.archived_at)
        self.assertEqual(Notification.objects.count(), 4)
