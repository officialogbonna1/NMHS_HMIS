"""
Prescribing and dispensing.

The two are deliberately separate steps: a doctor writes the prescription,
and a pharmacist fills it. Stock only ever moves inside
`dispense_prescription()` — it holds the whole deduction in one
`transaction.atomic` block with `select_for_update()` on the stock records,
so two pharmacists filling the last units at the same time can't both
succeed, and every unit that leaves a batch has a matching StockMovement
audit row.

**A patient can only be given what is standing in the Pharmacy.** Stock in
the Main Store is the hospital's, not the counter's: it reaches the
dispensing shelf by transfer (`inventory.services.transfer_stock`) and only
then can it be handed to anybody. Every query below is scoped to
`dispensing_location()` for that reason — an order that "there are 500 in
the building" must never fill a prescription at a counter holding none.

Nothing outside this module should write to a StockRecord for a
prescription; add a function here instead of routing around these.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.inventory.models import (StockRecord, dispensing_location, expired_q,
                                   receiving_location)
from apps.inventory.services import (InsufficientStockError, apply_stock_change,
                                     fefo_lines_for, quantity_on_hand, refuse_archived)
from apps.pharmacy.models import Prescription
from apps.billing.departments import department_for_source
from apps.billing.notifications import announce
from apps.billing.services import add_charge, cancel_charge, refresh_ledger
from apps.billing.status import service_billing


class OutOfStockError(ValidationError):
    """
    The counter cannot fill this line.

    One refusal for two causes that look identical from the shelf: it never
    had the units, or it had them a moment ago and another transaction took
    them first. Both are "insufficient stock" to the person standing there,
    so they get one message — which names the second possibility, because a
    pharmacist who saw `1 available` on screen a second ago is owed an
    explanation rather than a flat contradiction.

    Carries the figures beside the sentence so the counter can act on them
    without parsing prose, the way the billing refusals already answer with a
    `code` (rules 22, 38).
    """

    def __init__(self, message, *, available=0, requested=0):
        super().__init__(message, code="insufficient_stock")
        self.available = available
        self.requested = requested


class AlreadyDispensedError(ValidationError):
    pass


class PaymentRequired(ValidationError):
    """
    This script line's own bill does not yet let it leave the shelf.

    Carries `billing` — the one shape `billing/status.py` renders for every
    department — so the counter shows the figure and the state rather than
    re-deriving them. Refused before a single unit moves.
    """

    def __init__(self, message, *, code="payment_required", billing=None):
        super().__init__(message, code=code)
        self.billing = billing


def quote_prescription(*, item, quantity):
    """
    What `quantity` of `item` costs, priced the way dispensing always priced
    it: the FEFO lots on the pharmacy shelf, earliest expiry first, expired
    ones excluded, each unit at its own lot's `sale_price`.

    **Not a second price list.** `Batch.sale_price` is the hospital's only
    price for a drug, and `fefo_lines_for` is the same walk dispensing takes —
    so the bill raised at prescribing is exactly what the counter would have
    charged had it dispensed at that moment.
    """
    counter = dispensing_location()
    if counter is None:
        raise ValidationError("No dispensing location is configured.")
    lines = fefo_lines_for(item=item, quantity=quantity, location=counter)
    return sum((line["quantity"] * line["batch"].sale_price for line in lines), Decimal("0"))


class _AlreadyBilled(Exception):
    """Internal: lost the compare-and-set on `Prescription.charge`."""


@transaction.atomic
def bill_prescription(*, prescription, actor, notify=True):
    """
    Raise this script line's charge, once. Returns the charge — the one it
    already had, if it had one — or None for a line priced at nothing.

    **Idempotent, and not by trusting a read.** A linked charge returns at
    once; otherwise the charge is written and the link is set with a
    compare-and-set (`charge IS NULL`) inside a savepoint. A second caller that
    got past the first check loses that update, its charge is rolled back with
    its savepoint, and it returns the winner's. That holds on SQLite, where
    `select_for_update` is a no-op (rule 30's reasoning, applied to money).

    Priced once (`quote_prescription`) and snapshotted on `quoted_amount`, so
    a later call — a retry, a legacy line billed on request — never re-prices a
    line that was already quoted. A zero price raises nothing: a zero charge is
    noise on a bill (the laboratory's rule), and the line reads "NO PAYMENT
    REQUIRED".
    """
    prescription = Prescription.objects.select_for_update().select_related(
        "item", "patient", "doctor").get(pk=prescription.pk)
    if prescription.charge_id:
        return prescription.charge
    if prescription.status != "pending":
        raise ValidationError(
            f"This prescription is {prescription.get_status_display().lower()}; "
            f"only a script waiting at the pharmacy is billed.", code="not_pending")

    amount = prescription.quoted_amount
    if amount is None:
        amount = quote_prescription(item=prescription.item, quantity=prescription.quantity)
        Prescription.objects.filter(pk=prescription.pk, quoted_amount__isnull=True).update(
            quoted_amount=amount)
    if amount <= 0:
        return None

    try:
        with transaction.atomic():
            charge = add_charge(
                patient=prescription.patient,
                # What the cash desk reads on the bill: the drug and how many,
                # the way a lab line reads "Laboratory: FBC".
                description=f"Medication: {prescription.item.name} ×{prescription.quantity}",
                amount=amount,
                # Attributed to the prescriber — the order is what created the
                # debt, as a doctor's lab order is (rule 24).
                created_by=prescription.doctor,
                department=department_for_source("prescription"),
                source_type="prescription", source_id=prescription.pk,
                notify=notify,
            )
            if not Prescription.objects.filter(
                    pk=prescription.pk, charge__isnull=True).update(charge=charge):
                raise _AlreadyBilled
    except _AlreadyBilled:
        return Prescription.objects.get(pk=prescription.pk).charge
    return charge


def dispensing_clearance(prescription):
    """
    May this line leave the shelf, as far as the money goes? `(ok, billing)`.

    Read off **this line's own charge** through `billing.status
    .service_billing` — the vocabulary every department already uses — and
    never off the patient's balance. `requires_payment` is the whole rule:

    * PAID, NO PAYMENT REQUIRED (written off in full) → yes;
    * PAY LATER — an open `PaymentDeferral`, rule 26's authorisation to
      proceed owing, which settles nothing → yes;
    * UNPAID, and PARTIALLY PAID with no deferral → no: money is owed and
      nobody with the authority has said the patient may go without it;
    * CANCELLED → no: the bill was withdrawn, so the script is not live.

    A line priced at zero has nothing to pay. A line with no price at all was
    written before prescriptions were billed and must be billed first
    (`not_billed`) rather than slipping through on the absence of a bill.
    """
    if prescription.charge_id is None:
        if prescription.quoted_amount is not None and prescription.quoted_amount <= 0:
            return True, service_billing(None, fallback_amount=0)
        return False, service_billing(None, fallback_amount=0)
    billing = service_billing(prescription.charge)
    ok = billing["status"] != "cancelled" and not billing["requires_payment"]
    return ok, billing


def _refuse_unless_cleared(prescription):
    ok, billing = dispensing_clearance(prescription)
    if ok:
        return
    name = prescription.item.name
    if prescription.charge_id is None:
        raise PaymentRequired(
            f"{name} for {prescription.patient.display_name} has not been billed yet. "
            f"Raise its bill, then take payment before dispensing.",
            code="not_billed", billing=billing)
    if billing["status"] == "cancelled":
        raise PaymentRequired(
            f"The bill for {name} was cancelled, so this script cannot be dispensed.",
            code="charge_cancelled", billing=billing)
    raise PaymentRequired(
        f"{name} is {billing['label'].lower()}: ₦{billing['outstanding']} still owed on this "
        f"prescription. Take payment for it, waive it or authorise Pay later at the cash "
        f"desk first. Other bills on the patient's account do not affect this one.",
        billing=billing)


def available_quantity(item, location=None):
    """
    Units the pharmacy can actually hand over: on the dispensing shelf, not
    expired.

    Deliberately *not* the hospital total. What is in the Main Store cannot
    be given to a patient until somebody transfers it, so counting it here
    would promise a doctor a drug the counter has none of.
    """
    return quantity_on_hand(item=item, location=location or dispensing_location())


def _insufficient(*, item, available, requested, counter):
    """
    The one wording for "the counter cannot fill this".

    It names what is actually on the shelf **now** rather than what the caller
    believed, says the units may have gone to another transaction, and — only
    when it is true — says where the replacements are. Pointing at the store
    unconditionally would send a pharmacist to fetch stock that is not there.
    """
    message = (
        f"Insufficient stock. {item.name} has {available} unit(s) available on the "
        f"pharmacy shelf — cannot dispense {requested}. This item may have been "
        f"dispensed or allocated by another transaction."
    )
    store = receiving_location()
    in_store = quantity_on_hand(item=item, location=store) if store else 0
    if in_store:
        message += (f" There are {in_store} unit(s) in {store.name} — transfer them to "
                    f"the counter first.")
    return OutOfStockError(message, available=available, requested=requested)


def _directions(*, frequency="", duration="", route="", notes=""):
    """
    How the drug is to be taken, beside the dose. Every part optional; a route,
    when given, must be one the pharmacy label knows how to print.
    """
    route = str(route or "").strip()
    if route and route not in dict(Prescription.ROUTE_CHOICES):
        raise ValidationError(f'"{route}" is not a route of administration the pharmacy recognises.')
    return {"frequency": str(frequency or "").strip()[:60],
            "duration": str(duration or "").strip()[:60],
            "route": route, "notes": str(notes or "").strip()}


@transaction.atomic
def create_prescription(*, patient, doctor, item, quantity, dosage_instructions="",
                        frequency="", duration="", route="", notes="", client_token=None,
                        notify=True):
    """
    A doctor's request to the pharmacy. Stock is checked so a doctor is told
    straight away that a drug can't be filled, but nothing is deducted or
    reserved here — the pharmacist re-checks under lock when they dispense.

    **And the bill is raised here** (`bill_prescription`), in the same
    transaction: the doctor's order is the debt, the way a laboratory order is
    (rule 24), so the patient can pay at the cash desk before walking to the
    counter. Stock still moves only at dispensing.
    """
    if quantity < 1:
        raise ValidationError("Quantity must be at least 1.")
    # Archived is "stays on the old scripts, is never newly prescribed".
    # Dispensing a script written before the archive is untouched.
    refuse_archived(item, doing="newly prescribed")
    available = available_quantity(item)
    if available < quantity:
        raise OutOfStockError(
            f"Only {available} unit(s) of {item.name} on the pharmacy shelf — "
            f"cannot prescribe {quantity}."
        )
    prescription = Prescription.objects.create(
        patient=patient, doctor=doctor, item=item, quantity=quantity,
        dosage_instructions=dosage_instructions, status="pending", client_token=client_token,
        **_directions(frequency=frequency, duration=duration, route=route, notes=notes),
    )
    bill_prescription(prescription=prescription, actor=doctor, notify=notify)
    prescription.refresh_from_db()
    return prescription


@transaction.atomic
def create_prescriptions(*, patient, doctor, lines, client_token=None):
    """
    A whole script in one act. `lines` is [{item, quantity,
    dosage_instructions}, …].

    All-or-nothing on purpose: a doctor writing five drugs and having the
    third rejected for stock must not leave two queued at the pharmacy and
    three lost — they would have no way to tell which. The transaction rolls
    the lot back and the message names the drug that failed.
    """
    if not lines:
        raise ValidationError("Add at least one drug to the prescription.")

    # A retry of a submission already written answers with what it wrote:
    # no second script, and so no second bill.
    if client_token:
        already = list(Prescription.objects.filter(client_token=client_token, patient=patient,
                                                   doctor=doctor).order_by("pk"))
        if already:
            return already

    seen = set()
    for line in lines:
        item = line["item"]
        if item.pk in seen:
            raise ValidationError(
                f"{item.name} is on this prescription twice — put the whole amount on one line."
            )
        seen.add(item.pk)

    try:
        with transaction.atomic():
            prescriptions = [
                # `notify=False` per line: a script of five drugs is one
                # billing decision, and the cash desk hears about it once
                # below (rule 35) — the laboratory's `add_tests` pattern.
                create_prescription(
                    patient=patient, doctor=doctor, item=line["item"], quantity=line["quantity"],
                    dosage_instructions=line.get("dosage_instructions", ""),
                    frequency=line.get("frequency", ""), duration=line.get("duration", ""),
                    route=line.get("route", ""), notes=line.get("notes", ""),
                    client_token=client_token, notify=False,
                )
                for line in lines
            ]
    except IntegrityError:
        # The same submission racing itself: the other request wrote it first.
        # This one's lines and charges went back with the savepoint.
        if client_token:
            already = list(Prescription.objects.filter(client_token=client_token,
                                                       patient=patient).order_by("pk"))
            if already:
                return already
        raise
    billed = [p.charge for p in prescriptions if p.charge_id]
    if billed:
        announce(
            event="charge_raised", patient=patient,
            amount=sum((c.amount for c in billed), Decimal("0")),
            actor=doctor,
            detail=f"Pharmacy — {len(billed)} drug(s) prescribed by "
                   f"{doctor.get_full_name() or doctor.username}",
            outstanding=refresh_ledger(patient).outstanding_balance,
        )
    return prescriptions


@transaction.atomic
def dispense_prescription(*, prescription, pharmacist):
    """
    Hand the drugs over: deduct FEFO (earliest-expiring non-expired batch
    first), log a StockMovement per batch touched, and raise the charge the
    pharmacy then collects against.

    **This is where two prescriptions for the last unit are resolved**, and
    deliberately not earlier. Writing a prescription is a clinical order, so
    two doctors may each order the last box; what cannot happen is both being
    handed over. Nothing is reserved at prescribing time (see
    `create_prescription`) — the shelf is read again here, under lock, and the
    deduction itself is a conditional UPDATE inside
    `inventory.services.apply_stock_change`, so the loser matches no row and
    is refused before anything is written.

    Three things hold the line, smallest first:

    - the **prescription row** is locked, so the same script cannot be
      dispensed twice even by two clicks on one counter (`status != pending`
      is then decisive);
    - the **stock records for this product on this shelf** are locked with
      `of=("self",)` — those rows and nothing else. Without `of`, the joins
      this query needs (`batch__item`, `batch__expiry_date`) make PostgreSQL
      lock the `Batch` rows too, which would block a delivery being received
      into the Main Store for a lot the counter happens to be dispensing;
    - the **deduction** is `quantity = quantity - take WHERE quantity >= take`,
      which is what actually decides the race — and keeps deciding it on
      SQLite, where `select_for_update` compiles to nothing.

    All-or-nothing: a line that cannot be filled in full is refused whole.
    Partial dispensing is deliberately not built (see CLAUDE.md), so there is
    no state where half a prescription has left the shelf.
    """
    prescription = Prescription.objects.select_for_update().select_related(
        "charge", "item", "patient").get(pk=prescription.pk)
    if prescription.status != "pending":
        raise AlreadyDispensedError(
            f"This prescription is already {prescription.get_status_display().lower()}."
        )
    # **The money first, before a single unit is read off the shelf** — this
    # line's own bill, never the patient's balance. Refused here, nothing has
    # moved: no stock, no movement, no dispensed state, no charge.
    _refuse_unless_cleared(prescription)

    counter = dispensing_location()
    if counter is None:
        raise ValidationError("No dispensing location is configured.")

    # The pharmacy's own shelf, earliest expiry first. Stock in the Main
    # Store is not on this list: it has to be transferred to the counter
    # before it can be given to anybody.
    records = list(
        StockRecord.objects.select_for_update(of=("self",))
        .select_related("batch")
        .filter(batch__item=prescription.item, location=counter, quantity__gt=0)
        .exclude(expired_q())
        .order_by("batch__expiry_date", "batch_id")
    )

    available = sum(record.quantity for record in records)
    if available < prescription.quantity:
        raise _insufficient(item=prescription.item, available=available,
                            requested=prescription.quantity, counter=counter)

    remaining = prescription.quantity
    dispensed_value = 0
    try:
        for record in records:
            if remaining <= 0:
                break
            take = min(record.quantity, remaining)
            # Through the inventory service, so the deduction and its movement
            # land together and the movement says which shelf it came off.
            apply_stock_change(
                record, -take, reason="prescription", actor=pharmacist,
                reference=f"prescription:{prescription.id}",
            )
            dispensed_value += take * record.batch.sale_price
            remaining -= take
    except InsufficientStockError:
        # The shelf moved between the read above and this deduction — the
        # window `select_for_update` closes on PostgreSQL and cannot on
        # SQLite. Answered as the same refusal the pharmacist would have got a
        # moment earlier, re-reading what is genuinely there now. Everything
        # written so far goes back with the transaction: no movement, no
        # charge, and the prescription stays pending to be tried again.
        raise _insufficient(item=prescription.item,
                            available=available_quantity(prescription.item, counter),
                            requested=prescription.quantity, counter=counter)

    prescription.status = "dispensed"
    prescription.dispensed_by = pharmacist
    prescription.dispensed_at = timezone.now()
    prescription.dispensed_value = dispensed_value
    prescription.save(update_fields=["status", "dispensed_by", "dispensed_at", "dispensed_value"])

    # No charge here any more: the line was billed when it was prescribed
    # (`bill_prescription`), and dispensing hands over what that bill paid
    # for. `dispensed_value` still records what the lots taken were worth;
    # the bill keeps its order-time price (rule 21), so a lot re-priced
    # between the script and the counter never rewrites what the patient
    # was asked to pay.
    return prescription


@transaction.atomic
def cancel_prescription(*, prescription, actor, reason=""):
    """Pull an unfilled prescription. Dispensed stock can't be un-dispensed."""
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if prescription.status != "pending":
        raise ValidationError(
            f"Cannot cancel a prescription that is {prescription.get_status_display().lower()}."
        )
    prescription.status = "cancelled"
    prescription.cancelled_reason = reason
    prescription.save(update_fields=["status", "cancelled_reason"])
    # The bill goes with it — a drug nobody is going to hand over must not sit
    # on the patient's account. Cancelled, never deleted (rule 38), and only
    # while no money is on it: the pharmacy never moves money, so a line
    # already paid for stays billed and is pinned on the Service Cancellations
    # desk (`billing/withdrawn.py`) for the cash desk to cancel and refund —
    # exactly what the laboratory does when a paid test is removed.
    charge = prescription.charge
    if (charge is not None and charge.status not in ("waived", "cancelled")
            and charge.amount_paid <= 0):
        cancel_charge(charge=charge, cancelled_by=actor,
                      reason=f"Prescription cancelled: {prescription.item.name}"
                             + (f" — {reason}" if reason else ""))
    return prescription
