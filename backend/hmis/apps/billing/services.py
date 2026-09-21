from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .departments import department_for_source, is_authoritative
from .models import (PatientLedger, Charge, Payment, Adjustment, PaymentDeferral,
                     PaymentAllocation, Refund, RefundAllocation)
from apps.core import notifications_email as email_events
from .clearance import announce_cleared
from .notifications import announce

def ledger_totals(patient):
    """
    What the patient's ledger *should* say — `(charges, payments, adjustments)`
    computed from the rows and written nowhere. `refresh_ledger` stores it;
    `billing.integrity` compares it with what is stored, which is how drift is
    found without changing anything.
    """
    total_charges = Charge.objects.filter(patient=patient).exclude(status="cancelled").aggregate(v=Sum("amount"))["v"] or Decimal("0")
    total_payments = Payment.objects.filter(patient=patient).aggregate(v=Sum("amount"))["v"] or Decimal("0")
    # A discount or a waiver forgives part of one bill. Once that bill is
    # cancelled the whole of it has already left `total_charges`, so crediting
    # the forgiven part as well forgives it twice: a ₦4,000 test discounted by
    # ₦1,000 and then cancelled read as ₦1,000 in credit. The adjustment row
    # stays — it is still what the desk approved — it just stops reducing a
    # bill that no longer exists. Refunds are not excluded: that money did go
    # back. An unlinked adjustment has no charge to be cancelled and is kept.
    # Goods brought back to the pharmacy POS end the obligation for them the
    # same way a discount ends part of one — so a return is a credit here too.
    credits = (Adjustment.objects.filter(patient=patient, kind__in=["discount", "waiver", "return"])
               .exclude(charge__status="cancelled")
               .aggregate(v=Sum("amount"))["v"] or Decimal("0"))
    refunds = Adjustment.objects.filter(patient=patient, kind="refund").aggregate(v=Sum("amount"))["v"] or Decimal("0")
    return total_charges, total_payments, credits - refunds


@transaction.atomic
def refresh_ledger(patient):
    ledger, _ = PatientLedger.objects.select_for_update().get_or_create(patient=patient)
    ledger.total_charges, ledger.total_payments, ledger.total_adjustments = ledger_totals(patient)
    ledger.save(); return ledger

def resolve_department(source_type, supplied=None):
    """
    Which department this charge belongs to.

    **The backend wins where the backend knows.** A `source_type` of
    `prescription` is pharmacy work whoever raised it and whatever the request
    body says, so the authoritative map is consulted first and a supplied
    department is ignored — `source_type=prescription` with
    `department=reception` can never create a Reception charge.

    That is the same rule `Payment.channel` follows: stamped from the
    collector's role, never trusted from the client (rule 18). Ignoring rather
    than rejecting is deliberate and matches that precedent — the counter's
    job is to bill the patient in front of it, and a 400 over a field the
    frontend does not even send would fail a real transaction to correct a
    field nobody typed. The response carries the resolved department, so the
    caller is told what happened rather than left guessing.

    Precedence, in order:

    1. the authoritative `source_type` map — a unit the workflow pins;
    2. an explicitly supplied department, for a source the map does not
       cover — a write-in, a blank, or `investigation`, where the diagnostics
       catalogue's own department is the better answer;
    3. `None`, for a source that is genuinely unattributable. A charge is
       still raised: money owed is never refused over a reporting field, and
       the report shows it as "Other / Unclassified", which is the truth.
    """
    if is_authoritative(source_type):
        # A seeded department is expected here; falling back to the supplied
        # one keeps an unseeded database billing rather than losing the
        # attribution the caller did have.
        return department_for_source(source_type) or supplied
    return supplied


@transaction.atomic
def add_charge(*, patient, description, amount, created_by, department=None, source_type="", source_id=None, notify=True):
    """
    The one place a `Charge` is created — every path in the system comes
    through here — and therefore the one place department attribution belongs.
    """
    department = resolve_department(source_type, department)
    charge = Charge.objects.create(patient=patient, description=description, amount=amount, created_by=created_by, department=department, source_type=source_type, source_id=source_id)
    ledger = refresh_ledger(patient)
    if notify:
        # Announced here rather than in the view, so a charge raised by the
        # laboratory or the pharmacy reaches the cash desk exactly the way one
        # typed at the counter does.
        announce(event="charge_raised", patient=patient, amount=charge.amount,
                 actor=created_by, detail=charge.description,
                 outstanding=ledger.outstanding_balance)
        # And the administrator, by email. Queued on commit and unable to
        # raise (`core/email.py`), so Resend being down cannot undo a bill.
        # Behind the same `notify` flag as the announcement above, so a script
        # of six drugs or an order of five tests sends one line, not six
        # (rule 35's "once per decision" applies to the inbox too).
        email_events.patient_billed(charge=charge, patient=patient, actor=created_by,
                                    outstanding=ledger.outstanding_balance)
    return charge

