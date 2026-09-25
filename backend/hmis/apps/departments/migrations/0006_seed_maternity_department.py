"""
Maternity is a department of the hospital, and it is not a revenue one.

`departments/0003` seeded Clinicals (Nursing) on exactly this reasoning and
this is the second instance of it: a unit patients are sent to needs a
`Department` row so a route, an encounter and a printed form can say where she
actually went. What it must **not** join is `billing/departments.py`'s
`REVENUE_DEPARTMENTS`, which is held at seven by
`apps/departments/tests/test_department_registry.py` — maternity raises its
money through services that already have departments of their own (an ANC
consultation is a Consultation charge, a scan is Radiology, a test is
Laboratory), so an entry there would be a permanently empty column in every
financial report.

Seeding rules are `0002`'s, unchanged: adopt a hand-made row by name rather
than creating a second one (`name` is unique), never overwrite an edited name,
never reactivate a retired department, and reverse only what nothing points at.
"""
from django.db import migrations

CODE = "maternity"
NAME = "Maternity"


def seed(apps, schema_editor):
    Department = apps.get_model("departments", "Department")
    if Department.objects.filter(code=CODE).exists():
        return
    existing = Department.objects.filter(name=NAME).first()
    if existing is not None:
        # A hand-made row already carries the name. Give it the stable code
        # rather than failing the unique constraint with a duplicate.
        existing.code = CODE
        existing.save(update_fields=["code"])
        return
    Department.objects.create(code=CODE, name=NAME, is_active=True)


def unseed(apps, schema_editor):
    """Remove it only where nothing has been filed against it."""
    Department = apps.get_model("departments", "Department")
    department = Department.objects.filter(code=CODE).first()
    if department is not None and not department.routes.exists() \
            and not department.charge_set.exists():
        department.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("departments", "0005_seed_appointment_availability"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
