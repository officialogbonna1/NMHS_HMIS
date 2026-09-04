from decimal import Decimal

from django.db import migrations

# One priced item per service category, so laboratory, ultrasound, eye and
# procedure arrive usable the way Card and Consultation already were —
# instead of an empty dropdown at the counter on day one.
#
# These are starters, not the hospital's real prices. They are edited under
# Billing Catalog, and adding the rest of the price list is done there too.
STARTERS = [
    ("laboratory", "Full Blood Count", "3500"),
    ("ultrasound", "Abdominal Ultrasound", "8000"),
    ("eye", "Eye Test (Refraction)", "3000"),
    ("procedure", "Wound Dressing", "2500"),
]


def add_starters(apps, schema_editor):
    BillingItem = apps.get_model("billing", "BillingItem")
    for category, name, price in STARTERS:
        # Skip a category somebody has already priced: this must not add
        # noise to a catalogue that is being kept by hand.
        if BillingItem.objects.filter(category=category).exists():
            continue
        BillingItem.objects.get_or_create(
            name=name,
            defaults={"category": category, "price": Decimal(price), "is_active": True},
        )


def remove_starters(apps, schema_editor):
    BillingItem = apps.get_model("billing", "BillingItem")
    # Only the untouched ones: a price somebody has edited is theirs now.
    for category, name, price in STARTERS:
        BillingItem.objects.filter(category=category, name=name, price=Decimal(price)).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0009_alter_billingitem_category"),
    ]

    operations = [
        migrations.RunPython(add_starters, remove_starters),
    ]
