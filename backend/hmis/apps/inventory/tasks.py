"""
Scheduled via Celery Beat (see hmis/celery.py) — runs nightly.
Flags items below their reorder threshold and batches expiring soon.
Hook these into email/SMS/dashboard notifications as needed.
"""
from celery import shared_task
from datetime import timedelta
from django.utils import timezone

from apps.inventory.models import Item, Batch

EXPIRY_WARNING_DAYS = 30


@shared_task
def check_low_stock():
    low_stock_items = [item for item in Item.objects.all() if item.is_low_stock]
    return [{"item": i.name, "quantity": i.total_quantity, "threshold": i.reorder_threshold} for i in low_stock_items]


@shared_task
def check_expiring_batches():
    cutoff = timezone.now().date() + timedelta(days=EXPIRY_WARNING_DAYS)
    expiring = Batch.objects.filter(
        expiry_date__lte=cutoff, expiry_date__gte=timezone.now().date(), quantity__gt=0
    ).select_related("item")
    return [
        {"item": b.item.name, "batch_no": b.batch_no, "expiry_date": str(b.expiry_date), "quantity": b.quantity}
        for b in expiring
    ]
