"""
**Procedure and Theatre are two departments** (rule 59).

`departments/0002` seeded one row, code `theatre`, named "Theatre /
Procedures", and the procedure workflow was built against it. A procedure room
and an operating theatre are different units with different staff, so this
splits them — additively, on `0002`'s rules:

1. **Procedure is created** (code `procedure`, name "Procedure"). A hand-made
   row already carrying the name is adopted by giving it the code rather than
   failing the unique constraint; an existing `procedure` row is left exactly
   as it is.
2. **Theatre keeps its row, its code, its staff and its history** — only its
   seeded label changes, "Theatre / Procedures" → "Theatre", and only if
   nobody has edited it. A name an administrator chose is never overwritten.
3. **Procedure work filed under Theatre moves to Procedure**, and nothing
   else does: a `PatientRoute` with `purpose="procedure"`, and a `Charge`
   with `source_type="procedure"` — the authoritative source-type map
   (`billing/departments.py`, rule 33) now resolves `procedure` to this
   department, so its attribution follows. Amounts, payments, allocations and
   every other column are untouched; theatre and surgery work stays on Theatre.
   Staff are **not** copied: Procedure membership is a separate decision an
   administrator makes in Django admin or on the Departments page.

Reversible: the moved rows go back to Theatre, the label is restored if it
still reads "Theatre", and Procedure is removed only where nothing points at it.
"""
from django.db import migrations

CODE = "procedure"
NAME = "Procedure"
THEATRE_CODE = "theatre"
#: The seeded label, and what it becomes — the drift test applies this.
THEATRE_RENAME = ("Theatre / Procedures", "Theatre")


def separate(apps, schema_editor):
    Department = apps.get_model("departments", "Department")
    PatientRoute = apps.get_model("workflow", "PatientRoute")
    Charge = apps.get_model("billing", "Charge")

    procedure = Department.objects.filter(code=CODE).first()
    if procedure is None:
        procedure = Department.objects.filter(name=NAME).first()
        if procedure is not None:
            procedure.code = CODE
            procedure.save(update_fields=["code"])
        else:
            procedure = Department.objects.create(code=CODE, name=NAME, is_active=True)

    theatre = Department.objects.filter(code=THEATRE_CODE).first()
    if theatre is None:
        return
    old, new = THEATRE_RENAME
    if theatre.name == old and not Department.objects.filter(name=new).exists():
        theatre.name = new
        theatre.save(update_fields=["name"])

    PatientRoute.objects.filter(department=theatre, purpose="procedure").update(department=procedure)
    Charge.objects.filter(department=theatre, source_type="procedure").update(department=procedure)


def rejoin(apps, schema_editor):
    Department = apps.get_model("departments", "Department")
    PatientRoute = apps.get_model("workflow", "PatientRoute")
    Charge = apps.get_model("billing", "Charge")

    theatre = Department.objects.filter(code=THEATRE_CODE).first()
    procedure = Department.objects.filter(code=CODE).first()
    if theatre is not None and procedure is not None:
        PatientRoute.objects.filter(department=procedure, purpose="procedure").update(department=theatre)
        Charge.objects.filter(department=procedure, source_type="procedure").update(department=theatre)
    if theatre is not None:
        old, new = THEATRE_RENAME
        if theatre.name == new and not Department.objects.filter(name=old).exists():
            theatre.name = old
            theatre.save(update_fields=["name"])
    if procedure is not None and not procedure.routes.exists() \
            and not procedure.charge_set.exists() and not procedure.staff.exists():
        procedure.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("departments", "0006_seed_maternity_department"),
        ("workflow", "0007_alter_patientroute_purpose"),
        ("billing", "0023_seed_maternity_services"),
    ]

    operations = [migrations.RunPython(separate, rejoin)]
