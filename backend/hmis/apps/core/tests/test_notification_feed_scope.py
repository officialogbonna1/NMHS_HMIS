"""
Each person's notification feed is theirs, in the role they hold now.

Two halves, both held here:

* **Who is told** is decided when a notification is raised, by the rules that
  already exist — the eye doctor hears about their own patients, an unclaimed
  eye referral reaches every eye clinician (rule 14), money reaches the desks
  (rule 35). Nothing about that changed.
* **What a feed shows** is `NotificationQuerySet.for_user`: addressed to the
  caller *and* raised for the role they hold now. The list, the bell,
  mark-all-read, archiving and the dashboard all read it, and no request
  parameter reaches past it.
"""
import importlib
from datetime import timedelta

from django.apps import apps as django_apps
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.services import add_charge
from apps.core.models import AuditLog, Notification
from apps.core.services import notify
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class NotificationFeedScopeTests(TestCase):
    def setUp(self):
        make = lambda name, role: User.objects.create_user(username=name, password="t", role=role)
        self.reception, self.nurse = make("rec", "reception"), make("nurse", "nurse")
        self.doctor, self.lab = make("doc", "doctor"), make("lab", "laboratory")
        self.eye_a, self.eye_b = make("eye_a", "ophthalmologist"), make("eye_b", "ophthalmologist")
        self.optometrist = make("opto", "optometrist")
        self.cashier, self.accountant, self.admin = make("cash", "cashier"), make("acc", "accountant"), make("adm", "admin")
        self.department = Department.objects.create(code="eye-unit-test", name="Eye Unit (test)")

        self.patient_a = self._eye_patient("Ada", self.eye_a)
        self.patient_b = self._eye_patient("Bola", self.eye_b)
        self.general = Patient.objects.create(first_name="Chi", last_name="Test", sex="M", created_by=self.reception)
        Visit.objects.create(patient=self.general, opened_by=self.reception, attending_doctor=self.doctor)

    def _eye_patient(self, name, eye_doctor):
        patient = Patient.objects.create(first_name=name, last_name="Test", sex="F", created_by=self.reception)
        visit = Visit.objects.create(patient=patient, opened_by=self.reception)
        PatientRoute.objects.create(visit=visit, department=self.department, purpose="eye",
                                    assigned_to=eye_doctor, routed_by=self.reception, status="in_progress")
        return patient

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def feed(self, user, **params):
        response = self.as_(user).get("/api/notifications/", params)
        self.assertEqual(response.status_code, 200, response.data)
        return [row["title"] for row in rows(response)]

    def unread(self, user, **params):
        return self.as_(user).get("/api/notifications/unread-count/", params).data["unread"]

    def dashboard_unread(self, user):
        cards = self.as_(user).get("/api/dashboard/").data["cards"]
        return next(card["value"] for card in cards if card.get("key") == "unread")

    def take_vitals(self, patient):
        response = self.as_(self.nurse).post("/api/vitals/", {
            "patient": patient.id, "visit_time": timezone.now().isoformat(), "heart_rate": 80}, format="json")
        self.assertEqual(response.status_code, 201, response.data)

    # 1 -------------------------------------------------------------------------
    def test_another_eye_doctors_notifications_never_reach_this_feed(self):
        notify(recipient=self.eye_b, title="For Eye Doctor B only", category="general")
        self.take_vitals(self.patient_b)
        self.assertEqual(self.feed(self.eye_a), [])
        self.assertEqual((self.unread(self.eye_a), self.dashboard_unread(self.eye_a)), (0, 0))
        self.assertEqual(set(self.feed(self.eye_b)), {"For Eye Doctor B only", f"New vitals: {self.patient_b}"})
        self.assertEqual(self.unread(self.eye_b), 2)

    # 2 -------------------------------------------------------------------------
    def test_a_general_doctors_patient_activity_stays_with_that_doctor(self):
        self.take_vitals(self.general)
        referral = self.as_(self.doctor).post("/api/patient-routes/refer/", {
            "patient": self.general.id, "purpose": "laboratory", "notes": "FBC"}, format="json")
        self.assertEqual(referral.status_code, 201, referral.data)
        self.assertEqual(self.feed(self.doctor), [f"New vitals: {self.general}"])
        self.assertTrue(Notification.objects.filter(recipient=self.lab).exists())
        for user in (self.eye_a, self.eye_b):
            self.assertEqual((self.feed(user), self.unread(user)), ([], 0))

    # 3 -------------------------------------------------------------------------
    def test_a_notification_addressed_to_them_is_in_their_feed_their_bell_and_their_dashboard(self):
        notify(recipient=self.eye_a, title="Addressed to Eye Doctor A", category="routing", action_url="/eye")
        self.assertEqual(self.feed(self.eye_a), ["Addressed to Eye Doctor A"])
        self.assertEqual((self.unread(self.eye_a), self.dashboard_unread(self.eye_a)), (1, 1))
        activity = [row["action"] for row in self.as_(self.eye_a).get("/api/dashboard/").data["recent_activity"]]
        self.assertEqual(activity, ["Addressed to Eye Doctor A"])

    # 4 -------------------------------------------------------------------------
    def test_an_unclaimed_eye_referral_is_broadcast_to_the_eye_clinicians_as_it_always_was(self):
        response = self.as_(self.doctor).post("/api/patient-routes/refer/", {
            "patient": self.general.id, "purpose": "eye", "notes": "Sudden loss of vision"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        title = f"Eye clinic requested: {self.general}"
        for user in (self.eye_a, self.eye_b, self.optometrist):
            with self.subTest(user=user.username):
                self.assertEqual(self.feed(user), [title])
        self.assertEqual(Notification.objects.get(recipient=self.eye_a).action_url, "/eye")
        self.assertEqual(self.feed(self.doctor), [])

    # 5 -------------------------------------------------------------------------
    def test_activity_on_their_own_patient_reaches_them_and_not_the_other_eye_doctor(self):
        self.take_vitals(self.patient_a)
        referral = self.as_(self.eye_a).post("/api/patient-routes/refer/", {
            "patient": self.patient_a.id, "purpose": "laboratory", "notes": "FBS"}, format="json")
        self.assertEqual(referral.status_code, 201, referral.data)
        bench = self.as_(self.lab)
        bench.post(f"/api/patient-routes/{referral.data['id']}/accept/")
        filed = bench.post(f"/api/patient-routes/{referral.data['id']}/record-result/",
                           {"result": "FBS 5.1 mmol/L"}, format="json")
        self.assertEqual(filed.status_code, 200, filed.data)

        feed = self.feed(self.eye_a)
        self.assertIn(f"New vitals: {self.patient_a}", feed)
        self.assertIn(f"Laboratory result: {self.patient_a}", feed)
        self.assertEqual(self.unread(self.eye_a), len(feed))
        self.assertEqual(self.feed(self.eye_b), [])

    # 6 -------------------------------------------------------------------------
    def test_no_request_parameter_opens_another_persons_feed(self):
        theirs = notify(recipient=self.eye_b, title="Eye Doctor B's patient", category="clinical")
        api = self.as_(self.eye_a)
        for params in ({"recipient": self.eye_b.id}, {"search": "Eye Doctor B"}, {"category": "clinical"},
                       {"archived": "true"}, {"is_read": "false"}, {"recipient__role": "ophthalmologist"}):
            with self.subTest(params=params):
                self.assertEqual(self.feed(self.eye_a, **params), [])
        self.assertEqual(api.get("/api/notifications/", {"scope": "all"}).status_code, 403)
        self.assertEqual(api.get(f"/api/notifications/{theirs.id}/").status_code, 404)
        self.assertEqual(api.patch(f"/api/notifications/{theirs.id}/", {"is_read": True}, format="json").status_code, 403)
        self.assertEqual(api.post(f"/api/notifications/{theirs.id}/archive/").status_code, 403)
        self.assertEqual(api.post("/api/notifications/archive_selected/", {"ids": [theirs.id]},
                                  format="json").status_code, 403)
        self.assertEqual(self.unread(self.eye_a, recipient=self.eye_b.id), 0)
        api.post("/api/notifications/mark_all_read/")
        api.post("/api/notifications/archive_all/")
        theirs.refresh_from_db()
        self.assertEqual((theirs.is_read, theirs.archived_at), (False, None))

    # 7 -------------------------------------------------------------------------
    def test_financial_notifications_reach_the_money_desks_exactly_as_before(self):
        add_charge(patient=self.general, description="Consultation", amount="3000",
                   created_by=self.reception, source_type="consultation")
        for user in (self.cashier, self.accountant, self.admin):
            with self.subTest(user=user.username):
                self.assertEqual(len(self.feed(user, category="billing")), 1)
                self.assertEqual(self.unread(user), 1)
        # Whoever raised it is not told, and no clinician ever is.
        for user in (self.reception, self.eye_a, self.eye_b, self.doctor, self.nurse):
            with self.subTest(user=user.username):
                self.assertEqual((self.feed(user), self.unread(user)), ([], 0))

    # the role the account holds now ------------------------------------------
    def test_a_role_change_takes_the_old_roles_notifications_out_of_the_feed_and_the_bell(self):
        account = User.objects.create_user(username="moved", password="t", role="cashier")
        refund = notify(recipient=account, title="REFUND: Nwosu, Amaka", category="billing")
        self.assertEqual((refund.raised_for_role, self.unread(account)), ("cashier", 1))

        account.role = "ophthalmologist"
        account.save(update_fields=["role"])
        notify(recipient=account, title="Eye clinic requested: Obi", category="routing")
        self.assertEqual(self.feed(account), ["Eye clinic requested: Obi"])
        self.assertEqual((self.unread(account), self.dashboard_unread(account)), (1, 1))
        self.assertEqual(self.as_(account).get(f"/api/notifications/{refund.id}/").status_code, 404)
        self.as_(account).post("/api/notifications/mark_all_read/")
        refund.refresh_from_db()
        self.assertFalse(refund.is_read)

        # Kept, and an admin's oversight view still has it — with the role it was for.
        oversight = rows(self.as_(self.admin).get("/api/notifications/", {"scope": "all", "recipient": account.id}))
        self.assertEqual({(r["title"], r["raised_for_role"]) for r in oversight},
                         {("REFUND: Nwosu, Amaka", "cashier"), ("Eye clinic requested: Obi", "ophthalmologist")})

        account.role = "cashier"
        account.save(update_fields=["role"])
        self.assertEqual(self.feed(account), ["REFUND: Nwosu, Amaka"])


class ExistingNotificationStampingTests(TestCase):
    """The migration's rule for rows raised before `raised_for_role` existed."""

    def setUp(self):
        self.stamp = importlib.import_module("apps.core.migrations.0006_notification_raised_for_role").stamp_existing
        self.admin = User.objects.create_user(username="adm", password="t", role="admin")
        self.now = timezone.now()

    def old_notification(self, user, days_ago):
        note = notify(recipient=user, title=f"{user.username} {days_ago}", category="general")
        Notification.objects.filter(pk=note.pk).update(raised_for_role="",
                                                       created_at=self.now - timedelta(days=days_ago))
        return note.pk

    def role_value(self, pk):
        return Notification.objects.get(pk=pk).raised_for_role

    def test_only_rows_raised_since_the_last_recorded_role_change_are_given_the_current_role(self):
        steady = User.objects.create_user(username="steady", password="t", role="cashier")
        moved = User.objects.create_user(username="moved", password="t", role="ophthalmologist")
        edited = User.objects.create_user(username="edited", password="t", role="nurse")
        steady_row = self.old_notification(steady, 10)
        before_move, after_move = self.old_notification(moved, 10), self.old_notification(moved, 1)
        before_edit = self.old_notification(edited, 10)

        LogEntry.objects.create(user_id=self.admin.pk, content_type=ContentType.objects.get_for_model(User),
                                object_id=str(moved.pk), object_repr="moved", action_flag=CHANGE,
                                change_message='[{"changed": {"fields": ["Role"]}}]',
                                action_time=self.now - timedelta(days=5))
        # A name change in Django admin is not a role change.
        LogEntry.objects.create(user_id=self.admin.pk, content_type=ContentType.objects.get_for_model(User),
                                object_id=str(steady.pk), object_repr="steady", action_flag=CHANGE,
                                change_message='[{"changed": {"fields": ["First name"]}}]',
                                action_time=self.now - timedelta(days=5))
        audit = AuditLog.objects.create(actor=self.admin, action="user.updated", object_id=str(edited.pk))
        AuditLog.objects.filter(pk=audit.pk).update(created_at=self.now - timedelta(days=5))

        self.stamp(django_apps, None)
        self.assertEqual(self.role_value(steady_row), "cashier")
        self.assertEqual((self.role_value(before_move), self.role_value(after_move)), ("", "ophthalmologist"))
        self.assertEqual(self.role_value(before_edit), "")
