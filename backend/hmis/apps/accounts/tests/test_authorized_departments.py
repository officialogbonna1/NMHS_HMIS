"""
**Where a member of staff may work**, now that it can be more than one place.

The relation was always a many-to-many (`Department.staff`, since
`departments/0001`), so nothing here is a new authorisation system: what these
tests hold is that the question is asked in **one** place, that role and
department are both required, and that a person cannot answer it for
themselves.

The rule under test, stated once:

    ROLE           — what this person may do
    + DEPARTMENT   — where they may do it
    = AUTHORISED

Department alone grants nothing, and the role is never widened by a posting.
"""
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from apps.accounts.admin import HMISUserAdmin, StaffForm
from apps.accounts.departments import (authorized_departments, staff_of, works_in)
from apps.accounts.models import User
from apps.departments.models import Department


class Hospital(TestCase):
    def setUp(self):
        # Synthetic (§23). Maternity, Laboratory and Pharmacy are seeded
        # (`departments/0002` and `0006`); General Medicine is not a seeded
        # department in this hospital, so the test makes its own rather than
        # depending on one somebody created by hand.
        self.medicine, _ = Department.objects.get_or_create(
            code="general-medicine", defaults={"name": "General Medicine"})
        self.maternity = Department.objects.get(code="maternity")
        self.laboratory = Department.objects.get(code="laboratory")
        self.pharmacy = Department.objects.get(code="pharmacy")

        self.doctor = User.objects.create_user(username="dara", password="t",
                                               role="doctor", first_name="Dara",
                                               last_name="Ibe")
        self.medicine.staff.add(self.doctor)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client


class OneStaffMemberSeveralDepartments(Hospital):
    """The core requirement."""

    def test_a_doctor_starts_in_one(self):
        self.assertEqual([d.code for d in authorized_departments(self.doctor)],
                         ["general-medicine"])
        self.assertTrue(works_in(self.doctor, self.medicine))
        self.assertFalse(works_in(self.doctor, self.maternity))

    def test_an_administrator_adds_a_second(self):
        self.maternity.staff.add(self.doctor)
        self.assertEqual(
            sorted(d.code for d in authorized_departments(self.doctor)),
            ["general-medicine", "maternity"])
        self.assertTrue(works_in(self.doctor, self.medicine))
        self.assertTrue(works_in(self.doctor, self.maternity))

    def test_removing_one_leaves_the_other(self):
        self.maternity.staff.add(self.doctor)
        self.maternity.staff.remove(self.doctor)
        self.assertTrue(works_in(self.doctor, self.medicine),
                        "General Medicine must survive losing Maternity")
        self.assertFalse(works_in(self.doctor, self.maternity))

    def test_it_is_independently_controllable_in_both_directions(self):
        for _ in range(2):
            self.maternity.staff.add(self.doctor)
            self.assertTrue(works_in(self.doctor, self.maternity))
            self.maternity.staff.remove(self.doctor)
            self.assertFalse(works_in(self.doctor, self.maternity))
            self.assertTrue(works_in(self.doctor, self.medicine))

    def test_a_posting_grants_nothing_anywhere_else(self):
        self.maternity.staff.add(self.doctor)
        for elsewhere in (self.laboratory, self.pharmacy):
            with self.subTest(department=elsewhere.code):
                self.assertFalse(works_in(self.doctor, elsewhere))

    def test_a_department_names_everyone_posted_to_it(self):
        nurse = User.objects.create_user(username="grace", password="t", role="nurse")
        self.maternity.staff.add(self.doctor, nurse)
        self.assertEqual({u.username for u in staff_of(self.maternity)},
                         {"dara", "grace"})
        # Narrowed by role, which is what a selector asks.
        self.assertEqual({u.username for u in staff_of(self.maternity, roles=["doctor"])},
                         {"dara"})