@transaction.atomic
def allocate_to_charges(*, patient, amount, payment=None):
    """
    Spread a payment across the patient's open charges, oldest first, and move
    each one to part-paid or paid. Returns the amount that found no charge to
    settle (zero in the normal case, since record_payment caps a payment at
    what is owed).

    When `payment` is given, each slice is also written down as a
    `PaymentAllocation` — which charge this money settled, and how much of it.
    The allocation itself is unchanged; the row is the record of it, and it is
    what lets a report say the laboratory collected ₦40,000 in June rather
    than only that the hospital did. `payment` stays optional so the rule can
    still be applied on its own (the backfill migration does exactly that).
    """
    remaining = Decimal(amount)
    charges = Charge.objects.select_for_update().filter(
        patient=patient, status__in=["unpaid", "partial"]
    ).order_by("created_at")
    allocations = []
    # Which services this money actually finished off. Collected here because
    # this loop is the only thing that knows — the charge was owing by the
    # filter above, so anything that comes out settled was settled by us.
    cleared = []
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
        if payment is not None:
            allocations.append(PaymentAllocation(payment=payment, charge=charge, amount=take))
        if charge.status in ("paid", "waived"):
            cleared.append(charge)
    if allocations:
        PaymentAllocation.objects.bulk_create(allocations)
    for charge in cleared:
        _tell_the_unit(charge, was_owing=True,
                       actor=payment.received_by if payment is not None else None)
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


def _tell_the_unit(charge, *, was_owing, actor=None):
    """
    A service that was owed for is owed for no longer: tell whoever is holding
    the patient for it.

    The trigger is the charge's own state changing from owing to settled —
    there is no flag, and nothing has to be kept in step. `was_owing` is what
    the caller saw *before* it touched the charge, because that is the only
    thing this function cannot work out for itself.

    Every path that settles a charge comes through here, so a payment at the
    counter, a payment at the pharmacy, a waiver and a discount that finishes
    the bill off all reach the bench the same way (rule 35's "announced once,
    from the service", applied in the other direction).
    """
    if not was_owing or charge.status not in ("paid", "waived"):
        return []
    return announce_cleared(charge, actor=actor)

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
    allocate_to_charges(patient=patient, amount=amount, payment=payment)
    ledger = refresh_ledger(patient)
    _close_settled_deferrals(patient)
    # Full or part is what the balance says afterwards, not what was intended:
    # a payment that clears the account is a full payment however it was
    # described at the window.
    settled = ledger.outstanding_balance <= 0
    announce(event="payment_full" if settled else "payment_part",
             patient=patient, amount=payment.amount, actor=received_by,
             detail=f"{payment.get_method_display()} at the {payment.get_channel_display().lower()}",
             reference=payment.reference,
             outstanding=ledger.outstanding_balance)
    email_events.payment_received(payment=payment, patient=patient, actor=received_by,
                                  outstanding=ledger.outstanding_balance)
    return payment


def _close_settled_deferrals(patient):
    """
    A "pay later" is answered once the charge it authorised is settled. The
    row stays — it is the record that somebody let the patient proceed — but
    it stops reading as an open authorisation.
    """
    for deferral in PaymentDeferral.objects.filter(
            patient=patient, released_at__isnull=True).select_related("charge"):
        # `outstanding`, not `balance`: a cancelled charge keeps its face value
        # but is owed by nobody, so the authorisation to pay it later is spent.
        if deferral.charge.outstanding <= 0:
            deferral.released_at = timezone.now()
            deferral.save(update_fields=["released_at", "updated_at"])

@transaction.atomic
def apply_percentage_discount(*, charge, percent, reason, approved_by, notify=True):
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
    ledger = refresh_ledger(locked.patient)
    # A 100% discount leaves nothing to collect, so the unit is told exactly
    # as it would be for a payment. Anything less changes no status and
    # announces nothing here.
    _tell_the_unit(locked, was_owing=True, actor=approved_by)
    if notify:
        announce(event="discount", patient=locked.patient, amount=amount, actor=approved_by,
                 detail=f"{_percent_label(percent)}% off {locked.description}",
                 reference=reason, outstanding=ledger.outstanding_balance)
    return adjustment


@transaction.atomic
def discount_patient_balance(*, patient, percent, reason, approved_by):
    """Take the same percentage off everything the patient still owes."""
    charges = Charge.objects.filter(patient=patient, status__in=["unpaid", "partial"]).order_by("created_at")
    adjustments = []
    for charge in charges:
        if charge.balance <= 0:
            continue
        # `notify=False`: this is one decision the desk took, not six. The
        # announcement below carries the total, so a patient with six open
        # bills does not fill six inboxes with six notifications.
        adjustments.append(apply_percentage_discount(
            charge=charge, percent=percent, reason=reason, approved_by=approved_by,
            notify=False,
        ))
    if not adjustments:
        raise ValueError("This patient has nothing outstanding to discount.")
    total = sum((a.amount for a in adjustments), Decimal("0"))
    announce(event="discount", patient=patient, amount=total, actor=approved_by,
             detail=f"{len(adjustments)} charge(s) — whole outstanding balance",
             reference=reason,
             outstanding=refresh_ledger(patient).outstanding_balance)
    return adjustments


