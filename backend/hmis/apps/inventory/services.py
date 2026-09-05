"""
Every way stock legitimately moves, other than dispensing.

Receiving a delivery, transferring between locations, counting a shelf and
writing off what expired. Dispensing lives in `pharmacy/services.py` because
it also raises the patient's charge; everything else is here.

Three rules hold across all of them.

**A quantity change and its `StockMovement` are written in one transaction.**
The movement is not a log line emitted afterwards for information — it is
half of the operation, and `StockRecord.save()` will not accept the other
half without it. That is what makes the shelf reconstructible: replay the
movements for a batch at a location and you get the number that is there.

**Stock is held under `select_for_update` while it moves.** Two pharmacists
transferring the last 100 units at the same moment must not both succeed.

**Nothing here trusts a caller to say what is available.** Every function
re-reads the quantity under lock and refuses to take more than is there,
whatever the caller believed a moment ago.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Batch, StockCount, StockCountLine, StockLocation, StockMovement, StockRecord,
    StockTransfer, StockTransferLine, dispensing_location, receiving_location,
)


class InsufficientStockError(ValidationError):
    """Asked to move more units out of a location than are standing there."""


def _today():
    return timezone.now().date()


def location_by_code(code):
    return StockLocation.objects.filter(code=code, is_active=True).first()


def _record(batch, location, *, lock=False):
    """
    The (batch, location) row, created at zero if this is the first time
    stock has stood here.

    `get_or_create` rather than `get`: a location holding none of a lot has
    no row, so the first receipt or transfer into it is what brings the row
    into existence. Locking is done by re-selecting, because a row created in
    this transaction cannot be locked by a query that ran before it existed.
    """
    record, _ = StockRecord.objects.get_or_create(batch=batch, location=location)
    if lock:
        record = StockRecord.objects.select_for_update().get(pk=record.pk)
    return record


def apply_stock_change(record, delta, *, reason, actor, reference="", transfer=None,
                       stock_count=None):
    """
    Move one stock record by `delta` and write the movement that explains it.

    **The only way a quantity changes anywhere in the system.** The movement
    is written first and the quantity saved with `through_service=True`
    immediately after, inside the caller's atomic block — so either both land
    or neither does, and `StockRecord.save()` rejects any other route.

    Public because `pharmacy/services.py` dispenses through it: that module
    owns prescriptions and the patient's charge, but it does not get its own
    way of moving stock (design rule 6). Everything else that needs to move
    stock should get a function in this module rather than call this directly.
    """
    new_quantity = record.quantity + delta
    if new_quantity < 0:
        raise InsufficientStockError(
            f"Only {record.quantity} unit(s) of {record.batch} at {record.location.name}."
        )
    StockMovement.objects.create(
        batch=record.batch, location=record.location, change=delta, reason=reason,
        performed_by=actor, reference=reference[:200], transfer=transfer,
        stock_count=stock_count,
    )
    record.quantity = new_quantity
    record.save(update_fields=["quantity", "updated_at"], through_service=True)
    return record


# ------------------------------------------------------------------ receipts


@transaction.atomic
def receive_stock(*, batch, quantity, actor, location=None, reference=""):
    """
    A supplier delivery arriving.

    It lands in Main Store unless the caller names somewhere else — the
    hospital's rule is that goods come in through the store and reach the
    pharmacy by transfer, so the default is the flow and naming a location is
    the exception (an emergency delivery taken straight to the counter).
    """
    if quantity < 1:
        raise ValidationError("A delivery has to be at least one unit.")
    destination = location or receiving_location()
    if destination is None:
        raise ValidationError("No receiving location is configured.")
    record = _record(batch, destination, lock=True)
    return apply_stock_change(record, quantity, reason="received", actor=actor,
                              reference=reference or f"batch:{batch.batch_no}")


@transaction.atomic
def receive_batch(*, batch, quantity=None, actor, location=None):
    """
    Log the opening quantity of a newly received batch.

    Kept as the name the batch-creation path calls, so receiving a delivery
    reads the same as it always did — what changed underneath is that the
    units land in a *location* rather than in a column on the batch.
    """
    if not quantity:
        return batch
    receive_stock(batch=batch, quantity=quantity, actor=actor, location=location)
    return batch


# ----------------------------------------------------------------- transfers


@transaction.atomic
def transfer_stock(*, source, destination, lines, actor, note=""):
    """
    Move stock between two locations: Main Store → Pharmacy, normally.

    `lines` is `[{"batch": Batch, "quantity": int}, …]` — a line names a
    *batch*, never just a product, because what leaves the store has to be
    the same lot that arrives at the pharmacy or the expiry dates on the two
    shelves stop meaning anything. Use `fefo_lines_for()` to turn "100 units
    of paracetamol" into batch lines before calling this.

    Every line is applied or none is. A transfer that moved two of its three
    drugs is worse than one that failed: the paperwork says one thing and the
    shelves another, and nobody knows which line was the short one.
    """
    if source is None or destination is None:
        raise ValidationError("A transfer needs a source and a destination.")
    if source.pk == destination.pk:
        raise ValidationError("Source and destination are the same location.")
    if not destination.is_active:
        raise ValidationError(f"{destination.name} is not in use.")

    prepared = []
    for line in lines:
        batch = line["batch"]
        try:
            quantity = int(line["quantity"])
        except (TypeError, ValueError, KeyError):
            raise ValidationError(f"How many units of {batch} are being transferred?")
        if quantity < 1:
            raise ValidationError(f"The quantity for {batch} must be at least 1.")
        prepared.append((batch, quantity))

    if not prepared:
        raise ValidationError("Add at least one batch to this transfer.")

    seen = set()
    for batch, _ in prepared:
        if batch.pk in seen:
            raise ValidationError(
                f"{batch} is on this transfer twice — put the whole amount on one line."
            )
        seen.add(batch.pk)

    transfer = StockTransfer.objects.create(
        source=source, destination=destination, note=note[:255], transferred_by=actor,
    )

    for batch, quantity in prepared:
        out = _record(batch, source, lock=True)
        if out.quantity < quantity:
            # Named, so the person at the shelf knows which line to reduce.
            raise InsufficientStockError(
                f"{batch.item.name} batch {batch.batch_no}: only {out.quantity} unit(s) "
                f"in {source.name}, cannot transfer {quantity}."
            )
        StockTransferLine.objects.create(transfer=transfer, batch=batch, quantity=quantity)
        apply_stock_change(out, -quantity, reason="transfer_out", actor=actor,
                           reference=transfer.reference, transfer=transfer)
        apply_stock_change(_record(batch, destination, lock=True), quantity,
                           reason="transfer_in", actor=actor,
                           reference=transfer.reference, transfer=transfer)

    return transfer


def fefo_lines_for(*, item, quantity, location, include_expired=False):
    """
    Turn "100 units of paracetamol out of the Main Store" into batch lines,
    earliest expiry first.

    The same FEFO rule dispensing uses, so a transfer moves the stock that
    would have been dispensed next rather than leaving the short-dated lot
    behind in the store to expire.
    """
    if quantity < 1:
        raise ValidationError("Quantity must be at least 1.")
    records = _available_records(item=item, location=location, include_expired=include_expired)

    lines, remaining = [], quantity
    for record in records:
        if remaining <= 0:
            break
        take = min(record.quantity, remaining)
        lines.append({"batch": record.batch, "quantity": take})
        remaining -= take

    if remaining > 0:
        available = quantity - remaining
        raise InsufficientStockError(
            f"Only {available} unit(s) of {item.name} in {location.name} — "
            f"cannot transfer {quantity}."
        )
    return lines


def _available_records(*, item, location, include_expired=False):
    """Stock records with units on them, FEFO-ordered. Expired lots are out."""
    records = (StockRecord.objects
               .select_related("batch", "batch__item")
               .filter(batch__item=item, location=location, quantity__gt=0))
    if not include_expired:
        records = records.exclude(batch__expiry_date__lt=_today())
    return records.order_by("batch__expiry_date", "batch_id")


def quantity_on_hand(*, item, location=None, include_expired=False):
    """
    How many usable units of a product are standing somewhere.

    `location=None` means the whole hospital, which is what a reorder
    threshold is about. Dispensing asks for the pharmacy specifically, and
    that difference is the whole point of the module.
    """
    records = StockRecord.objects.filter(batch__item=item, quantity__gt=0)
    if location is not None:
        records = records.filter(location=location)
    if not include_expired:
        records = records.exclude(batch__expiry_date__lt=_today())
    return records.aggregate(total=Sum("quantity"))["total"] or 0


# ------------------------------------------------------------------- counting


@transaction.atomic
def record_stock_count(*, batch, location, counted_quantity, actor, note=""):
    """
    Correct one batch at one location to what was physically counted, and log
    the difference as an adjustment.

    A count that matches the system is a no-op — it writes no movement rather
    than a zero-change row, so the log stays a list of things that actually
    happened.
    """
    if counted_quantity < 0:
        raise ValidationError("Counted quantity cannot be negative.")
    if location is None:
        raise ValidationError("A count belongs to a location — name the one you counted.")

    record = _record(batch, location, lock=True)
    delta = counted_quantity - record.quantity
    if delta == 0:
        return record
    return apply_stock_change(record, delta, reason="adjustment", actor=actor,
                              reference=note or f"stock count: {batch.batch_no}")


def count_sheet(*, location, item=None, include_empty=False):
    """
    The sheet somebody walks the shelves with: product, batch, location, and
    what the system believes is there.

    Every batch the location is holding, earliest expiry first. Batches at
    zero are left off unless asked for — a count sheet listing every lot the
    hospital ever held is a sheet nobody finishes.
    """
    records = (StockRecord.objects
               .select_related("batch", "batch__item", "location")
               .filter(location=location))
    if item is not None:
        records = records.filter(batch__item=item)
    if not include_empty:
        records = records.filter(quantity__gt=0)
    return records.order_by("batch__item__name", "batch__expiry_date")


@transaction.atomic
def post_stock_count(*, location, lines, actor, note=""):
    """
    Post a whole physical inventory of one location.

    `lines` is `[{"batch": Batch, "counted_quantity": int}, …]`. The count is
    kept as a document — what the system believed, what was found, and the
    difference, per line — and one adjustment movement is written for each
    line that differs. Lines that match are still recorded on the sheet: "we
    counted it and it was right" is a fact worth keeping, and it is the
    difference between a partial count and a complete one.
    """
    if location is None:
        raise ValidationError("Which location was counted?")
    if not lines:
        raise ValidationError("A count needs at least one line.")

    count = StockCount.objects.create(location=location, note=note[:255], counted_by=actor)

    seen = set()
    for line in lines:
        batch = line["batch"]
        if batch.pk in seen:
            raise ValidationError(
                f"{batch} is counted twice on this sheet — one line per batch."
            )
        seen.add(batch.pk)
        try:
            counted = int(line["counted_quantity"])
        except (TypeError, ValueError, KeyError):
            raise ValidationError(f"Enter the number counted for {batch}.")
        if counted < 0:
            raise ValidationError(f"Counted quantity for {batch} cannot be negative.")

        record = _record(batch, location, lock=True)
        StockCountLine.objects.create(
            count=count, batch=batch,
            system_quantity=record.quantity, counted_quantity=counted,
        )
        delta = counted - record.quantity
        if delta:
            apply_stock_change(record, delta, reason="adjustment", actor=actor,
                               reference=f"{count.reference}: {batch.batch_no}",
                               stock_count=count)

    return count


# ------------------------------------------------------------------ write-off


@transaction.atomic
def write_off_expired(*, batch, actor, location=None, note=""):
    """
    Take an expired lot off the shelf, leaving the movements behind.

    With no location named, it clears the lot everywhere it is standing —
    expired stock is expired in the store and at the counter alike, and
    clearing one shelf and forgetting the other is how expired units get
    dispensed. One movement per location, so the log still says where each
    unit went.
    """
    if not batch.is_expired:
        raise ValidationError("This batch has not expired yet.")

    records = StockRecord.objects.select_for_update().filter(batch=batch, quantity__gt=0)
    if location is not None:
        records = records.filter(location=location)
    records = list(records.select_related("location"))
    if not records:
        raise ValidationError("There is none of this batch left to write off.")

    for record in records:
        apply_stock_change(record, -record.quantity, reason="expired_writeoff", actor=actor,
                           reference=note or f"expired: {batch.batch_no}")
    return batch


__all__ = [
    "InsufficientStockError", "apply_stock_change", "count_sheet", "fefo_lines_for",
    "location_by_code",
    "post_stock_count", "quantity_on_hand", "receive_batch", "receive_stock",
    "record_stock_count", "transfer_stock", "write_off_expired",
    "dispensing_location", "receiving_location",
]
