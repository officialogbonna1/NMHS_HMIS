"""
Stock, tracked by **product + batch + location**.

The hospital holds the same drug in more than one place, and the same drug
in one place may be two different lots with two different expiry dates. Those
are three independent facts, so they are three things in the schema:

* `Item`        — the product. Paracetamol 500mg.
* `Batch`       — one lot of it: batch number, expiry, what it cost, what it
                  sells for. A lot is the same lot wherever it is standing,
                  so expiry and cost are defined here **once**.
* `StockRecord` — how many units of that batch are standing in one location.
                  This is the row the whole module turns on: `Main Store /
                  PCM001 / 400` and `Pharmacy / PCM001 / 100` are two rows,
                  and there is deliberately no column anywhere holding "500".

`Batch` used to carry a `quantity` column, which meant the hospital had one
undifferentiated pile: stock in the store and stock on the dispensing shelf
were the same number, and moving stock between them was a quantity edit
nobody could audit. That column is gone. `Batch.total_quantity` still
answers "how much of this lot does the hospital own", but it is derived by
summing the locations, so it can never disagree with them.

Two operational locations exist today — Main Store and Pharmacy — but they
are **rows, not code**: `StockLocation` is seeded by a migration, and adding
a theatre store or a ward cupboard later is a row plus whoever should be
allowed to work it. Nothing below hard-codes two.

Every quantity change goes through `inventory/services.py` or
`pharmacy/services.py`, and `StockRecord.save()` refuses a change that did
not — the same way `LockedRecordMixin` refuses an unauthorised edit. A
number that moved with no `StockMovement` beside it is a number nobody can
explain a month later.
"""
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models

from apps.core.mixins import TimeStampedModel

# The two locations this deployment runs on. They are looked up by code
# rather than by primary key so a fixture, a test and a service all name the
# same row, and so adding a third location never renumbers these two.
MAIN_STORE = "main-store"
PHARMACY = "pharmacy"


class StockLocation(TimeStampedModel):
    """
    Somewhere stock physically stands.

    Two flags carry the workflow rather than a hard-coded name:
    `is_default_receiving` is where a supplier delivery lands (Main Store),
    and `is_dispensing_point` is where the pharmacy hands drugs to patients
    from (Pharmacy). Code asks for the *role*, never for the name, so the
    day a second dispensary opens the services do not have to change.
    """
    KIND = [
        ("store", "Store"),
        ("dispensary", "Dispensary"),
    ]
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=KIND, default="store")
    description = models.CharField(max_length=255, blank=True)

    # Supplier deliveries land here unless the caller names somewhere else.
    is_default_receiving = models.BooleanField(
        default=False, help_text="Deliveries are received into this location by default.")
    # Only stock standing here can be dispensed to a patient.
    is_dispensing_point = models.BooleanField(
        default=False, help_text="The pharmacy dispenses to patients from this location.")
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    @property
    def total_units(self):
        return self.stock.aggregate(total=models.Sum("quantity"))["total"] or 0


def receiving_location():
    """Where a delivery lands: Main Store, unless the flag has been moved."""
    return (StockLocation.objects.filter(is_default_receiving=True, is_active=True).first()
            or StockLocation.objects.filter(code=MAIN_STORE).first())


def dispensing_location():
    """The only place a patient's drugs may come off: the Pharmacy shelf."""
    return (StockLocation.objects.filter(is_dispensing_point=True, is_active=True).first()
            or StockLocation.objects.filter(code=PHARMACY).first())


