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
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.inventory.models import StockRecord, dispensing_location, receiving_location
from apps.inventory.services import (InsufficientStockError, apply_stock_change,
                                     quantity_on_hand)
from apps.pharmacy.models import Prescription
from apps.billing.departments import department_for_source
from apps.billing.services import add_charge


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
                        frequency="", duration="", route="", notes=""):
    """
    A doctor's request to the pharmacy. Stock is checked so a doctor is told
    straight away that a drug can't be filled, but nothing is deducted or
    reserved here — the pharmacist re-checks under lock when they dispense.
    """
    if quantity < 1:
        raise ValidationError("Quantity must be at least 1.")
    available = available_quantity(item)
    if available < quantity:
        raise OutOfStockError(
            f"Only {available} unit(s) of {item.name} on the pharmacy shelf — "
            f"cannot prescribe {quantity}."
        )
    return Prescription.objects.create(
        patient=patient, doctor=doctor, item=item, quantity=quantity,
        dosage_instructions=dosage_instructions, status="pending",
        **_directions(frequency=frequency, duration=duration, route=route, notes=notes),
    )


@transaction.atomic
def create_prescriptions(*, patient, doctor, lines):
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

    seen = set()
    for line in lines:
        item = line["item"]
        if item.pk in seen:
            raise ValidationError(
                f"{item.name} is on this prescription twice — put the whole amount on one line."
            )
        seen.add(item.pk)

    return [
        create_prescription(
            patient=patient, doctor=doctor, item=line["item"], quantity=line["quantity"],
            dosage_instructions=line.get("dosage_instructions", ""),
            frequency=line.get("frequency", ""), duration=line.get("duration", ""),
            route=line.get("route", ""), notes=line.get("notes", ""),
        )
        for line in lines
    ]


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
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if prescription.status != "pending":
        raise AlreadyDispensedError(
            f"This prescription is already {prescription.get_status_display().lower()}."
        )

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
        .exclude(batch__expiry_date__lt=_today())
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

    # The charge is created only after stock movement succeeds, keeping stock
    # and the patient ledger in the same transaction.
    add_charge(
        patient=prescription.patient, description=f"Medication: {prescription.item.name}",
        amount=dispensed_value, created_by=pharmacist,
        # Dispensing is pharmacy revenue. Stated here as well as resolved in
        # `add_charge`, so the call site says which unit earned the money.
        department=department_for_source("prescription"),
        source_type="prescription", source_id=prescription.id,
    )
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
    return prescription


@transaction.atomic
def create_prescription_and_dispense(*, patient, doctor, item, quantity, dosage_instructions="",
                                     **directions):
    """
    Write and fill in one step, for the counter sale case where the same
    person does both. Normal ward flow goes through `create_prescription()`
    and then `dispense_prescription()` so the pharmacist is the one who
    releases the stock.
    """
    prescription = create_prescription(
        patient=patient, doctor=doctor, item=item, quantity=quantity,
        dosage_instructions=dosage_instructions, **directions,
    )
    return dispense_prescription(prescription=prescription, pharmacist=doctor)


def _today():
    """
    Today, in the hospital's own timezone.

    `timezone.now().date()` is the **UTC** date. With `TIME_ZONE` set to
    Africa/Lagos (UTC+1), the two disagree between local midnight and 01:00 —
    UTC is still on yesterday — and the expiry filter above
    (`expiry_date__lt=_today()`) then keeps a lot that expired yesterday.
    During that hour the pharmacy would dispense expired stock.

    `localdate()` is the same clock the batch's `expiry_date` was entered
    against, and the same one the rest of the codebase reads.
    """
    return timezone.localdate()
