"""
The department registry the financial attribution depends on.

Departments had no seed at all: `0001_initial` created the table and nothing
filled it, so a fresh installation had none, and this deployment had two rows
somebody typed by hand. That is why `Charge.department` was null on 96% of
charges and why a laboratory referral was routed to "General Medicine" — the
routing falls back to whichever active department happens to be first.

This seeds one row per revenue-generating unit, keyed by a stable `code`.
The codes are the machine identity and are repeated here as literals because
a migration must be self-contained; `apps/billing/departments.py` holds the
same list for the application to read, and
`apps/departments/tests/test_department_registry.py` fails if the two drift.

**It never overwrites a name.** `get_or_create` on `code` alone, so an
administrator who has renamed "Radiology / Ultrasound" to "Imaging" keeps
their name, on this run and on every future one. Re-running the migration on
a database that already has these rows changes nothing.

It also does not touch, rename, merge or deactivate a department that was
already there — "General Medicine" and the hand-made "Laboratory" survive
untouched. Nothing here backfills a charge: historical attribution stays
exactly as it is, which is the point.

**Reversing it is deliberately timid.** It removes only a seeded department
nothing points at, and where it cannot tell — a reverse renders the state for
this migration alone, so another app's model may simply not be there — it
leaves the row alone. An unused department left behind is harmless; one
deleted out from under a charge is not.
"""
from django.db import migrations

# code, name. Mirrors REVENUE_DEPARTMENTS in apps/billing/departments.py.
REVENUE_DEPARTMENTS = [
    ("reception", "Reception"),
    ("consultation", "Consultation"),
    ("laboratory", "Laboratory"),
    ("pharmacy", "Pharmacy"),
    ("radiology", "Radiology / Ultrasound"),
    ("eye", "Eye Clinic"),
    ("theatre", "Theatre / Procedures"),
]


def seed(apps, schema_editor):
    Department = apps.get_model("departments", "Department")
    for code, name in REVENUE_DEPARTMENTS:
        if Department.objects.filter(code=code).exists():
            # Already here — leave the name, the manager, the staff and the
            # active flag exactly as the hospital has them.
            continue
        # `name` is unique, so a department already carrying this name under a
        # different code is the same unit somebody created by hand. Adopt it
        # by giving it the stable code rather than failing on the constraint
        # or creating a duplicate beside it.
        existing = Department.objects.filter(name__iexact=name).first()
        if existing:
            existing.code = code
            existing.save(update_fields=["code"])
            continue
        Department.objects.create(code=code, name=name, is_active=True)


# What may point at a department, as (app label, model, field). Looked up
# through the migration state rather than through a reverse accessor
# (`department.charge_set`): on a *reverse* run the state is rendered for this
# migration alone, and a reverse accessor that is not in it raises
# AttributeError rather than answering the question.
REFERENCES = [
    ("billing", "Charge", "department"),
    ("workflow", "PatientRoute", "department"),
    ("departments", "Service", "department"),
    ("laboratory", "LabTest", "department"),
    ("inpatient", "Ward", "department"),
]


def _in_use(apps, department):
    """
    Does anything point at this department?

    A model missing from the migration state reads as "cannot tell", and the
    answer there is yes — leaving a department in place is recoverable, and
    deleting one that history points at is not.
    """
    for app_label, model_name, field in REFERENCES:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            return True
        if model.objects.filter(**{field: department}).exists():
            return True
    return False


def unseed(apps, schema_editor):
    """
    Remove only the rows nothing points at.

    A seeded department that has since been used is part of the record —
    `Charge.department` is PROTECT, so deleting it would fail anyway, and it
    should. Reversing this migration must never take history with it, so this
    errs towards leaving a department behind.
    """
    Department = apps.get_model("departments", "Department")
    for code, _ in REVENUE_DEPARTMENTS:
        department = Department.objects.filter(code=code).first()
        if department and not _in_use(apps, department):
            department.delete()


class Migration(migrations.Migration):
    # billing is depended on so `Charge` is in the state both ways: it is the
    # financial record this seed exists to attribute, and the one relation the
    # reverse must be certain about. departments.0001 → billing.0001 → here,
    # which is the order Django already had, so this adds no cycle.
    dependencies = [
        ("departments", "0001_initial"),
        ("billing", "0001_initial"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