class TheQuestionIsAskedOneWay(Hospital):
    """`works_in` takes what call sites actually hold, and never raises."""

    def test_a_department_a_code_or_a_name(self):
        self.maternity.staff.add(self.doctor)
        for named in (self.maternity, "maternity", "Maternity", "  MATERNITY  "):
            with self.subTest(named=named):
                self.assertTrue(works_in(self.doctor, named))

    def test_nonsense_is_unauthorised_rather_than_an_error(self):
        for named in (None, "", "   ", "no-such-department", 12345):
            with self.subTest(named=named):
                self.assertFalse(works_in(self.doctor, named))

    def test_an_anonymous_caller_is_authorised_nowhere(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(works_in(AnonymousUser(), self.maternity))
        self.assertEqual(list(authorized_departments(AnonymousUser())), [])


class TheLegacyTextStillWorks(Hospital):
    """
    Backward compatibility: an account whose only record of its department is
    the old free-text column keeps its access, and the two spellings of that
    fallback — the code and the name — are now one rule rather than two that
    disagreed.
    """

    def test_the_name_authorises(self):
        who = User.objects.create_user(username="old", password="t", role="radiology",
                                       department="Radiology / Ultrasound")
        self.assertTrue(works_in(who, "radiology"))

    def test_and_so_does_the_code(self):
        who = User.objects.create_user(username="old2", password="t", role="radiology",
                                       department="radiology")
        self.assertTrue(works_in(who, "radiology"))

    def test_text_naming_no_department_authorises_nothing(self):
        who = User.objects.create_user(username="desk", password="t", role="reception",
                                       department="Front Desk")
        self.assertEqual(list(authorized_departments(who)), [])

    def test_the_text_is_not_the_relation_and_neither_overwrites_the_other(self):
        who = User.objects.create_user(username="both", password="t", role="doctor",
                                       department="General Medicine")
        self.maternity.staff.add(who)
        self.assertEqual(sorted(d.code for d in authorized_departments(who)),
                         ["general-medicine", "maternity"])
        who.refresh_from_db()
        self.assertEqual(who.department, "General Medicine",
                         "the organisational label is not touched by a posting")


def _migration():
    """The data migration's own function, imported by path — the module name
    starts with a digit, so it cannot be imported with `from … import`."""
    import importlib

    return importlib.import_module(
        "apps.accounts.migrations.0007_authorized_departments_backfill")


class TheMigrationPreservesExistingStaff(Hospital):
    """
    §24: an account represented the old way keeps working, with no
    administrator repairing it by hand.
    """

    def test_it_backfills_the_relation_from_the_text(self):
        from django.apps import apps as django_apps

        module = _migration()
        who = User.objects.create_user(username="legacy", password="t", role="doctor",
                                       department="General Medicine")
        Department.objects.get(code="general-medicine").staff.remove(who)
        self.assertEqual(list(who.department_memberships.all()), [])

        module.backfill(django_apps, None)

        self.assertEqual([d.code for d in who.department_memberships.all()],
                         ["general-medicine"])

    def test_it_is_safe_to_run_twice(self):
        from django.apps import apps as django_apps
        module = _migration()

        who = User.objects.create_user(username="legacy2", password="t", role="doctor",
                                       department="General Medicine")
        module.backfill(django_apps, None)
        module.backfill(django_apps, None)
        self.assertEqual(who.department_memberships.count(), 1)

    def test_it_invents_nothing_for_text_naming_no_department(self):
        from django.apps import apps as django_apps
        module = _migration()

        who = User.objects.create_user(username="legacy3", password="t", role="reception",
                                       department="Front Desk")
        module.backfill(django_apps, None)
        self.assertEqual(who.department_memberships.count(), 0)
        self.assertEqual(Department.objects.filter(name="Front Desk").count(), 0)

    def test_a_blank_department_is_handled(self):
        from django.apps import apps as django_apps
        module = _migration()

        who = User.objects.create_user(username="legacy4", password="t", role="doctor")
        module.backfill(django_apps, None)
        self.assertEqual(who.department_memberships.count(), 0)


class TheApiSaysWhereSomebodyWorks(Hospital):
    """§10 — `/auth/me/`, and what it must never accept back."""

    def me(self, user):
        return self.api(user).get("/api/auth/me/").data

    def test_it_carries_the_authorised_departments(self):
        self.maternity.staff.add(self.doctor)
        payload = self.me(self.doctor)
        self.assertEqual(sorted(d["code"] for d in payload["authorized_departments"]),
                         ["general-medicine", "maternity"])

    def test_it_keeps_the_primary_department_beside_them(self):
        self.doctor.department = "General Medicine"
        self.doctor.save(update_fields=["department"])
        payload = self.me(self.doctor)
        self.assertEqual(payload["department"], "General Medicine")
        self.assertIn("authorized_departments", payload)

    def test_a_single_department_account_reads_exactly_as_before(self):
        payload = self.me(self.doctor)
        for field in ("id", "username", "role", "department", "staff_number"):
            self.assertIn(field, payload)
        self.assertEqual(len(payload["authorized_departments"]), 1)

    def test_an_account_posted_nowhere_gets_an_empty_list_not_an_error(self):
        nobody = User.objects.create_user(username="new", password="t", role="cashier")
        self.assertEqual(self.me(nobody)["authorized_departments"], [])


class StaffCannotPostThemselves(Hospital):
    """§9, §16 and §21 — self-escalation, from every direction."""

    def test_the_account_cannot_write_it_about_itself(self):
        """
        `UserSerializer` is the payload the signed-in account reads about
        itself (`/auth/me/`), and it will not take this back. An administrator
        *does* write it — through `UserAdminSerializer`, behind `IsAdmin` —
        which is the point: the person described never sets it.
        """
        from apps.accounts.serializers import UserSerializer

        writable = {name for name, field in UserSerializer().get_fields().items()
                    if not field.read_only}
        self.assertNotIn("authorized_departments", writable)

    def test_patching_their_own_account_changes_nothing(self):
        answer = self.api(self.doctor).patch(f"/api/users/{self.doctor.pk}/", {
            "authorized_departments": [self.maternity.pk], "department": "Maternity"})
        # A doctor is not an administrator, so the whole endpoint refuses.
        self.assertEqual(answer.status_code, 403)
        self.assertFalse(works_in(self.doctor, self.maternity))

    def test_an_administrator_posts_somebody_from_the_users_page(self):
        """
        Two front doors onto one relation — the Users page and Django admin —
        which is rule 31, not a second way of granting access. An administrator
        should not have to open Django admin to put a doctor on a second ward.
        """
        boss = User.objects.create_user(username="boss", password="t", role="admin")
        answer = self.api(boss).patch(f"/api/users/{self.doctor.pk}/", {
            "authorized_departments": [self.medicine.pk, self.maternity.pk]},
            format="json")
        self.assertEqual(answer.status_code, 200, answer.data)
        self.doctor.refresh_from_db()
        self.assertTrue(works_in(self.doctor, self.maternity))
        self.assertTrue(works_in(self.doctor, self.medicine))

    def test_and_can_take_it_away_again(self):
        boss = User.objects.create_user(username="boss2", password="t", role="admin")
        self.maternity.staff.add(self.doctor)
        answer = self.api(boss).patch(f"/api/users/{self.doctor.pk}/",
                                      {"authorized_departments": [self.medicine.pk]},
                                      format="json")
        self.assertEqual(answer.status_code, 200, answer.data)
        self.assertFalse(works_in(self.doctor, self.maternity))
        self.assertTrue(works_in(self.doctor, self.medicine))

    def test_the_change_is_audited_with_both_ends_of_it(self):
        from apps.core.models import AuditLog

        boss = User.objects.create_user(username="boss3", password="t", role="admin")
        self.api(boss).patch(f"/api/users/{self.doctor.pk}/", {
            "authorized_departments": [self.medicine.pk, self.maternity.pk]},
            format="json")
        row = AuditLog.objects.filter(action="user.updated").latest("id")
        self.assertEqual(row.actor, boss)
        change = row.details["authorized_departments"]
        self.assertEqual(change["before"], ["General Medicine"])
        self.assertEqual(sorted(change["after"]), ["General Medicine", "Maternity"])

    def test_an_edit_that_leaves_the_departments_alone_logs_no_change(self):
        from apps.core.models import AuditLog

        boss = User.objects.create_user(username="boss4", password="t", role="admin")
        self.api(boss).patch(f"/api/users/{self.doctor.pk}/", {"first_name": "Dara"},
                             format="json")
        row = AuditLog.objects.filter(action="user.updated").latest("id")
        self.assertNotIn("authorized_departments", row.details)

    def test_a_doctor_cannot_post_anybody_including_themselves(self):
        for target in (self.doctor.pk,):
            answer = self.api(self.doctor).patch(f"/api/users/{target}/", {
                "authorized_departments": [self.maternity.pk]}, format="json")
            self.assertEqual(answer.status_code, 403)
        self.assertFalse(works_in(self.doctor, self.maternity))

    def test_only_an_active_department_can_be_chosen(self):
        boss = User.objects.create_user(username="boss5", password="t", role="admin")
        self.maternity.is_active = False
        self.maternity.save(update_fields=["is_active"])
        answer = self.api(boss).patch(f"/api/users/{self.doctor.pk}/", {
            "authorized_departments": [self.maternity.pk]}, format="json")
        self.assertEqual(answer.status_code, 400)

    def test_the_list_says_where_each_person_works(self):
        boss = User.objects.create_user(username="boss6", password="t", role="admin")
        self.maternity.staff.add(self.doctor)
        rows = self.api(boss).get("/api/users/").data
        rows = rows.get("results", rows)
        mine = next(r for r in rows if r["id"] == self.doctor.pk)
        self.assertEqual(sorted(mine["authorized_department_names"]),
                         ["General Medicine", "Maternity"])

    def test_sending_a_department_id_in_a_request_grants_nothing(self):
        """§16 — a client that names a department is not thereby in it."""
        for params in ({"department": self.maternity.pk},
                       {"department_id": self.maternity.pk},
                       {"department": "maternity"}):
            with self.subTest(params=params):
                self.assertFalse(works_in(self.doctor, self.maternity))
                answer = self.api(self.doctor).get("/api/maternity/patients/", params)
                self.assertEqual(answer.status_code, 200)
                self.assertEqual(answer.data["results"], [])


class TheAdminFormManagesIt(Hospital):
    """§9 and §21 — an administrator adds and removes, and it persists."""

    def form_for(self, user, chosen):
        # `UserChangeForm` is the base, so the bound data is the model's own
        # editable fields plus the one this form adds.
        data = {
            "username": user.username, "role": user.role,
            "department": user.department or "",
            "authorized_departments": [d.pk for d in chosen],
            "first_name": user.first_name, "last_name": user.last_name,
            "email": user.email or "",
            "date_joined": user.date_joined.isoformat(),
        }
        return StaffForm(data=data, instance=user)

    def test_it_offers_the_active_departments(self):
        form = StaffForm(instance=self.doctor)
        offered = set(form.fields["authorized_departments"].queryset)
        self.assertIn(self.maternity, offered)
        self.assertIn(self.medicine, offered)

    def test_it_shows_what_the_person_already_has(self):
        self.maternity.staff.add(self.doctor)
        form = StaffForm(instance=self.doctor)
        self.assertEqual(
            {d.pk for d in form.fields["authorized_departments"].initial},
            {self.medicine.pk, self.maternity.pk})

    def test_an_administrator_adds_one_and_it_persists(self):
        form = self.form_for(self.doctor, [self.medicine, self.maternity])
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.doctor.refresh_from_db()
        self.assertTrue(works_in(self.doctor, self.maternity))

    def test_and_removes_one(self):
        self.maternity.staff.add(self.doctor)
        form = self.form_for(self.doctor, [self.medicine])
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.doctor.refresh_from_db()
        self.assertFalse(works_in(self.doctor, self.maternity))
        self.assertTrue(works_in(self.doctor, self.medicine))

    def test_a_retired_department_somebody_holds_is_not_silently_revoked(self):
        """The widget never offered it, so saving the form must not drop it."""
        self.maternity.staff.add(self.doctor)
        self.maternity.is_active = False
        self.maternity.save(update_fields=["is_active"])
        form = self.form_for(self.doctor, [self.medicine])
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(self.maternity.staff.filter(pk=self.doctor.pk).exists())

    def test_the_field_is_on_the_staff_form(self):
        site = AdminSite()
        admin = HMISUserAdmin(User, site)
        request = RequestFactory().get("/admin/accounts/user/1/change/")
        request.user = User.objects.create_user(username="boss2", password="t",
                                                role="admin", is_superuser=True,
                                                is_staff=True)
        fields = admin.get_form(request, self.doctor)().fields
        self.assertIn("authorized_departments", fields)
        self.assertIn("department", fields)


class TheLegacyFallbackCanEventuallyBeRetired(Hospital):
    """
    §1 — the fallback is temporary, and this is what proves when it is safe to
    drop rather than leaving somebody to guess.

    **Why it exists**: `User.department` was free text and, before
    `accounts/departments.py`, three of the four authorisation readings already
    consulted it. Removing it in the same change that introduced the relation
    would have taken access away from any account whose only record of its
    department was that string.

    **Who needs it**: nobody, once `accounts/0007` has run. The migration
    reconciles every account whose text names a real department into
    `Department.staff`, so the fallback only covers text typed *after* the
    migration — and the Users page now labels that field "not an
    authorisation" precisely so nothing new comes to depend on it.

    **How to retire it**: delete the text clause from `_membership_q` and
    `staff_of`. `test_nobody_depends_on_the_fallback_once_reconciled` is what
    says the day has come; while it passes on a deployment, dropping the
    fallback changes nobody's authorisation there.
    """

    def reconcile(self):
        """What `accounts/0007` does, applied to whatever is in the database."""
        from django.apps import apps as django_apps

        _migration().backfill(django_apps, None)

    def authorisation_map(self, *, with_fallback):
        """Every account's authorised departments, with and without the text."""
        from apps.accounts import departments as helper
        from django.db.models import Q

        if with_fallback:
            return {u.username: sorted(d.code for d in helper.authorized_departments(u))
                    for u in User.objects.all()}
        # The relation alone — what `_membership_q` would be with the text
        # clause deleted.
        return {u.username: sorted(d.code for d in
                                   Department.objects.filter(Q(staff=u)).distinct())
                for u in User.objects.all()}

    def test_nobody_depends_on_the_fallback_once_reconciled(self):
        User.objects.create_user(username="legacy_a", password="t", role="radiology",
                                 department="Radiology / Ultrasound")
        User.objects.create_user(username="legacy_b", password="t", role="doctor",
                                 department="general-medicine")
        User.objects.create_user(username="legacy_c", password="t", role="reception",
                                 department="Front Desk")

        self.reconcile()

        self.assertEqual(self.authorisation_map(with_fallback=True),
                         self.authorisation_map(with_fallback=False),
                         "an account still depends on the legacy text; the "
                         "fallback cannot be retired yet")

    def test_before_reconciling_it_is_load_bearing(self):
        """The other half: without the migration the fallback is doing work."""
        who = User.objects.create_user(username="legacy_d", password="t",
                                       role="radiology",
                                       department="Radiology / Ultrasound")
        self.assertTrue(works_in(who, "radiology"))
        self.assertEqual(who.department_memberships.count(), 0,
                         "the relation is empty until the migration runs")

    def test_text_naming_no_department_never_becomes_authorisation(self):
        """It is not a second authorisation system: it resolves or it does not."""
        who = User.objects.create_user(username="vague", password="t", role="nurse",
                                       department="Theatre annexe, second floor")
        self.assertEqual(list(authorized_departments(who)), [])
        self.reconcile()
        self.assertEqual(list(authorized_departments(who)), [])
        self.assertEqual(Department.objects.filter(
            name="Theatre annexe, second floor").count(), 0)


class CreatingAnAccountWithItsDepartments(Hospital):
    """
    The add path, which the patch tests do not cover: DRF has to write the
    many-to-many *after* the row exists, and a create that silently dropped it
    would leave a new member of staff posted nowhere.
    """

    def setUp(self):
        super().setUp()
        self.boss = User.objects.create_user(username="boss", password="t", role="admin")

    def test_a_new_doctor_is_posted_to_both_at_once(self):
        answer = self.api(self.boss).post("/api/users/", {
            "username": "newdoc", "role": "doctor", "password": "x7Kq2m9Zt4",
            "authorized_departments": [self.medicine.pk, self.maternity.pk],
        }, format="json")
        self.assertEqual(answer.status_code, 201, answer.data)
        made = User.objects.get(username="newdoc")
        self.assertEqual(sorted(d.code for d in authorized_departments(made)),
                         ["general-medicine", "maternity"])

    def test_a_new_account_with_none_named_is_posted_nowhere(self):
        answer = self.api(self.boss).post("/api/users/", {
            "username": "newcash", "role": "cashier", "password": "x7Kq2m9Zt4",
        }, format="json")
        self.assertEqual(answer.status_code, 201, answer.data)
        self.assertEqual(
            list(authorized_departments(User.objects.get(username="newcash"))), [])

    def test_the_creation_is_audited_with_where_they_were_posted(self):
        from apps.core.models import AuditLog

        self.api(self.boss).post("/api/users/", {
            "username": "newnurse", "role": "nurse", "password": "x7Kq2m9Zt4",
            "authorized_departments": [self.maternity.pk],
        }, format="json")
        row = AuditLog.objects.filter(action="user.created").latest("id")
        self.assertEqual(row.details["authorized_departments"], ["Maternity"])