@transaction.atomic
def apply_amount_discount(*, charge, amount, reason, approved_by, notify=True):
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
    ledger = refresh_ledger(locked.patient)
    _tell_the_unit(locked, was_owing=True, actor=approved_by)
    if notify:
        announce(event="discount", patient=locked.patient, amount=amount, actor=approved_by,
                 detail=locked.description, reference=reason,
                 outstanding=ledger.outstanding_balance)
    return adjustment


@transaction.atomic
def waive_charge(*, charge, reason, approved_by, amount=None, notify=True):
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
    ledger = refresh_ledger(locked.patient)
    _close_settled_deferrals(locked.patient)
    # A fully waived service is not an unpaid one, and the unit holding the
    # patient has to be told so — otherwise the bench sits on a bill nobody
    # is ever going to collect.
    _tell_the_unit(locked, was_owing=True, actor=approved_by)
    if notify:
        announce(event="waiver", patient=locked.patient, amount=amount, actor=approved_by,
                 detail=locked.description, reference=reason,
                 outstanding=ledger.outstanding_balance)
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

    deferral = PaymentDeferral.objects.create(
        charge=locked, patient=locked.patient, amount_deferred=locked.balance,
        reason=reason or "", approved_by=approved_by,
    )
    announce(event="deferral", patient=locked.patient, amount=deferral.amount_deferred,
             actor=approved_by, detail=locked.description, reference=reason or "",
             outstanding=refresh_ledger(locked.patient).outstanding_balance)
    return deferral

class RefundRequired(ValueError):
    """
    Cancelling this charge would leave money in the hospital's hands that the
    patient no longer owes — so it has to go back, and the caller has to say
    so explicitly.

    Its own class rather than a bare ValueError because the view turns it into
    a distinct answer (`code: "refund_required"`, with the amount), which is
    what lets the screen offer **Cancel & refund** instead of just failing.
    """

    code = "refund_required"

    def __init__(self, amount):
        self.amount = amount
        super().__init__(
            f"{amount} has already been paid against this charge. Cancelling it on its "
            f"own would leave the patient in credit — use cancel and refund instead.")


class ChargeNotCancellable(ValueError):
    """The charge is already cancelled, or was written off in full."""
    code = "not_cancellable"

    def __init__(self, charge):
        super().__init__(f"Charge is already {charge.get_status_display().lower()}.")


class FullRefundRequired(ValueError):
    """
    Cancel & refund was asked to hand back something other than everything the
    service is holding.

    Cancelling withdraws the whole bill, so it has to return the whole of what
    was paid for it. A ₦4,000 test cancelled with only ₦1,000 refunded leaves
    the hospital holding ₦3,000 against a bill nobody owes, and the patient
    reads as ₦3,000 in credit. There is no patient-credit account for that
    money to sit in, and inventing one here would be a business decision nobody
    has taken. Part of a payment going back for a service that stays active is
    `refund_payment`, which is where that flexibility belongs.

    So the amount a screen sends is a confirmation, never a choice — which also
    refuses a stale screen: if another cashier refunded part of the payment
    after the page loaded, the figure no longer matches and the desk is shown
    the new one instead of the old one being acted on.
    """
    code = "full_refund_required"

    def __init__(self, refundable, requested):
        self.refundable = refundable
        self.requested = requested
        super().__init__(
            f"This service is holding {refundable} and Cancel & refund returns all of it, "
            f"so {requested} cannot be submitted. If your screen showed a different figure, "
            f"it has changed since the page loaded — check it and try again. To give back "
            f"part of a payment for a service that stays active, use Refunds.")


class UntraceablePayment(ValueError):
    """
    Part of what a charge says it holds cannot be traced to any payment.

    Only history can produce this — `record_payment` writes the allocations in
    the same transaction as the `amount_paid` they explain, and nothing else in
    the application moves `amount_paid` up (`billing.integrity` and its tests
    hold that). It is refused rather than repaired, because the only way to
    "fix" it here would be to pick a payment by patient, date or amount, and a
    refund taken from the wrong payment is worse than one not taken at all.
    """
    code = "untraceable_payment"

    def __init__(self, amount):
        self.amount = amount
        super().__init__(
            f"{amount} paid against this charge cannot be traced to a payment, so it cannot "
            f"be refunded automatically. Nothing was changed — refer it to accounts.")


