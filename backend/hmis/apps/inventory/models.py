from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel


class Item(TimeStampedModel):
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    reorder_threshold = models.PositiveIntegerField(default=10)
    unit = models.CharField(max_length=30, default="unit")

    class Meta:
        # Alphabetical, and stable: without an ordering the paginated drug
        # picker can repeat or drop items between pages.
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def total_quantity(self):
        return self.batches.aggregate(total=models.Sum("quantity"))["total"] or 0

    @property
    def is_low_stock(self):
        return self.total_quantity <= self.reorder_threshold


class Batch(TimeStampedModel):
    """Stock is tracked per batch since expiry differs batch to batch."""
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="batches")
    batch_no = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField(default=0)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2)
    expiry_date = models.DateField()
    supplier = models.CharField(max_length=200, blank=True)
    received_date = models.DateField(auto_now_add=True)

    class Meta:
        ordering = ["expiry_date"]  # FEFO: first-expiry-first-out by default
        indexes = [models.Index(fields=["expiry_date"]), models.Index(fields=["quantity"])]

    @property
    def is_expired(self):
        from django.utils import timezone
        return self.expiry_date < timezone.now().date()

    def __str__(self):
        return f"{self.item.name} — batch {self.batch_no}"


class StockMovement(TimeStampedModel):
    """Audit trail: every deduction/addition to a batch is recorded here."""
    REASON_CHOICES = [
        ("received", "Stock received"),
        ("prescription", "Dispensed via prescription"),
        ("sale", "Sold to patient"),
        ("adjustment", "Manual adjustment"),
        ("expired_writeoff", "Expired write-off"),
    ]
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="movements")
    change = models.IntegerField()  # negative = deduction, positive = addition
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reference = models.CharField(max_length=200, blank=True)  # e.g. prescription id, sale id
