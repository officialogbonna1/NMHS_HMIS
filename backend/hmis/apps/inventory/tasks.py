"""
Scheduled via Celery Beat (see hmis/celery.py) — runs nightly.
Flags items below their reorder threshold and batches expiring soon.
Hook these into email/SMS/dashboard notifications as needed.
"""
from celery import shared_task
from datetime import timedelta
from django.utils import timezone

from apps.core.models import HospitalSettings
from apps.inventory.models import Item, StockRecord


@shared_task
def check_low_stock():
    low_stock_items = [item for item in Item.objects.all() if item.is_low_stock]
    return [{"item": i.name, "quantity": i.total_quantity, "threshold": i.reorder_threshold} for i in low_stock_items]


@shared_task
def check_expiring_batches():
    """
    What is about to expire, and **where it is standing** — the store and the
    dispensing shelf are walked by different people, so a warning that does
    not say which shelf is a warning somebody else has to chase.
    """
    today = timezone.now().date()
    # The same window the dashboards use — one setting, not a constant here
    # and a different number there.
    cutoff = today + timedelta(days=HospitalSettings.load().expiry_warning_days)
    expiring = StockRecord.objects.filter(
        batch__expiry_date__lte=cutoff, batch__expiry_date__gte=today, quantity__gt=0,
    ).select_related("batch__item", "location").order_by("batch__expiry_date")
    return [
        {"item": r.batch.item.name, "batch_no": r.batch.batch_no,
         "location": r.location.name, "expiry_date": str(r.batch.expiry_date),
         "quantity": r.quantity}
        for r in expiring
    ]
