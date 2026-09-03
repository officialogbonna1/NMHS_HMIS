"""
Stock movements the pharmacy makes directly: receiving a delivery and
counting the shelf. Both write a StockMovement in the same transaction as
the quantity change, so the audit trail can always be replayed to explain a
batch's current number.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Batch, StockMovement


@transaction.atomic
def receive_batch(*, batch, actor):
    """Log the opening quantity of a newly received batch."""
    if batch.quantity:
        StockMovement.objects.create(
            batch=batch, change=batch.quantity, reason="received",
            performed_by=actor, reference=f"batch:{batch.batch_no}",
        )
    return batch


@transaction.atomic
def record_stock_count(*, batch, counted_quantity, actor, note=""):
    """
    Stock-take: set a batch to what was physically counted and log the
    difference as an adjustment. A count that matches the system is a no-op —
    it writes no movement rather than a zero-change row.
    """
    if counted_quantity < 0:
        raise ValidationError("Counted quantity cannot be negative.")

    locked = Batch.objects.select_for_update().get(pk=batch.pk)
    delta = counted_quantity - locked.quantity
    if delta == 0:
        return locked

    locked.quantity = counted_quantity
    locked.save(update_fields=["quantity"])
    StockMovement.objects.create(
        batch=locked, change=delta, reason="adjustment", performed_by=actor,
        reference=(note or f"stock count: {locked.batch_no}")[:200],
    )
    return locked


@transaction.atomic
def write_off_expired(*, batch, actor, note=""):
    """Take an expired batch off the shelf, leaving the movement behind."""
    locked = Batch.objects.select_for_update().get(pk=batch.pk)
    if not locked.is_expired:
        raise ValidationError("This batch has not expired yet.")
    if locked.quantity == 0:
        raise ValidationError("This batch is already empty.")
    removed = locked.quantity
    locked.quantity = 0
    locked.save(update_fields=["quantity"])
    StockMovement.objects.create(
        batch=locked, change=-removed, reason="expired_writeoff", performed_by=actor,
        reference=(note or f"expired: {locked.batch_no}")[:200],
    )
    return locked