class LedgerMismatch(ValueError):
    """
    Withdrawing a bill moved the patient's balance by something other than what
    that bill still owed.

    It never should: a cancelled charge leaves `total_charges`, its discounts
    and waivers stop crediting it, and whatever it held goes back — so the
    balance falls by the charge's own remaining balance and not a naira more.
    If the rows disagree (an adjustment that does not match the charge's own
    figures, from before those figures were kept) the operation is refused and
    rolled back, rather than leaving a balance nobody can explain.
    """
    code = "ledger_mismatch"

    def __init__(self, expected, actual):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Cancelling this charge would move the patient's balance by {actual} instead of "
            f"{expected}; its recorded adjustments do not match its figures. Nothing was "
            f"changed — refer it to accounts.")


def _check_withdrawal(*, before, after, owed):
    """The patient's balance must fall by exactly what the withdrawn bill owed."""
    if after - before != -owed:
        raise LedgerMismatch(-owed, after - before)


@transaction.atomic
def cancel_charge(*, charge, cancelled_by=None, reason="", refunding=False, notify=True):
    """
    Withdraw a bill: the patient is no longer financially responsible for it.

    **This is not a refund and must never become one.** Cancelling ends the
    obligation; refunding returns money. A service that was never delivered is
    cancelled; money handed back for a service that *was* delivered is
    refunded; an unused service that was already paid for needs both, which is
    `cancel_and_refund` below.

    What survives, by design: the row, its original `amount`, its department
    and source attribution, its payments and its allocations. Nothing is
    deleted and no figure is rewritten — `status` carries the state and the
    three cancellation columns carry who, when and why. `refresh_ledger` drops
    cancelled charges from `total_charges`, which is how the outstanding
    becomes zero without anything being hidden or forced.

    Refuses to cancel a charge that is still holding money (`amount_paid > 0`)
    unless `refunding=True` says the caller is handing that money back in the
    same transaction. Without that guard, cancelling a paid bill leaves
    `total_charges` short of `total_payments` and the patient reads as being in
    **credit** — the mirror image of the bug this workflow exists to fix, and
    the reason rule 12 says no path may push a patient into credit.

    **Exactly once, even under a race.** The status is written with a
    compare-and-set update rather than `save()`: it only lands while the charge
    is still live and still holding what was read. `select_for_update`
    serialises two cashiers on PostgreSQL but is a no-op on SQLite, and the
    guarantee cannot depend on the database — so the second caller finds
    nothing to update and is refused before any money moves.

    **And the balance moves by exactly what the bill owed.** With no money
    moving, the patient's outstanding must fall by the charge's own remaining
    balance; anything else is refused and rolled back (`LedgerMismatch`).
    `cancel_and_refund` makes the same check once its refund has landed,
    because between the two halves it cannot hold.
    """
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("A reason is required to cancel a service.")

    locked = Charge.objects.select_for_update().select_related("patient").get(pk=charge.pk)
    if locked.status in ("waived", "cancelled"):
        raise ChargeNotCancellable(locked)
    if locked.amount_paid > 0 and not refunding:
        raise RefundRequired(locked.amount_paid)

    owed = locked.balance
    before = refresh_ledger(locked.patient).outstanding_balance
    now = timezone.now()
    updated = (Charge.objects.filter(pk=locked.pk, amount_paid=locked.amount_paid)
               .exclude(status__in=["waived", "cancelled"])
               .update(status="cancelled", cancelled_at=now, cancelled_by=cancelled_by,
                       cancellation_reason=reason, updated_at=now))
    locked.refresh_from_db()
    if updated != 1:
        if locked.status in ("waived", "cancelled"):
            raise ChargeNotCancellable(locked)
        raise ValueError("This charge changed while it was being cancelled — "
                         "reload it and try again.")

    ledger = refresh_ledger(locked.patient)
    if not refunding:
        _check_withdrawal(before=before, after=ledger.outstanding_balance, owed=owed)
    # A "pay later" on a bill nobody owes any more is not an open
    # authorisation: closing it stops the deferral register showing a debt
    # that was withdrawn.
    _close_settled_deferrals(locked.patient)
    if notify:
        # Voiding a bill is a change to what the patient owes, so the desks that
        # chase it are told — otherwise somebody rings a patient about a charge
        # that was withdrawn an hour ago.
        announce(event="charge_cancelled", patient=locked.patient, amount=locked.amount,
                 actor=cancelled_by, detail=locked.description, reference=locked.cancellation_reason,
                 outstanding=ledger.outstanding_balance)
    return locked


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------

def refundable_balance(payment):
    """
    What is still refundable on a payment: what arrived, less what has already
    gone back. Read under the same lock the refund takes, so two cashiers
    cannot each refund the last ₦3,000 of the same payment.
    """
    already = Refund.objects.filter(payment=payment).aggregate(
        v=Sum("amount"))["v"] or Decimal("0")
    return max(Decimal(payment.amount) - already, Decimal("0"))


