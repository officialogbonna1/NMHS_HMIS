from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from .models import PatientLedger, Charge, Payment, Adjustment

@transaction.atomic
def refresh_ledger(patient):
    ledger, _ = PatientLedger.objects.select_for_update().get_or_create(patient=patient)
    ledger.total_charges = Charge.objects.filter(patient=patient).exclude(status="cancelled").aggregate(v=Sum("amount"))["v"] or Decimal("0")
    ledger.total_payments = Payment.objects.filter(patient=patient).aggregate(v=Sum("amount"))["v"] or Decimal("0")
    credits = Adjustment.objects.filter(patient=patient, kind__in=["discount", "waiver"]).aggregate(v=Sum("amount"))["v"] or Decimal("0")
    refunds = Adjustment.objects.filter(patient=patient, kind="refund").aggregate(v=Sum("amount"))["v"] or Decimal("0")
    ledger.total_adjustments = credits - refunds; ledger.save(); return ledger

@transaction.atomic
def add_charge(*, patient, description, amount, created_by, department=None, source_type="", source_id=None):
    charge = Charge.objects.create(patient=patient, description=description, amount=amount, created_by=created_by, department=department, source_type=source_type, source_id=source_id)
    refresh_ledger(patient); return charge

@transaction.atomic
def allocate_to_charges(*, patient, amount):
    """
    Spread a payment across the patient's open charges, oldest first, and move
    each one to part-paid or paid. Returns the amount that found no charge to
    settle (zero in the normal case, since record_payment caps a payment at
    what is owed).
    """
    remaining = Decimal(amount)
    charges = Charge.objects.select_for_update().filter(
        patient=patient, status__in=["unpaid", "partial"]
    ).order_by("created_at")
    for charge in charges:
        if remaining <= 0: break
        due = charge.amount - charge.amount_paid - charge.amount_discounted
        if due <= 0:
            continue
        take = min(due, remaining)
        charge.amount_paid += take
        charge.status = _settled_status(charge)
        charge.save(update_fields=["amount_paid", "status"])
        remaining -= take
    return remaining

def _percent_label(percent):
    """10 not 1E+1, 12.5 not 12.50 — this ends up in the reason a human reads."""
    return f"{percent.normalize():f}"

def _settled_status(charge):
    """Discounted money counts as settled — it is money nobody will collect."""
    covered = charge.amount_paid + charge.amount_discounted
    if covered >= charge.amount:
        return "paid"
    return "partial" if covered > 0 else "unpaid"

@transaction.atomic
def record_payment(*, patient, amount, received_by, method="cash", reference="", channel="front_desk"):
    amount = Decimal(amount)
    if amount <= 0: raise ValueError("Payment amount must be greater than zero.")
    ledger, _ = PatientLedger.objects.select_for_update().get_or_create(patient=patient)
    if ledger.outstanding_balance <= 0:
        raise ValueError("This patient has no outstanding balance.")
    if amount > ledger.outstanding_balance:
        # Never let a payment push the ledger negative — cap what reception
        # can record to what is actually owed.
        raise ValueError(f"This patient's outstanding balance is {ledger.outstanding_balance}; a payment of {amount} would exceed it.")
    payment = Payment.objects.create(patient=patient, amount=amount, received_by=received_by, method=method, reference=reference, channel=channel)
    allocate_to_charges(patient=patient, amount=amount)
    refresh_ledger(patient); return payment

@transaction.atomic
def apply_percentage_discount(*, charge, percent, reason, approved_by):
    """
    Take a percentage off one charge. The amount comes from the charge's face
    value, but is capped at what is still owed on it — discounting money the
    patient already handed over would put the ledger into credit.
    """
    if charge.status in ("waived", "cancelled"):
        raise ValueError(f"Charge is already {charge.status}.")
    percent = Decimal(str(percent))
    if percent <= 0 or percent > 100:
        raise ValueError("Discount must be between 0 and 100 percent.")
    if not reason:
        raise ValueError("A reason is required for a discount.")

    locked = Charge.objects.select_for_update().get(pk=charge.pk)
    outstanding = locked.amount - locked.amount_paid - locked.amount_discounted
    if outstanding <= 0:
        raise ValueError("This charge has nothing left to discount.")

    amount = (locked.amount * percent / Decimal("100")).quantize(Decimal("0.01"))
    amount = min(amount, outstanding)

    adjustment = Adjustment.objects.create(
        patient=locked.patient, charge=locked, kind="discount", amount=amount,
        reason=f"{_percent_label(percent)}% — {reason}", approved_by=approved_by,
    )
    locked.amount_discounted += amount
    locked.status = _settled_status(locked)
    locked.save(update_fields=["amount_discounted", "status"])
    refresh_ledger(locked.patient)
    return adjustment


@transaction.atomic
def discount_patient_balance(*, patient, percent, reason, approved_by):
    """Take the same percentage off everything the patient still owes."""
    charges = Charge.objects.filter(patient=patient, status__in=["unpaid", "partial"]).order_by("created_at")
    adjustments = []
    for charge in charges:
        if charge.amount - charge.amount_paid - charge.amount_discounted <= 0:
            continue
        adjustments.append(apply_percentage_discount(
            charge=charge, percent=percent, reason=reason, approved_by=approved_by,
        ))
    if not adjustments:
        raise ValueError("This patient has nothing outstanding to discount.")
    return adjustments


@transaction.atomic
def waive_charge(*, charge, reason, approved_by):
    if charge.status in ("waived", "cancelled"): raise ValueError(f"Charge is already {charge.status}.")
    # Waive what is still owed, not the face value: money already paid or
    # already discounted has been accounted for once, and crediting it again
    # puts the patient's ledger into credit.
    outstanding = charge.amount - charge.amount_paid - charge.amount_discounted
    if outstanding <= 0: raise ValueError("This charge has nothing left to waive.")
    adjustment = Adjustment.objects.create(patient=charge.patient, charge=charge, kind="waiver", amount=outstanding, reason=reason, approved_by=approved_by)
    charge.status = "waived"; charge.save(update_fields=["status"])
    refresh_ledger(charge.patient); return adjustment

@transaction.atomic
def cancel_charge(*, charge):
    if charge.status in ("waived", "cancelled"): raise ValueError(f"Charge is already {charge.status}.")
    charge.status = "cancelled"; charge.save(update_fields=["status"])
    refresh_ledger(charge.patient); return charge
