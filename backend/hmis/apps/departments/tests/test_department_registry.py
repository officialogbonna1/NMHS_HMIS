"""
The department registry, and the protection around it.

Departments had no seed at all: `0001_initial` made the table and nothing
filled it, so a fresh installation had none and `Charge.department` could not
be populated even in principle. These tests hold the registry that fixed it —
that it exists, that it is safe to re-run, that it never renames a department
somebody has edited, and that a department which has taken money cannot be
deleted out from under its own history.
"""
import importlib
from decimal import Decimal

from django.apps import apps as django_apps
from django.db.models import ProtectedError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.departments import REVENUE_DEPARTMENTS
from apps.billing.models import Charge
from apps.billing.services import add_charge
from apps.departments.models import Department
from apps.patients.models import Patient

EXPECTED_CODES = ["reception", "consultation", "laboratory", "pharmacy",
                  "radiology", "eye", "theatre"]

# A migration module's name starts with a digit, so it is reached through
# importlib rather than a plain import.
SEED_MIGRATION = "apps.departments.migrations.0002_seed_revenue_departments"


def seed_module():
    return importlib.import_module(SEED_MIGRATION)


class SeededRegistryTests(TestCase):
    """
    The seven revenue units exist on any database the migrations have run —
    which, in a test, is every one of them. That is the point: a fresh install
    now arrives with somewhere to attribute money to.
    """

    def test_a_fresh_database_has_every_revenue_department(self):
        for code in EXPECTED_CODES:
            with self.subTest(code=code):
                self.assertTrue(Department.objects.filter(code=code).exists(),
                                f"no department seeded with code {code!r}")

    def test_they_are_active_and_therefore_available_for_new_work(self):
        seeded = Department.objects.filter(code__in=EXPECTED_CODES)
        self.assertEqual(seeded.count(), len(EXPECTED_CODES))
        self.assertEqual(seeded.filter(is_active=True).count(), len(EXPECTED_CODES))

    def test_the_registry_codes_are_the_seven_expected(self):
        self.assertEqual([code for code, _, _ in REVENUE_DEPARTMENTS], EXPECTED_CODES)


class MigrationLiteralTests(TestCase):
    """
    The migration's literal list must match `billing.departments`.

    A migration is self-contained by necessity — it cannot import application
    code that may change under it — so the codes and names exist twice. This
    is what stops the two copies drifting apart in silence.
    """

    def test_codes_and_names_match_the_application_registry(self):
        self.assertEqual(
            seed_module().REVENUE_DEPARTMENTS,
            [(code, name) for code, name, _ in REVENUE_DEPARTMENTS],
            "the seed migration and billing.departments have drifted apart",
        )


class IdempotencyTests(TestCase):
    """
    Re-running the seed must change nothing. A migration that is only correct
    the first time is a migration that breaks the second deployment.
    """

    def _seed_again(self):
        seed_module().seed(django_apps, None)

    def test_running_it_twice_creates_nothing_new(self):
        before = Department.objects.count()
        self._seed_again()
        self._seed_again()
        self.assertEqual(Department.objects.count(), before)

    def test_it_never_overwrites_a_renamed_department(self):
        """
        An administrator renames "Radiology / Ultrasound" to "Imaging". The
        code is the machine identity, so attribution keeps working — and the
        seed must leave their name alone, on this run and every future one.
        """
        radiology = Department.objects.get(code="radiology")
        radiology.name = "Imaging"
        radiology.save(update_fields=["name"])

        self._seed_again()

        radiology.refresh_from_db()
        self.assertEqual(radiology.name, "Imaging")
        self.assertEqual(radiology.code, "radiology")

    def test_it_leaves_a_deactivated_department_deactivated(self):
        theatre = Department.objects.get(code="theatre")
        theatre.is_active = False
        theatre.save(update_fields=["is_active"])

        self._seed_again()

        theatre.refresh_from_db()
        self.assertFalse(theatre.is_active,
                         "the seed reactivated a department an admin had retired")

    def test_it_leaves_departments_it_does_not_own_alone(self):
        mine = Department.objects.create(code="physiotherapy", name="Physiotherapy")
        self._seed_again()
        mine.refresh_from_db()
        self.assertEqual(mine.name, "Physiotherapy")
        self.assertTrue(mine.is_active)


class DepartmentProtectionTests(TestCase):
    """
    A department that has taken money is part of the financial record.

    `Charge.department` was SET_NULL and `charge_set` was not in the protected
    relations, so deleting a department silently erased which unit had earned
    every charge it ever took. Both halves are closed now: the API refuses
    with a 409 that says what to do instead, and the database refuses too.
    """

    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_a_department_with_charges_cannot_be_deleted(self):
        charge = add_charge(patient=self.patient, description="Adult Card",
                            amount=Decimal("2000"), created_by=self.reception,
                            source_type="card")
        reception = charge.department
        self.assertIsNotNone(reception)

        response = self.client.delete(f"/api/departments/{reception.pk}/")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "in_use")
        self.assertIn("charge set", response.data["detail"])
        self.assertIn("Deactivate it instead", response.data["detail"])
        self.assertTrue(Department.objects.filter(pk=reception.pk).exists())

    def test_the_database_refuses_too_even_around_the_api(self):
        charge = add_charge(patient=self.patient, description="Adult Card",
                            amount=Decimal("2000"), created_by=self.reception,
                            source_type="card")
        with self.assertRaises(ProtectedError):
            charge.department.delete()

    def test_a_department_nothing_points_at_is_still_deletable(self):
        """Protection is about history, not about departments in general."""
        spare = Department.objects.create(code="spare", name="Spare Unit")
        self.assertEqual(self.client.delete(f"/api/departments/{spare.pk}/").status_code, 204)

    def test_deactivating_is_the_way_to_retire_one(self):
        charge = add_charge(patient=self.patient, description="Adult Card",
                            amount=Decimal("2000"), created_by=self.reception,
                            source_type="card")
        reception = charge.department

        response = self.client.patch(f"/api/departments/{reception.pk}/",
                                     {"is_active": False}, format="json")

        self.assertEqual(response.status_code, 200)
        reception.refresh_from_db()
        self.assertFalse(reception.is_active)

    def test_deactivation_does_not_alter_the_charges_already_against_it(self):
        charge = add_charge(patient=self.patient, description="Adult Card",
                            amount=Decimal("2000"), created_by=self.reception,
                            source_type="card")
        reception = charge.department
        reception.is_active = False
        reception.save(update_fields=["is_active"])

        charge.refresh_from_db()
        self.assertEqual(charge.department_id, reception.pk)
        self.assertEqual(charge.amount, Decimal("2000.00"))
        # And the history still reads: an inactive department is still the
        # department that earned the money.
        self.assertEqual(Charge.objects.get(pk=charge.pk).department.name, "Reception")

    def test_an_inactive_department_takes_no_new_charge_by_the_seed_reactivating_it(self):
        """
        Retiring a unit must stick. Nothing in the attribution path reactivates
        a department to use it — `department_for_source` looks a code up, it
        does not create or revive one.
        """
        pharmacy = Department.objects.get(code="pharmacy")
        pharmacy.is_active = False
        pharmacy.save(update_fields=["is_active"])

        add_charge(patient=self.patient, description="Medication: X", amount=Decimal("500"),
                   created_by=self.reception, source_type="prescription")

        pharmacy.refresh_from_db()
        self.assertFalse(pharmacy.is_active)