def _reverse_allocations(*, refund, payment, amount, only_charge=None):
    """
    Take the refunded money back off the bills the payment settled, newest
    settlement first, and write a `RefundAllocation` for each slice.

    Newest first because that is the bill the money most recently landed on:
    refunding a consultation fee taken this morning should reopen this
    morning's consultation, not last week's card.

    Each charge is reopened by exactly what this refund took off it —
    `amount_paid` goes down, the status is recomputed by the same rule every
    other path uses, and the balance becomes owed again. Nothing else on the
    charge moves: the original amount, the discount and the waiver are
    untouched, so `original − discount − waiver − payments = outstanding`
    still closes (rule 25).

    `only_charge` narrows the reversal to a single bill. A refund raised
    against a *payment* spreads newest-first across whatever that payment
    settled; a refund raised against a *charge* — which is what cancelling an
    unused service does — must come off that charge and no other, or cancelling
    an unused scan would quietly reopen the consultation the same payment also
    covered.

    Returns whatever could not be placed against a charge — nil in the normal
    case, since `record_payment` allocates every payment as it is taken. A
    remainder is still refunded and still hits the ledger; it simply has no
    single bill to point at, exactly as an unlinked adjustment does.
    """
    remaining = Decimal(amount)
    # Per charge, because one payment writes at most one allocation per charge.
    already = {
        row["charge_id"]: row["total"]
        for row in RefundAllocation.objects.filter(refund__payment=payment)
        .values("charge_id").annotate(total=Sum("amount"))
    }
    touched = []
    allocations = payment.allocations.order_by("-created_at", "-id")
    if only_charge is not None:
        allocations = allocations.filter(charge=only_charge)
    for allocation in allocations:
        if remaining <= 0:
            break
        left = allocation.amount - already.get(allocation.charge_id, Decimal("0"))
        if left <= 0:
            continue
        take = min(left, remaining)
        charge = Charge.objects.select_for_update().get(pk=allocation.charge_id)
        charge.amount_paid = max(charge.amount_paid - take, Decimal("0"))
        # A withdrawn bill stays withdrawn. `_settled_status` only knows about
        # paid / partial / unpaid / waived, so recomputing it blindly would
        # resurrect a charge the desk had cancelled after part of it was paid.
        if charge.status != "cancelled":
            charge.status = _settled_status(charge)
        charge.save(update_fields=["amount_paid", "status"])
        RefundAllocation.objects.create(refund=refund, charge=charge, amount=take)
        touched.append(charge)
        remaining -= take
    return remaining, touched


@transaction.atomic
def refund_payment(*, payment, amount, reason, processed_by, authorized_by=None,
                   method=None, reference="", charge=None, notify=True, pos_return=False):
    """
    Hand money back, as a transaction of its own.

    The `Payment` row is never touched — not its amount, not its method, not
    the person who took it. What this writes is a `Refund` pointing at it, the
    `RefundAllocation` rows saying which bills the money came off, and the
    `Adjustment(kind="refund")` the ledger and the write-off register already
    read. Afterwards the history says: payment +10,000, refund −3,000, net
    7,000 — and both halves are still there to be read next year.

    Refuses: a blank reason, zero or a negative amount, more than the
    payment's own refundable balance, and anything at all against a payment
    that has already gone back in full. The balance is read under
    `select_for_update`, so two refunds racing on the last of a payment cannot
    both succeed.

    **A refund does not cancel anything.** The charge it comes off is reopened
    and becomes owed again, which is the correct answer when the service was
    delivered and the money is being returned anyway. Ending the obligation is
    a separate decision — `cancel_charge` — and doing both at once is
    `cancel_and_refund`.

    `charge` scopes the reversal to one bill (see `_reverse_allocations`);
    `notify` is switched off by `cancel_and_refund`, which announces the
    combined event once rather than letting this fire a second bell.
    """
    if not str(reason or "").strip():
        raise ValueError("A reason is required for a refund.")
    try:
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter the amount to refund.")
    if amount <= 0:
        raise ValueError("A refund must be greater than zero.")

    locked = Payment.objects.select_for_update().select_related("patient").get(pk=payment.pk)
    # A pharmacy POS sale's payment goes back as a POS return, which receives
    # the medicine into quarantine in the same transaction. Refunding it from
    # the Refunds desk would hand the money back and leave the stock unaccounted
    # for, and would treat a retail sale as a hospital service.
    if not pos_return and Payment.objects.filter(pk=locked.pk, pos_sale__isnull=False).exists():
        raise ValueError(
            "This payment settled a pharmacy POS sale. Return it from Pharmacy → POS sales, "
            "so the returned medicine is quarantined along with the refund.")
    available = refundable_balance(locked)
    if available <= 0:
        raise ValueError("This payment has already been refunded in full.")
    if amount > available:
        raise ValueError(
            f"Only {available} of this payment is still refundable — "
            f"{Decimal(locked.amount) - available} has already been refunded.")

    refund = Refund.objects.create(
        payment=locked, patient=locked.patient, amount=amount, reason=str(reason).strip(),
        method=method or locked.method, reference=reference or "",
        processed_by=processed_by, authorized_by=authorized_by or processed_by,
    )
    if locked.patient is None:
        # A walk-in POS customer: no patient, so no ledger, no allocation to
        # reverse and no statement entry. The `Refund` row is still what every
        # revenue figure subtracts.
        refund.unplaced_amount = Decimal("0.00")
        return refund
    unplaced, touched = _reverse_allocations(refund=refund, payment=locked, amount=amount,
                                             only_charge=charge)

    # The ledger-side record, so a refund keeps appearing everywhere money
    # written off already appears: the patient's statement, the write-off
    # register, the report's adjustments block. It names a charge only when
    # exactly one was reopened — naming one of three would be a claim the
    # allocations already contradict.
    adjustment = Adjustment.objects.create(
        patient=locked.patient,
        charge=touched[0] if len(touched) == 1 else None,
        kind="refund", amount=amount, reason=str(reason).strip(),
        approved_by=authorized_by or processed_by,
    )
    refund.adjustment = adjustment
    refund.save(update_fields=["adjustment", "updated_at"])

    ledger = refresh_ledger(locked.patient)
    if not notify:
        refund.unplaced_amount = unplaced
        return refund
    announce(event="refund", patient=locked.patient, amount=amount, actor=processed_by,
             detail=f"of the {locked.amount:,.2f} taken on "
                    f"{timezone.localtime(locked.created_at):%d %b %Y}",
             reference=str(reason).strip(),
             outstanding=ledger.outstanding_balance)
    refund.unplaced_amount = unplaced
    return refund