class ItemCategory(TimeStampedModel):
    """
    How the catalogue is grouped — Analgesics, Antibiotics, Consumables.

    A row, not a free-text field on the product. Typed by hand on every item
    it drifted into "Analgesic", "analgesics" and "Pain relief" being three
    different groups, and a category could not be renamed, retired or
    reported on. It is retired rather than deleted once products point at it.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "item categories"

    def __str__(self):
        return self.name

    @property
    def item_count(self):
        return self.items.count()


class UnitOfMeasure(TimeStampedModel):
    """
    What a product is counted in: tablets, bottles, vials, packs.

    `abbreviation` is what a prescription and a dispensing label print —
    "20 tab" rather than "20 Tablet" — and falls back to the name when the
    hospital has not set one.
    """
    name = models.CharField(max_length=60, unique=True)
    abbreviation = models.CharField(max_length=16, blank=True)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "unit of measure"
        verbose_name_plural = "units of measure"

    def __str__(self):
        return self.name

    @property
    def label(self):
        return self.abbreviation or self.name

    @property
    def item_count(self):
        return self.items.count()


class Item(TimeStampedModel):
    name = models.CharField(max_length=200)
    # Configuration rows, not free text (see ItemCategory / UnitOfMeasure).
    # PROTECT: a category or unit that products are using cannot be deleted
    # out from under them — it is deactivated instead.
    category = models.ForeignKey(ItemCategory, null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="items")
    reorder_threshold = models.PositiveIntegerField(default=10)
    unit = models.ForeignKey(UnitOfMeasure, null=True, blank=True,
                             on_delete=models.PROTECT, related_name="items")
    is_active = models.BooleanField(
        default=True,
        help_text="An inactive product stays on every record that references it, "
                  "but cannot be received, transferred or newly prescribed.")

    class Meta:
        # Alphabetical, and stable: without an ordering the paginated drug
        # picker can repeat or drop items between pages.
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def category_name(self):
        return self.category.name if self.category_id else ""

    @property
    def unit_label(self):
        """What a label prints: the abbreviation, else the name, else "unit"."""
        return self.unit.label if self.unit_id else "unit"

    @property
    def total_quantity(self):
        """Everything the hospital holds, wherever it is standing."""
        return StockRecord.objects.filter(batch__item=self).aggregate(
            total=models.Sum("quantity"))["total"] or 0

    def quantity_at(self, location):
        """What is standing in one location — the number a dispenser cares about."""
        if location is None:
            return 0
        return StockRecord.objects.filter(batch__item=self, location=location).aggregate(
            total=models.Sum("quantity"))["total"] or 0

    @property
    def is_low_stock(self):
        return self.total_quantity <= self.reorder_threshold


class Batch(TimeStampedModel):
    """
    One lot of one product: the identity, the dates and the money.

    **No quantity.** A lot has no single quantity — it has a quantity in each
    place it is standing, which is `StockRecord`. Splitting a delivery
    between the store and the dispensary must never be an edit to a number
    here; it is a transfer, and a transfer leaves movements behind.
    """
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="batches")
    batch_no = models.CharField(max_length=100)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2)
    expiry_date = models.DateField()
    supplier = models.CharField(max_length=200, blank=True)
    received_date = models.DateField(auto_now_add=True)

    class Meta:
        ordering = ["expiry_date"]  # FEFO: first-expiry-first-out by default
        indexes = [models.Index(fields=["expiry_date"])]

    @property
    def is_expired(self):
        from django.utils import timezone
        return self.expiry_date < timezone.now().date()

    @property
    def total_quantity(self):
        """Across every location. Derived, so it cannot drift from the parts."""
        return self.stock.aggregate(total=models.Sum("quantity"))["total"] or 0

    def quantity_at(self, location):
        if location is None:
            return 0
        record = self.stock.filter(location=location).first()
        return record.quantity if record else 0

    def __str__(self):
        return f"{self.item.name} — batch {self.batch_no}"


class StockRecord(TimeStampedModel):
    """
    Product + batch + location = a quantity. The unit of stock in this system.

    `save()` refuses to move the number unless a service is doing it. That is
    not politeness — it is the guarantee the API rests on: there is no
    serializer field, no admin form and no shell one-liner that can change a
    quantity without writing the `StockMovement` that explains it. Services
    pass `through_service=True` *after* they have written the movement, in
    the same transaction.
    """
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="stock")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="stock")
    quantity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["batch__expiry_date", "location__display_order"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "location"],
                                    name="unique_batch_per_location"),
        ]
        indexes = [
            models.Index(fields=["location", "quantity"]),
        ]

    def __str__(self):
        return f"{self.batch} @ {self.location.name}: {self.quantity}"

    @property
    def is_expired(self):
        return self.batch.is_expired

    def save(self, *args, through_service=False, **kwargs):
        if self.pk and not through_service:
            previous = (type(self).objects.filter(pk=self.pk)
                        .values_list("quantity", flat=True).first())
            if previous is not None and previous != self.quantity:
                raise PermissionDenied(
                    "Stock quantities move through inventory/pharmacy services only, so "
                    "every change leaves a StockMovement behind. Use a receipt, transfer, "
                    "count, write-off or dispense."
                )
        super().save(*args, **kwargs)


class StockTransfer(TimeStampedModel):
    """
    Stock moving between two locations inside the hospital.

    A transfer is a document with a reference number, not a pair of edits:
    `TRF-000123` says who moved what, out of where, into where, and when. It
    is applied atomically — the units leave the source and arrive at the
    destination in one transaction, so the hospital's total never changes
    because of a transfer, and a half-applied one cannot exist.
    """
    reference = models.CharField(max_length=24, unique=True, blank=True, editable=False)
    source = models.ForeignKey(StockLocation, on_delete=models.PROTECT,
                               related_name="transfers_out")
    destination = models.ForeignKey(StockLocation, on_delete=models.PROTECT,
                                    related_name="transfers_in")
    note = models.CharField(max_length=255, blank=True)
    transferred_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                       related_name="stock_transfers")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference or 'TRF'}: {self.source} → {self.destination}"

    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating and not self.reference:
            # Off the PK sequence, like the patient's file number and the lab
            # order number: unique, ordered, and no counter to race on.
            self.reference = f"TRF-{self.pk:06d}"
            super().save(update_fields=["reference"])

    @property
    def total_units(self):
        return self.lines.aggregate(total=models.Sum("quantity"))["total"] or 0


class StockTransferLine(TimeStampedModel):
    """One batch on a transfer. A line names a batch, never just a product —
    what left the store has to be the same lot that arrived at the pharmacy,
    or the expiry dates on the two shelves stop meaning anything."""
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="transfer_lines")
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.batch} ×{self.quantity}"


class StockCount(TimeStampedModel):
    """
    A physical inventory of one location.

    Counts are per location on purpose: somebody walks the Main Store shelves
    with this sheet, and somebody else walks the Pharmacy's. Each line holds
    what the system believed, what was actually there and the difference, and
    posting the count writes one adjustment movement per line that differs —
    against that batch, at that location.
    """
    reference = models.CharField(max_length=24, unique=True, blank=True, editable=False)
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="counts")
    note = models.CharField(max_length=255, blank=True)
    counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="stock_counts")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference or 'CNT'}: {self.location}"

    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating and not self.reference:
            self.reference = f"CNT-{self.pk:06d}"
            super().save(update_fields=["reference"])

    @property
    def discrepancy_count(self):
        return self.lines.exclude(counted_quantity=models.F("system_quantity")).count()


class StockCountLine(TimeStampedModel):
    """
    One line of a count sheet: Product | Batch | Location | System | Counted |
    Difference. `system_quantity` is frozen at the moment the count was
    posted — the point of a count sheet is what the system *believed*, and
    that has to keep reading true after the adjustment corrected it.
    """
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="count_lines")
    system_quantity = models.PositiveIntegerField()
    counted_quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]

    @property
    def difference(self):
        return self.counted_quantity - self.system_quantity

    def __str__(self):
        return f"{self.batch}: {self.system_quantity} → {self.counted_quantity}"


class StockMovement(TimeStampedModel):
    """
    Audit trail: every addition to or deduction from a batch **at a
    location** is recorded here.

    `location` is what makes the log replayable. Without it, a transfer reads
    as "-100, +100" against the same batch and says nothing about where the
    stock went; with it, the pair is "-100 Main Store, +100 Pharmacy" and the
    shelf can be reconstructed from the log alone.
    """
    REASON_CHOICES = [
        ("received", "Stock received"),
        ("transfer_out", "Transferred out"),
        ("transfer_in", "Transferred in"),
        ("prescription", "Dispensed via prescription"),
        ("sale", "Sold to patient"),
        ("adjustment", "Manual adjustment"),
        ("expired_writeoff", "Expired write-off"),
    ]
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="movements")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="movements")
    change = models.IntegerField()  # negative = deduction, positive = addition
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reference = models.CharField(max_length=200, blank=True)  # e.g. prescription id, sale id
    # The document this movement belongs to, where there is one. Both sides
    # of a transfer point at the same row, so "show me TRF-000123" is a query
    # rather than a string match on `reference`.
    transfer = models.ForeignKey(StockTransfer, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="movements")
    stock_count = models.ForeignKey(StockCount, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="movements")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["location", "-created_at"])]
