from decimal import Decimal

from django.db import migrations


def allocate_existing_payments(apps, schema_editor):
    """
    Charge.amount_paid arrived after money had already been taken, so every
    existing charge reads unpaid however much was collected. Spread each
    patient's payments over their open charges oldest-first — the same rule
    billing.services.allocate_to_charges applies from now on.

    A patient who paid more than they were charged keeps the excess as an
    unallocated credit rather than having a charge marked overpaid.
    """
    Charge = apps.get_model("billing", "Charge")
    Payment = apps.get_model("billing", "Payment")
    patient_ids = set(Payment.objects.values_list("patient_id", flat=True))

    for patient_id in patient_ids:
        paid = sum((p.amount for p in Payment.objects.filter(patient_id=patient_id)), Decimal("0"))
        charges = Charge.objects.filter(
            patient_id=patient_id, status__in=["unpaid", "partial"]
        ).order_by("created_at")
        for charge in charges:
            if paid <= 0:
                break
            due = charge.amount - charge.amount_paid
            if due <= 0:
                continue
            take = min(due, paid)
            charge.amount_paid += take
            charge.status = "paid" if charge.amount_paid >= charge.amount else "partial"
            charge.save(update_fields=["amount_paid", "status"])
            paid -= take


def unallocate(apps, schema_editor):
    Charge = apps.get_model("billing", "Charge")
    Charge.objects.filter(status__in=["paid", "partial"]).update(amount_paid=0, status="unpaid")


class Migration(migrations.Migration):
    dependencies = [("billing", "0005_charge_amount_paid")]
    operations = [migrations.RunPython(allocate_existing_payments, unallocate)]