# ---------------------------------------------------------------------------
# The pharmacy POS till
# ---------------------------------------------------------------------------

@transaction.atomic
def record_pos_payment(*, amount, method, received_by, reference, patient=None, charge=None):
    """
    Money taken at the pharmacy POS till — one `Payment`, the same row every
    other collection is, so Total Facility Revenue, the method breakdown and
    the Pharmacy department's takings count it without being told.

    Two shapes, and only these two:

    * **a registered patient** — `charge` is the Pharmacy charge the sale has
      just raised, and the payment settles exactly that charge. It is never
      spread oldest-first across the patient's other bills the way
      `record_payment` does: the customer is paying for the medicine in their
      hand, not clearing last month's consultation;
    * **a walk-in customer** — no patient and no charge. Nobody is put on the
      patient register to satisfy the column, and there is no ledger to move.

    `channel` is always `pharmacy`: the till stands at the pharmacy counter,
    whichever role is operating it.
    """
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("A POS payment must be greater than zero.")
    if method not in dict(Payment.METHOD):
        raise ValueError("Unknown payment method.")
    if (patient is None) != (charge is None):
        raise ValueError("A registered patient's POS payment settles their POS charge; "
                         "a walk-in customer's settles none.")
    payment = Payment.objects.create(patient=patient, amount=amount, method=method,
                                     channel="pharmacy", reference=reference or "",
                                     received_by=received_by)
    if charge is None:
        return payment
    locked = Charge.objects.select_for_update().get(pk=charge.pk)
    if locked.patient_id != patient.pk:
        raise ValueError("That charge belongs to another patient.")
    due = (locked.amount - locked.amount_paid - locked.amount_discounted - locked.amount_waived
           - locked.amount_returned)
    if amount != due:
        raise ValueError(f"The POS payment of {amount} does not match the {due} due on its charge.")
    locked.amount_paid += amount
    locked.status = _settled_status(locked)
    locked.save(update_fields=["amount_paid", "status"])
    PaymentAllocation.objects.create(payment=payment, charge=locked, amount=amount)
    refresh_ledger(patient)
    return payment


@transaction.atomic
def credit_returned_goods(*, charge, amount, reason, approved_by):
    """
    Goods a registered patient brought back to the pharmacy POS, taken off
    the charge for them.

    Rule 25's way to reduce a bill: its own column (`amount_returned`) and its
    own `Adjustment` kind, never an edit to `Charge.amount`. It is called by
    the POS return in the same transaction as the `Refund` that gave the money
    back — the refund reopens what it took off the charge, and this ends the
    obligation for the medicine that came back, so the patient neither owes for
    it nor sits in credit.

    Not a discount (nothing was given away), not a waiver (nothing was forgiven)
    and not a service cancellation (the sale happened). Only a POS sale's charge
    takes one.
    """
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("A reason is required for a return.")
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("A return must be greater than zero.")
    locked = Charge.objects.select_for_update().select_related("patient").get(pk=charge.pk)
    if locked.source_type != "pos_sale":
        raise ValueError("Only a pharmacy POS sale's charge takes returned goods.")
    goods = locked.amount - locked.amount_discounted - locked.amount_waived - locked.amount_returned
    if amount > goods:
        raise ValueError(f"Only {goods} of goods on this charge can still be returned.")
    locked.amount_returned += amount
    locked.status = _settled_status(locked)
    locked.save(update_fields=["amount_returned", "status"])
    adjustment = Adjustment.objects.create(patient=locked.patient, charge=locked, kind="return",
                                           amount=amount, reason=reason, approved_by=approved_by)
    refresh_ledger(locked.patient)
    return adjustment


