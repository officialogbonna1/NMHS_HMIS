"""
Charges waived before `amount_waived` existed only carried a status.

Their `balance` therefore still read as money owed — the ledger knew about
the waiver (it counts the Adjustment) but the charge itself did not. This
copies each waiver Adjustment onto the charge it forgave, so old and new
waivers describe themselves the same way.
"""
from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def backfill(apps, schema_editor):
    Charge = apps.get_model("billing", "Charge")
    Adjustment = apps.get_model("billing", "Adjustment")

    waived = (Adjustment.objects.filter(kind="waiver", charge__isnull=False)
              .values("charge").annotate(total=Sum("amount")))
    for row in waived:
        charge = Charge.objects.filter(pk=row["charge"]).first()
        if charge is None or charge.amount_waived:
            continue
        # Never more than was actually outstanding — a waiver recorded against
        # a charge that was later part-paid must not push it into credit.
        room = charge.amount - charge.amount_paid - charge.amount_discounted
        charge.amount_waived = min(row["total"] or Decimal("0"), max(room, Decimal("0")))
        charge.save(update_fields=["amount_waived"])


def unbackfill(apps, schema_editor):
    # The column is going away with the previous migration; nothing to undo.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0011_charge_amount_waived_paymentdeferral"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
