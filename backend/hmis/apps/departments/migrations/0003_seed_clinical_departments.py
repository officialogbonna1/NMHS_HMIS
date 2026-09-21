"""
The units that treat patients without billing them.

`0002` seeded the seven **revenue** departments — the ones
`billing/departments.py` attributes money to. Nursing is not one of them and
must never be added to that registry: a nurse takes a patient's vitals and
raises no charge, so a nursing row in `REVENUE_DEPARTMENTS` would put a column
that is permanently zero into every financial report, and
`apps/departments/tests/test_department_registry.py` holds that list at seven
for exactly that reason.

But a department is not only a revenue bucket. `PatientRoute.department` is a
required foreign key, so **every** route the front desk raises has to name
one — and there was nowhere to send a patient for vitals. Reception's
department list read Consultation, Eye Clinic, General Medicine, Laboratory,
Pharmacy, Radiology / Ultrasound, Reception, Theatre / Procedures: a vitals
route had to be filed against whichever of those was least wrong.

Routing itself is unaffected either way — `purpose` is what decides who the
patient reaches (rule 16), and unassigned vitals work is broadcast to every
active nurse whatever department the route names. What the department does is
say, on the queue and on the printed referral form, which unit the patient was
sent to. That is worth being true.

The rules are `0002`'s, because they are the rules for seeding any
configuration row: never overwrite a name an administrator has edited, never
reactivate a department they have retired, adopt a hand-made row carrying the
same name rather than failing the unique constraint beside it, and reverse
only what nothing points at.
"""
from django.db import migrations

# code, name. Deliberately *not* in `billing/departments.py` — see above.
CLINICAL_DEPARTMENTS = [
    ("clinicals", "Clinicals (Nursing)"),
]


def seed(apps, schema_editor):
    Department = apps.get_model("departments", "Department")
    for code, name in CLINICAL_DEPARTMENTS:
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
# through the migration state rather than through a reverse accessor: on a
# *reverse* run the state is rendered for this migration alone, and a reverse
# accessor that is not in it raises AttributeError rather than answering.
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
    """Remove only the rows nothing points at — a department a patient has
    been routed to is part of the record."""
    Department = apps.get_model("departments", "Department")
    for code, _ in CLINICAL_DEPARTMENTS:
        department = Department.objects.filter(code=code).first()
        if department and not _in_use(apps, department):
            department.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("departments", "0002_seed_revenue_departments"),
        ("billing", "0001_initial"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