# ---------------------------------------------------------------------------
# Cancelling a service, and cancelling it with the money going back
# ---------------------------------------------------------------------------

def refundable_for_charge(charge):
    """
    What could still be handed back **on this charge**.

    Not the same question as `refundable_balance(payment)`. A payment may have
    settled three bills; this asks how much of it is sitting on *this* one, so
    cancelling an unused scan refunds the scan and leaves the consultation the
    same payment also covered exactly where it is.

    `Charge.amount_paid` is already net of earlier refunds — `_reverse_allocations`
    decrements it — so what the charge is still holding is the answer.
    """
    return max(Decimal(charge.amount_paid), Decimal("0"))


def _payment_slices_for(charge, amount):
    """
    Which payments to take a charge-scoped refund out of, and how much from
    each — newest settlement first, capped at what each payment still has
    sitting on this charge.

    A bill settled by a ₦2,000 deposit and a ₦2,000 balance is two payments,
    and refunding ₦4,000 has to answer both. Each slice is capped by that
    payment's own remaining allocation to this charge, so no payment is ever
    refunded past what it actually contributed here.
    """
    already = {
        row["refund__payment_id"]: row["total"]
        for row in RefundAllocation.objects.filter(charge=charge)
        .values("refund__payment_id").annotate(total=Sum("amount"))
    }
    remaining = Decimal(amount)
    slices = []
    for allocation in (PaymentAllocation.objects.filter(charge=charge)
                       .select_related("payment").order_by("-created_at", "-id")):
        if remaining <= 0:
            break
        left = allocation.amount - already.get(allocation.payment_id, Decimal("0"))
        if left <= 0:
            continue
        take = min(left, remaining)
        slices.append((allocation.payment, take))
        remaining -= take
    return slices, remaining


def _refund_off_charge(*, charge, amount, reason, actor, method, reference):
    """
    Hand `amount` back off this charge, taken from the payments that actually
    settled it, newest first.

    Shared by `refund_charge` and `cancel_and_refund` so the two cannot drift:
    the only difference between them is whether the bill is withdrawn as well.
    Every slice goes through `refund_payment`, which is still the one place a
    `Refund` is written — this composes it, it does not reimplement it.
    """
    # A charge holding money no payment allocation accounts for is refused
    # rather than given an invented payment to refund it from.
    slices = _traceable_slices(charge, amount)
    return [
        refund_payment(payment=payment, amount=slice_amount, reason=reason,
                       processed_by=actor, method=method, reference=reference,
                       charge=charge, notify=False)
        for payment, slice_amount in slices
    ]


def _validate_charge_refund(charge, amount):
    """The amount a charge-scoped refund may take, or the reason it may not."""
    available = refundable_for_charge(charge)
    if amount is None:
        return available
    try:
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter the amount to refund.")
    if amount < 0:
        raise ValueError("A refund cannot be negative.")
    if amount > available:
        raise ValueError(
            f"Only {available} has been paid against this charge; "
            f"{amount} cannot be refunded from it.")
    return amount


@transaction.atomic
def refund_charge(*, charge, reason, actor, amount=None, method=None, reference=""):
    """
    Hand money back **off one service, without cancelling it**.

    For a service that *was* delivered and is being refunded anyway: the bill
    stays active and becomes owed again, exactly as a payment-scoped refund
    does. The only thing this adds over `refund_payment` is that the money is
    taken off *this* charge rather than spread across whatever else the payment
    settled — which is what you want when a patient is querying one line on
    their bill.

    Cancelling as well is `cancel_and_refund`; that is a different decision and
    has to be asked for.
    """
    if not str(reason or "").strip():
        raise ValueError("A reason is required for a refund.")
    locked = Charge.objects.select_for_update().select_related("patient").get(pk=charge.pk)
    if locked.status == "cancelled":
        raise ValueError("This service is already cancelled — its money has been dealt with.")
    amount = _validate_charge_refund(locked, amount)
    if amount <= 0:
        raise ValueError("Nothing has been paid against this charge, so there is "
                         "nothing to refund.")

    refunds = _refund_off_charge(charge=locked, amount=amount, reason=reason, actor=actor,
                                 method=method, reference=reference)
    locked.refresh_from_db()
    ledger = refresh_ledger(locked.patient)
    announce(event="refund", patient=locked.patient, amount=amount, actor=actor,
             detail=locked.description, reference=str(reason).strip(),
             outstanding=ledger.outstanding_balance)
    return {"charge": locked, "refunds": refunds, "amount_refunded": amount,
            "outstanding": ledger.outstanding_balance}


