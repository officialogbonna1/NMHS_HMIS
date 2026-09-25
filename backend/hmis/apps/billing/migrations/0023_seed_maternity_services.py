"""
The maternity price list the hospital had not configured yet.

These are ordinary `BillingItem` rows under the `maternity` category, so the
counter bills them exactly as it bills a consultation fee or a scan: the same
picker (`billing/catalogue.py`), the same `add_charge`, the same ledger,
payments, deferrals, discounts, waivers, refunds and reports. Nothing here is
a maternity billing path.

**Seeding rules are `departments/0002`'s, unchanged**: never overwrite a row
the hospital has edited, never reactivate one an administrator retired, and
adopt by name rather than creating a duplicate beside it — `BillingItem.name`
is unique, so a second "Delivery Package" would fail the constraint anyway.

**The prices are a starting point, not a decision.** A hospital sets its own
fees on Administration → Billing Catalog, and this migration never touches a
price again once the row exists.
"""
from decimal import Decimal

from django.db import migrations

CATEGORY = "maternity"

#: name, price. Ordered as the ward works: booking, the antenatal course,
#: the birth, and the care after it.
SERVICES = [
    ("ANC Booking Visit", Decimal("5000.00")),
    ("ANC Follow-up Visit", Decimal("2000.00")),
    ("Obstetric Ultrasound", Decimal("8000.00")),
    ("Delivery Package — Normal", Decimal("45000.00")),
    ("Delivery Package — Caesarean Section", Decimal("150000.00")),
    ("Postnatal Visit", Decimal("2500.00")),
    ("Newborn Care", Decimal("5000.00")),
]


def seed(apps, schema_editor):
    BillingItem = apps.get_model("billing", "BillingItem")
    for name, price in SERVICES:
        if BillingItem.objects.filter(name=name).exists():
            # Already here — leave the price, the category and the active flag
            # exactly as the hospital has them.
            continue
        BillingItem.objects.create(name=name, category=CATEGORY, price=price,
                                   is_active=True)


def unseed(apps, schema_editor):
    """Remove only the rows nothing has been billed against."""
    BillingItem = apps.get_model("billing", "BillingItem")
    Charge = apps.get_model("billing", "Charge")
    for name, _ in SERVICES:
        item = BillingItem.objects.filter(name=name, category=CATEGORY).first()
        if item is None:
            continue
        # A charge records the service by name and source type, not by a
        # foreign key, so "in use" is asked the way the report asks it.
        if Charge.objects.filter(source_type=CATEGORY, description__icontains=name).exists():
            continue
        item.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0022_alter_billingitem_category"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
