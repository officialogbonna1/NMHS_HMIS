"""
Turn appointment booking on for the departments that already offer it.

The field defaults to False — a department is not an appointment destination
until somebody says it is — which on its own would have switched the feature
off for every hospital the moment this migration ran. So this turns it on
exactly where it was already true: a department is made available if at least
one active, appointment-bookable `BillingItem` resolves to it.

**Derived, not listed.** There is no roll-call of departments here. The
services ticked by `billing/0021` are what decide, so this migration says
"keep working what was working" rather than encoding an opinion about which
units take appointments. A hospital that had ticked a laboratory service
before upgrading gets Laboratory switched on too, which is the right answer
for them and not something a literal list could have known.

**Consultation is switched on whether or not anything is ticked under it.**
The general consultation — patient, doctor, reason, no service, no charge — is
the booking Reception has always made, and it belongs to that department
(`booking.GENERAL_CONSULTATION`). It needs no catalogue row, so on a fresh
install there is none to derive from: a hospital that had never priced a
consultation fee would have come up with the front desk unable to queue
anybody. That is the one code named here, and it is named because the path
exists in the product rather than in anyone's configuration.

The category → department code map is repeated as literals because a migration
has to be self-contained — the same reason `departments/0002` repeats the
revenue registry rather than importing `billing/departments.py`.

It never turns anything **off**, and reversing it is a no-op: after this runs,
the switch belongs to the administrator in Django admin, and a migration must
not overwrite a decision somebody has since made.
"""
from django.db import migrations

# `BillingItem.category` -> `Department.code`, as `billing/departments.py`
# resolves it. Repeated here so this migration stands alone.
CATEGORY_DEPARTMENT = {
    "card": "reception",
    "consultation": "consultation",
    "laboratory": "laboratory",
    "ultrasound": "radiology",
    "eye": "eye",
    "procedure": "theatre",
}


def switch_on_what_already_works(apps, schema_editor):
    BillingItem = apps.get_model("billing", "BillingItem")
    Department = apps.get_model("departments", "Department")

    categories = set(
        BillingItem.objects.filter(is_active=True, is_appointment_service=True)
        .values_list("category", flat=True)
    )
    codes = {CATEGORY_DEPARTMENT[category] for category in categories
             if category in CATEGORY_DEPARTMENT}
    # The general consultation needs no catalogue row, so it cannot be derived
    # from one. See the note above.
    codes.add("consultation")
    Department.objects.filter(code__in=codes).update(is_appointment_available=True)


def leave_the_configuration_alone(apps, schema_editor):
    """Reversing the schema must not reverse somebody's configuration."""


class Migration(migrations.Migration):

    dependencies = [
        ("departments", "0004_department_is_appointment_available"),
        # The services this reads are ticked by that migration.
        ("billing", "0021_seed_appointment_services"),
    ]

    operations = [
        migrations.RunPython(switch_on_what_already_works, leave_the_configuration_alone),
    ]
