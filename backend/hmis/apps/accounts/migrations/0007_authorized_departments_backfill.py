"""
Give every existing account the authorised department its old text names.

**No schema changes.** `Department.staff` is a `ManyToManyField` to `User` and
has been since `departments/0001`, so the relation multi-department
authorisation needs was already here — this migration only fills it in, so
that nobody has to repair a staff account by hand after deployment.

What it does: for each active-or-inactive user whose free-text
`User.department` matches a department's `code` or `name` (case-insensitively,
trimmed), add them to that department's `staff`. `add()` on a many-to-many is
idempotent, so a user already in the relation is untouched and the migration
is safe to re-run.

What it deliberately does not do:

- **Guess.** Text that matches no department is left exactly as it is. A
  hospital types "Front Desk" and "ANC clinic" into that column, and inventing
  a department from a string is how a person ends up authorised somewhere
  nobody put them. Those accounts keep working: `accounts/departments.py`
  honours the text as a fallback either way.
- **Clear the text.** `User.department` stays as the primary/organisational
  label it has always been, on `/auth/me/`, the Users page and the directory.
- **Remove anything.** It only ever adds, so an administrator who has already
  curated `Department.staff` finds it as they left it.

Reversing it is a no-op on purpose: the relation it fills is also the one
administrators edit by hand, and a reverse that emptied it would throw away
work this migration never did.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Department = apps.get_model("departments", "Department")

    departments = list(Department.objects.all())
    if not departments:
        return
    by_label = {}
    for department in departments:
        for label in (department.code, department.name):
            if label:
                by_label[label.strip().lower()] = department

    for user in User.objects.exclude(department="").exclude(department=None):
        department = by_label.get((user.department or "").strip().lower())
        if department is not None:
            department.staff.add(user)


def unbackfill(apps, schema_editor):
    """Deliberately nothing — see the module docstring."""


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_alter_user_role"),
        # The departments have to exist before anybody can be posted to one.
        ("departments", "0006_seed_maternity_department"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
