from decimal import Decimal

from django.db import migrations


def backfill(apps, schema_editor):
    """
    Reconstruct which charge each existing payment settled.

    `PaymentAllocation` arrived after money had already been taken, and the
    allocation that moved `Charge.amount_paid` left no record of itself. This
    replays the same rule going backwards — each patient's payments oldest
    first, spread across their charges oldest first, capped at what each
    charge actually shows as paid — so a report over a past period has
    something to group by instead of a blank.

    It is a reconstruction, not the original event, and it is honest about
    where it can be wrong: where a discount or a waiver landed *between* two
    payments, the true allocation may have differed. The totals are right
    either way — the sum of a payment's allocations never exceeds the payment,
    and the sum of a charge's allocations never exceeds its `amount_paid` —
    so no figure is invented; only its attribution is inferred.
    """
    Charge = apps.get_model("billing", "Charge")
    Payment = apps.get_model("billing", "Payment")
    PaymentAllocation = apps.get_model("billing", "PaymentAllocation")

    if PaymentAllocation.objects.exists():
        return

    rows = []
    for patient_id in sorted(set(Payment.objects.values_list("patient_id", flat=True))):
        # What each charge is recorded as having collected — the ceiling this
        # replay must not exceed.
        capacity = {
            charge.id: Decimal(charge.amount_paid)
            for charge in Charge.objects.filter(patient_id=patient_id).order_by("created_at", "id")
            if charge.amount_paid and charge.amount_paid > 0
        }
        order = list(capacity)

        for payment in Payment.objects.filter(patient_id=patient_id).order_by("created_at", "id"):
            remaining = Decimal(payment.amount)
            for charge_id in order:
                if remaining <= 0:
                    break
                room = capacity.get(charge_id, Decimal("0"))
                if room <= 0:
                    continue
                take = min(room, remaining)
                capacity[charge_id] = room - take
                remaining -= take
                rows.append(PaymentAllocation(
                    payment_id=payment.id, charge_id=charge_id, amount=take,
                    created_at=payment.created_at, updated_at=payment.created_at,
                ))

    if rows:
        PaymentAllocation.objects.bulk_create(rows, batch_size=500)


def unbackfill(apps, schema_editor):
    apps.get_model("billing", "PaymentAllocation").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("billing", "0013_paymentallocation_and_more")]
    operations = [migrations.RunPython(backfill, unbackfill)]
