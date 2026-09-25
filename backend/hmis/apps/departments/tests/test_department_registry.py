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
                  "radiology", "eye", "theatre",
                  # Maternity joined when the hospital configured its price
                  # list — see the note in `billing/departments.py`.
                  "maternity"]

# A migration module's name starts with a digit, so it is reached through
# importlib rather than a plain import.
SEED_MIGRATION = "apps.departments.migrations.0002_seed_revenue_departments"
# Maternity is seeded by its own migration, written when it was a department
# that raised no charges. It stays there rather than being back-dated into
# `0002`, which is applied history on every deployment — so the drift test
# below reads both, which is what the seeded literals actually are.
MATERNITY_SEED_MIGRATION = "apps.departments.migrations.0006_seed_maternity_department"


def seed_module():
    return importlib.import_module(SEED_MIGRATION)


def seeded_literals():
    """Every (code, name) the migrations actually seed, wherever they seed it."""
    maternity = importlib.import_module(MATERNITY_SEED_MIGRATION)
    return seed_module().REVENUE_DEPARTMENTS + [(maternity.CODE, maternity.NAME)]


class SeededRegistryTests(TestCase):
    """
    Every revenue unit exists on any database the migrations have run —
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

    def test_the_registry_codes_are_the_ones_expected(self):
        self.assertEqual([code for code, _, _ in REVENUE_DEPARTMENTS], EXPECTED_CODES)


class ClinicalDepartmentTests(TestCase):
    """
    The units that treat patients without billing them.

    `PatientRoute.department` is required, so every route the front desk
    raises names a department — and there was none for nursing. Reception had
    to file a vitals route against Consultation or General Medicine, neither
    of which is where the patient went.

    Seeded by `departments/0003`, deliberately *outside* `REVENUE_DEPARTMENTS`:
    a nurse raises no charge, so a nursing entry there would be a column that
    is permanently zero in every financial report.
    """

    def test_the_nursing_department_is_seeded_and_available(self):
        clinicals = Department.objects.get(code="clinicals")
        self.assertEqual(clinicals.name, "Clinicals (Nursing)")
        self.assertTrue(clinicals.is_active)

    def test_it_is_not_a_revenue_department(self):
        self.assertNotIn("clinicals", [code for code, _, _ in REVENUE_DEPARTMENTS])
        # And the registry stays the subset that takes money, which is what
        # stops a unit being added here just for having a queue.
        # the next time somebody wants a department in a dropdown.
        self.assertEqual(len(REVENUE_DEPARTMENTS), len(EXPECTED_CODES))

    def test_a_vitals_route_can_name_it(self):
        """The reason it exists: the front desk can file nursing work truthfully."""
        from apps.workflow.models import PatientRoute, Visit

        reception = User.objects.create_user(username="desk2", password="t", role="reception")
        patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                         created_by=reception)
        visit = Visit.objects.create(patient=patient, opened_by=reception)
        route = PatientRoute.objects.create(
            visit=visit, department=Department.objects.get(code="clinicals"),
            purpose="vitals", routed_by=reception)
        self.assertEqual(route.department.name, "Clinicals (Nursing)")

    def test_it_never_appears_in_the_financial_report(self):
        """
        A department only reaches the report by carrying a charge, and nursing
        raises none. This is the guard on the whole decision: if somebody later
        adds it to the revenue registry, this fails.
        """
        from apps.billing.reporting import department_for

        key, _ = department_for("", department_code="clinicals",
                                department_name="Clinicals (Nursing)")
        # Unmapped, so it would only ever be bucketed by its own FK — which no
        # charge carries, because nothing bills nursing.
        self.assertEqual(key, "department:clinicals")
        self.assertFalse(Charge.objects.filter(department__code="clinicals").exists())

    def test_re_running_the_seed_leaves_a_renamed_one_alone(self):
        module = importlib.import_module(
            "apps.departments.migrations.0003_seed_clinical_departments")
        clinicals = Department.objects.get(code="clinicals")
        clinicals.name = "Nursing Station"
        clinicals.is_active = False
        clinicals.save(update_fields=["name", "is_active"])

        module.seed(django_apps, None)

        clinicals.refresh_from_db()
        self.assertEqual((clinicals.name, clinicals.is_active), ("Nursing Station", False))
        self.assertEqual(Department.objects.filter(code="clinicals").count(), 1)


class MigrationLiteralTests(TestCase):
    """
    The migration's literal list must match `billing.departments`.

    A migration is self-contained by necessity — it cannot import application
    code that may change under it — so the codes and names exist twice. This
    is what stops the two copies drifting apart in silence.
    """

    def test_codes_and_names_match_the_application_registry(self):
        self.assertEqual(
            sorted(seeded_literals()),
            sorted((code, name) for code, name, _ in REVENUE_DEPARTMENTS),
            "the seed migrations and billing.departments have drifted apart",
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
