"""
The ultrasound examinations a maternity hospital starts with.

**Seed data, not a definition.** Every row lands in `BillingItem` as ordinary
configuration and is the hospital's from that moment: rename it, re-price it,
retire it — from the Billing Catalog page or Django admin, with no code change
and no migration. Nothing in the application reads this list; the doctor's
referral screen and Reception's billing panel both read the catalogue.

**Why `BillingItem` and not a catalogue of its own.** A laboratory test needed
a model (`LabTest`) because it carries a specimen, a container and a page of
parameters with reference ranges. An ultrasound examination carries a name and
a price, which is exactly what the billing catalogue already is — and it is
the list Reception bills from, so putting the examinations anywhere else would
recreate the split this work exists to close: the doctor ordering from one
book and the desk billing from another.

The rules are the ones every seed in this codebase follows: never overwrite a
name or a price an administrator has edited, never reactivate a row they have
retired, adopt a hand-made row carrying the same name rather than failing the
unique constraint beside it, and reverse only what nothing points at.

The prices are a starting price list, in naira, in the same spirit as the
laboratory catalogue's. They are the first thing a hospital changes.
"""
from decimal import Decimal

from django.db import migrations

CATEGORY = "ultrasound"

# name, starting price. The studies this hospital actually performs — a
# maternity and general practice list, not a radiology department's.
EXAMINATIONS = [
    ("Abdominal Ultrasound", "8000"),
    ("Abdominopelvic Ultrasound", "10000"),
    ("Pelvic Ultrasound", "8000"),
    ("Transvaginal Ultrasound", "10000"),
    ("Obstetric Ultrasound (Dating)", "8000"),
    ("Obstetric Ultrasound (Anomaly Scan)", "15000"),
    ("Obstetric Ultrasound (Growth / Wellbeing)", "10000"),
    ("Biophysical Profile", "12000"),
    ("Renal / Urinary Tract Ultrasound", "8000"),
    ("Breast Ultrasound", "10000"),
    ("Prostate Ultrasound", "10000"),
    ("Scrotal Ultrasound", "10000"),
    ("Thyroid / Neck Ultrasound", "10000"),
    ("Soft Tissue Ultrasound", "8000"),
    ("Doppler Study (Obstetric)", "15000"),
    ("Doppler Study (Venous / Arterial)", "15000"),
    ("Follicular Tracking Scan", "6000"),
    ("Early Pregnancy / Viability Scan", "7000"),
]


def seed(apps, schema_editor):
    BillingItem = apps.get_model("billing", "BillingItem")
    for name, price in EXAMINATIONS:
        existing = BillingItem.objects.filter(name=name).first()
        if existing is not None:
            # Somebody already priced this — theirs wins, including the
            # category they filed it under and whether it is still offered.
            continue
        BillingItem.objects.create(name=name, category=CATEGORY,
                                   price=Decimal(price), is_active=True)


def unseed(apps, schema_editor):
    """
    Remove only the untouched rows nothing points at. A row that has been
    re-priced, renamed into another category or billed to a patient stays:
    reversing a migration must not take a service or its history with it.
    """
    BillingItem = apps.get_model("billing", "BillingItem")
    for name, price in EXAMINATIONS:
        row = BillingItem.objects.filter(name=name, category=CATEGORY,
                                         price=Decimal(price)).first()
        if row is None or row.lab_tests.exists() or row.route_services.exists():
            continue
        row.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0018_pos_payments_and_returns"),
        # `route_services` is checked above before a row is removed.
        ("workflow", "0005_routeservice_routeservice_unique_route_service_item"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
