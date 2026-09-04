from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import PatientLedger, Charge, Payment, Adjustment, PaymentDeferral

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
        due = charge.amount - charge.amount_paid - charge.amount_discounted - charge.amount_waived
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
    """
    Discounted and waived money counts as settled — it is money nobody will
    collect. A charge fully written off reads "waived" rather than "paid",
    because nobody handed anything over.
    """
    covered = charge.amount_paid + charge.amount_discounted + charge.amount_waived
    if covered >= charge.amount:
        return "waived" if charge.amount_waived > 0 and charge.amount_paid <= 0 else "paid"
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
    refresh_ledger(patient)
    _close_settled_deferrals(patient)
    return payment


def _close_settled_deferrals(patient):
    """
    A "pay later" is answered once the charge it authorised is settled. The
    row stays — it is the record that somebody let the patient proceed — but
    it stops reading as an open authorisation.
    """
    for deferral in PaymentDeferral.objects.filter(
            patient=patient, released_at__isnull=True).select_related("charge"):
        if deferral.charge.balance <= 0:
            deferral.released_at = timezone.now()
            deferral.save(update_fields=["released_at", "updated_at"])

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
    outstanding = locked.balance
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
        if charge.balance <= 0:
            continue
        adjustments.append(apply_percentage_discount(
            charge=charge, percent=percent, reason=reason, approved_by=approved_by,
        ))
    if not adjustments:
        raise ValueError("This patient has nothing outstanding to discount.")
    return adjustments


@transaction.atomic
def apply_amount_discount(*, charge, amount, reason, approved_by):
    """
    Take a flat sum off one charge — "₦500 off the FBC" — as opposed to a
    percentage. Same rules either way: capped at what is still owed, a reason
    is required, and the original amount is never touched. The catalogue's
    price stays the hospital's standard price; the adjustment is this
    patient's.
    """
    if charge.status in ("waived", "cancelled"):
        raise ValueError(f"Charge is already {charge.status}.")
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("A discount must be greater than zero.")
    if not reason:
        raise ValueError("A reason is required for a discount.")

    locked = Charge.objects.select_for_update().get(pk=charge.pk)
    if locked.balance <= 0:
        raise ValueError("This charge has nothing left to discount.")
    amount = min(amount, locked.balance)

    adjustment = Adjustment.objects.create(
        patient=locked.patient, charge=locked, kind="discount", amount=amount,
        reason=reason, approved_by=approved_by,
    )
    locked.amount_discounted += amount
    locked.status = _settled_status(locked)
    locked.save(update_fields=["amount_discounted", "status"])
    refresh_ledger(locked.patient)
    return adjustment


@transaction.atomic
def waive_charge(*, charge, reason, approved_by, amount=None):
    """
    Write money off. `amount` waives part of the charge — "₦1,000 off the
    ₦3,500" — and omitting it waives everything still owed.

    Waived money is recorded on the charge itself (`amount_waived`) as well
    as on the Adjustment, so the bill can show Original / Discount / Waived /
    Payable, and so the balance stops reading as owed. The original amount is
    never overwritten: what was waived and what it was waived from both have
    to survive an audit.
    """
    if charge.status in ("waived", "cancelled"):
        raise ValueError(f"Charge is already {charge.status}.")
    locked = Charge.objects.select_for_update().get(pk=charge.pk)
    # What is still owed, not the face value: money already paid or already
    # discounted has been accounted for once, and crediting it again puts the
    # patient's ledger into credit.
    outstanding = locked.balance
    if outstanding <= 0:
        raise ValueError("This charge has nothing left to waive.")

    if amount is None:
        amount = outstanding
    else:
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
        if amount <= 0:
            raise ValueError("A waiver must be greater than zero.")
        amount = min(amount, outstanding)

    adjustment = Adjustment.objects.create(
        patient=locked.patient, charge=locked, kind="waiver", amount=amount,
        reason=reason, approved_by=approved_by,
    )
    locked.amount_waived += amount
    locked.status = _settled_status(locked)
    locked.save(update_fields=["amount_waived", "status"])
    refresh_ledger(locked.patient)
    _close_settled_deferrals(locked.patient)
    return adjustment


@transaction.atomic
def defer_charge(*, charge, reason, approved_by):
    """
    Authorise "pay later": the patient may have the service now and settle
    afterwards.

    This does not move a penny. The charge keeps its balance, stays on the
    debtors list and still takes the next payment — deferring is permission
    to proceed, not a kind of payment. What it records is who gave that
    permission, when, and for how much, so a test run without payment has a
    name against it rather than looking like an oversight.
    """
    if charge.status == "cancelled":
        raise ValueError("This charge was cancelled.")
    locked = Charge.objects.select_for_update().get(pk=charge.pk)
    if locked.balance <= 0:
        raise ValueError("This charge has nothing outstanding to defer.")
    open_already = locked.deferrals.filter(released_at__isnull=True).first()
    if open_already:
        raise ValueError(
            f"{open_already.approved_by.get_full_name() or open_already.approved_by.username} "
            "already approved pay later on this charge.")

    return PaymentDeferral.objects.create(
        charge=locked, patient=locked.patient, amount_deferred=locked.balance,
        reason=reason or "", approved_by=approved_by,
    )

@transaction.atomic
def cancel_charge(*, charge):
    if charge.status in ("waived", "cancelled"): raise ValueError(f"Charge is already {charge.status}.")
    charge.status = "cancelled"; charge.save(update_fields=["status"])
    refresh_ledger(charge.patient); return charge