def _traceable_slices(charge, amount):
    """
    The payments `amount` would come back from, or `UntraceablePayment` if the
    allocations cannot account for all of it. Never a guess: a slice is only
    ever a payment that actually settled this charge.
    """
    slices, unplaced = _payment_slices_for(charge, amount)
    if unplaced > 0:
        raise UntraceablePayment(unplaced)
    return slices


@transaction.atomic
def cancel_and_refund(*, charge, reason, actor, expected_amount=None, method=None, reference=""):
    """
    The unused-service workflow: withdraw the bill **and** hand back everything
    that was paid for it, as one indivisible operation.

    This is the case the hospital actually has most often — a laboratory test
    billed and paid for at the counter, then never run. Neither half is right
    on its own. Cancelling alone leaves the hospital holding money for a
    service it did not provide, and the patient reading as ₦4,000 in credit.
    Refunding alone hands the money back and leaves the ₦4,000 bill standing,
    so the patient owes for a test that never happened. Both, together, land
    where they should: charge cancelled, payment still on the record, refund
    recorded beside it, outstanding **zero**.

    **All of it, always.** The refund is everything the charge is holding —
    `refundable_for_charge` — and is not a parameter. Cancelling ends the whole
    obligation, so returning only part of the money would leave the hospital
    holding cash against a bill nobody owes and the patient in credit, with no
    account for that credit to live in. `expected_amount` is the figure the
    screen showed: it is checked and never used, and anything else is refused
    with `FullRefundRequired` — including a figure that was right when the page
    loaded and has since been changed by another cashier. Part of a payment
    going back for a service that stays active is `refund_payment`.

    A charge with nothing paid against it needs no refund and is simply
    cancelled; a part-paid one refunds what was paid, and the unpaid remainder
    stops being owed along with the rest of the bill.

    **Refused before anything is written** when the charge cannot be cancelled,
    when the confirmed amount does not match, or when part of what it holds
    cannot be traced to a payment (`UntraceablePayment`). Then everything runs
    inside one `transaction.atomic` under `select_for_update`, so a failure
    anywhere — a payment refunded out from under us a moment ago — rolls back
    the cancellation too. There is no state in which the charge is cancelled
    but the money never went back, or the money went back but the bill is still
    owed.

    **Checked afterwards, as well.** The charge must end holding nothing, and
    the patient's balance must have fallen by exactly what the bill still owed
    — never below it into credit. Either failing raises and rolls back.

    One notification for one decision: the two services are called with
    `notify=False` and the combined event is announced once at the end.
    """
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("A reason is required to cancel a service.")

    locked = Charge.objects.select_for_update().select_related("patient").get(pk=charge.pk)
    if locked.status in ("waived", "cancelled"):
        raise ChargeNotCancellable(locked)

    refundable = refundable_for_charge(locked)
    if expected_amount not in (None, ""):
        try:
            requested = Decimal(str(expected_amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("The amount to refund is not a number.")
        if requested != refundable:
            raise FullRefundRequired(refundable, requested)
    if refundable > 0:
        _traceable_slices(locked, refundable)

    owed = locked.balance
    before = refresh_ledger(locked.patient).outstanding_balance

    # Cancel first: the business event is that the service is not happening,
    # and the refund is its consequence. `refunding=True` because the money is
    # going back in this same transaction, which is exactly what the guard in
    # `cancel_charge` is asking to be told.
    cancel_charge(charge=locked, cancelled_by=actor, reason=reason,
                  refunding=refundable > 0, notify=False)

    refunds = _refund_off_charge(charge=locked, amount=refundable, reason=reason, actor=actor,
                                 method=method, reference=reference) if refundable > 0 else []

    locked.refresh_from_db()
    if locked.amount_paid != 0:
        raise UntraceablePayment(locked.amount_paid)
    ledger = refresh_ledger(locked.patient)
    _check_withdrawal(before=before, after=ledger.outstanding_balance, owed=owed)

    announce(
        event="cancel_and_refund" if refundable > 0 else "service_cancelled",
        patient=locked.patient, amount=refundable if refundable > 0 else locked.amount,
        actor=actor, detail=locked.description, reference=reason,
        outstanding=ledger.outstanding_balance,
    )
    return {"charge": locked, "refunds": refunds, "amount_refunded": refundable,
            "amount_paid_before": refundable, "owed_before": owed,
            "outstanding_before": before, "outstanding": ledger.outstanding_balance}
