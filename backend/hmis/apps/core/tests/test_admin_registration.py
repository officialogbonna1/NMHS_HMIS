"""
The admin is the back office: if a model is not registered there, the only
way to look at it or fix it is a shell.

These are cheap checks that catch the mistakes a misconfigured ModelAdmin
makes — an `autocomplete_fields` pointing at a model whose admin has no
`search_fields`, a `list_display` naming a field that does not exist —
which otherwise surface as a 500 the first time somebody opens the page.
"""
from io import StringIO

from django.apps import apps as django_apps
from django.contrib import admin
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.patients.models import Patient

# Registered deliberately nowhere: through-tables and anything with no
# reason to be edited by hand.
NOT_REGISTERED = set()


class AdminConfigurationTests(TestCase):
    def test_django_finds_no_problems_with_any_admin(self):
        """`manage.py check` runs the admin.E* checks. It must come back clean."""
        out = StringIO()
        call_command("check", stdout=out, stderr=out)
        self.assertIn("no issues", out.getvalue().lower(), out.getvalue())

    def test_every_model_in_our_apps_is_registered(self):
        missing = []
        for model in django_apps.get_models():
            label = model._meta.label
            if not label.startswith(("accounts.", "patients.", "clinical.", "inventory.",
                                     "pharmacy.", "sales.", "appointments.", "departments.",
                                     "workflow.", "billing.", "diagnostics.", "inpatient.",
                                     "core.")):
                continue  # Django's own tables
            if label in NOT_REGISTERED or model._meta.auto_created:
                continue
            if not admin.site.is_registered(model):
                missing.append(label)
        self.assertEqual(missing, [], f"not visible in the admin: {missing}")

    def test_patients_are_their_own_section_not_hidden_among_users(self):
        self.assertTrue(admin.site.is_registered(Patient))
        self.assertTrue(admin.site.is_registered(User))


class AdminPagesLoadTests(TestCase):
    """A registered model whose changelist 500s is not registered in any
    useful sense."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", password="test", role="admin", email="root@example.com")
        self.client.force_login(self.admin)

    def test_every_changelist_opens(self):
        for model, model_admin in admin.site._registry.items():
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            with self.subTest(model=model._meta.label):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_patient_page_opens_and_finds_a_patient_by_file_number(self):
        patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                         created_by=self.admin)
        url = reverse("admin:patients_patient_changelist")
        response = self.client.get(url, {"q": patient.file_number})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Doe")


class AdminPasswordTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", password="test", role="admin", email="root@example.com")
        self.nurse = User.objects.create_user(username="nurse", password="old-password", role="nurse")
        self.client.force_login(self.admin)

    def _password_url(self):
        return reverse("admin:auth_user_password_change", args=[self.nurse.pk])

    def test_the_set_password_form_opens(self):
        self.assertEqual(self.client.get(self._password_url()).status_code, 200)

    def test_an_admin_can_set_a_staff_password(self):
        response = self.client.post(self._password_url(), {
            "password1": "a-new-strong-password-42",
            "password2": "a-new-strong-password-42",
        })
        self.assertIn(response.status_code, (200, 302))
        self.nurse.refresh_from_db()
        self.assertTrue(self.nurse.check_password("a-new-strong-password-42"),
                        "the new password was not set")
        self.assertFalse(self.nurse.check_password("old-password"))

    def test_the_new_password_works_on_the_api(self):
        self.client.post(self._password_url(), {
            "password1": "a-new-strong-password-42",
            "password2": "a-new-strong-password-42",
        })
        self.client.logout()
        response = self.client.post("/api/auth/login/", {
            "username": "nurse", "password": "a-new-strong-password-42",
        })
        self.assertEqual(response.status_code, 200, response.content)

    def test_the_role_is_asked_for_when_creating_a_staff_account(self):
        """A user created without a role can log in and reach nothing."""
        fields = admin.site._registry[User].add_fieldsets
        asked = {name for _, opts in fields for name in opts["fields"]}
        self.assertIn("role", asked)
