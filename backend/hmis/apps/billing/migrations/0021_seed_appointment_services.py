"""
Turn on appointment booking for the units that take appointments today.

The flag itself defaults to False — a priced row is something the counter can
bill, which is not the same as something a patient can be queued for. This
migration ticks it once, for the three categories whose services are the
hospital's existing appointment destinations: the consultation fee, the eye
clinic and the imaging examinations.

**Laboratory and Theatre / Procedures are deliberately left off.** They are
appointment-capable only for specific services somebody configures, which is
one tick in Django admin or on the Billing Catalog page — not a default that
would put sixty-six laboratory tests on the booking form.

It never un-ticks anything and it never runs twice in a way that would undo an
administrator's decision: the reverse is a no-op, for the same reason the
department seeds are (rule 34). Turning a service off afterwards is
configuration, and configuration wins.
"""
from django.db import migrations

# The categories whose services are appointment destinations out of the box.
# `category` is what already decides the department and the eligible
# providers, so this list is the whole of the default configuration.
DEFAULT_BOOKABLE = ("consultation", "eye", "ultrasound")


def tick_the_defaults(apps, schema_editor):
    BillingItem = apps.get_model("billing", "BillingItem")
    BillingItem.objects.filter(category__in=DEFAULT_BOOKABLE).update(is_appointment_service=True)


def leave_it_alone(apps, schema_editor):
    """Reversing the schema must not reverse somebody's configuration."""


class Migration(migrations.Migration):

    dependencies = [("billing", "0020_billingitem_is_appointment_service")]

    operations = [migrations.RunPython(tick_the_defaults, leave_it_alone)]
