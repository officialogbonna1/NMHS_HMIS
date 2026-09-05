"""
Seed the two operational locations, and put the stock that already exists
into one of them.

**Which one, and why.** Before this migration the hospital had a single
undifferentiated pile: one `Batch.quantity`, which `dispense_prescription()`
drew from directly. So whatever that number was, it was in practice the
dispensing shelf — it is what patients were being given from. Moving it to
Main Store would have emptied the Pharmacy overnight and stopped every
prescription that worked yesterday from being fillable today.

Existing stock therefore lands in **Pharmacy**, and every historical
`StockMovement` is stamped with Pharmacy for the same reason: that is where
those units actually moved. New deliveries land in **Main Store** and reach
the Pharmacy by transfer, which is the flow from here on.

If a hospital would rather open with its stock in the store, the correction
is a transfer — Pharmacy → Main Store — which is exactly the operation this
whole change exists to make auditable.
"""
from django.db import migrations

LOCATIONS = [
    {
        "code": "main-store",
        "name": "Main Store",
        "kind": "store",
        "description": "Central stockholding. Supplier deliveries are received here.",
        "is_default_receiving": True,
        "is_dispensing_point": False,
        "display_order": 10,
    },
    {
        "code": "pharmacy",
        "name": "Pharmacy",
        "kind": "dispensary",
        "description": "Dispensing shelf. Stock arrives by transfer from Main Store.",
        "is_default_receiving": False,
        "is_dispensing_point": True,
        "display_order": 20,
    },
]


def seed_and_move(apps, schema_editor):
    StockLocation = apps.get_model("inventory", "StockLocation")
    StockRecord = apps.get_model("inventory", "StockRecord")
    Batch = apps.get_model("inventory", "Batch")
    StockMovement = apps.get_model("inventory", "StockMovement")

    for row in LOCATIONS:
        StockLocation.objects.update_or_create(code=row["code"], defaults=row)

    pharmacy = StockLocation.objects.get(code="pharmacy")

    # One stock record per batch that is holding units. A batch at zero gets
    # no row: "no row" and "zero" mean the same thing, and the row appears
    # the moment something is transferred in.
    StockRecord.objects.bulk_create([
        StockRecord(batch=batch, location=pharmacy, quantity=batch.quantity)
        for batch in Batch.objects.filter(quantity__gt=0)
    ])

    # Every movement already recorded happened on that same shelf.
    StockMovement.objects.filter(location__isnull=True).update(location=pharmacy)


def unseed(apps, schema_editor):
    """
    Reverse: fold the locations back into the single column.

    Summing across locations is the only sane way back — the old schema has
    nowhere to put the split — so a down-migration loses which shelf stock
    was on. It keeps the totals right, which is what a rollback needs.
    """
    from django.db.models import Sum

    StockRecord = apps.get_model("inventory", "StockRecord")
    Batch = apps.get_model("inventory", "Batch")
    StockLocation = apps.get_model("inventory", "StockLocation")

    totals = (StockRecord.objects.values("batch_id")
              .annotate(total=Sum("quantity")))
    for row in totals:
        Batch.objects.filter(pk=row["batch_id"]).update(quantity=row["total"] or 0)

    StockRecord.objects.all().delete()
    StockLocation.objects.filter(code__in=[row["code"] for row in LOCATIONS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0003_stock_locations"),
    ]

    operations = [
        migrations.RunPython(seed_and_move, unseed),
    ]
