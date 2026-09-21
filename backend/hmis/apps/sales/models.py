"""
The pharmacy's walk-in till: registers, sales and returns.

**Not a second pharmacy.** A POS sale takes stock off the same `StockRecord`
rows prescription dispensing takes it off, earliest expiry first, through
`inventory.services.consume_fefo`; its money is an ordinary `billing.Payment`,
so Total Facility Revenue, the Pharmacy department's takings and every other
financial report see it without being told. Nothing in this app holds a
quantity or a balance of its own.

What is genuinely new here is only what the HMIS had no model for:

* `PosRegister` — a till session: who opened it and with what float, and, once
  it is closed, what the drawer should have held, what was counted and the
  variance. Closing reconciles payments that already exist; it never creates
  money.
* `Sale` + `SaleLine` — the receipt. A *held* sale is lines and nothing more:
  no batch is chosen, no stock moves and no payment exists until it is
  completed.
* `SaleItem` — the batch each line actually came off, at the price that batch
  sells for: what a recall, an expiry audit or a return reads.
* `SaleReturn` + `SaleReturnLine` — what came back. The money goes back through
  `billing.services.refund_payment` (a `Refund` — there is no second refund
  ledger) and the medicine goes into quarantine, never back onto the shelf.

Every row here is written by `sales/services.py`; the API and Django admin
only read.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.billing.models import Charge, Payment, Refund
from apps.core.mixins import TimeStampedModel
from apps.inventory.models import Batch, Item, StockLocation
from apps.patients.models import Patient


class _Referenced(TimeStampedModel):
    """A document numbered off its own primary key, like TRF- and CNT-."""
    PREFIX = ""
    reference = models.CharField(max_length=24, unique=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating and not self.reference:
            self.reference = f"{self.PREFIX}-{self.pk:06d}"
            type(self).objects.filter(pk=self.pk).update(reference=self.reference)


class PosRegister(_Referenced):
    """
    One operator's till, from opening to closing.

    An operator has at most one register open at a time (the constraint), and
    every completed sale and every return is taken in one. The closing figures
    are a snapshot of the payments and refunds already recorded against the
    register's sales — kept so a variance found on Tuesday still reads the
    same on Friday — never new money.
    """
    PREFIX = "REG"
    STATUS = [("open", "Open"), ("closed", "Closed")]
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="pos_registers")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name="pos_registers_opened")
    opening_float = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS, default="open")
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="pos_registers_closed")
    closed_at = models.DateTimeField(null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    closing_note = models.TextField(blank=True)
    closing_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["opened_by"], condition=Q(status="open"),
                                    name="one_open_pos_register_per_operator"),
        ]

    def __str__(self):
        return f"{self.reference or 'REG'} ({self.get_status_display()})"


class Sale(_Referenced):
    """
    A pharmacy POS transaction.

    `held` → `completed`, or `held` → `cancelled` (a held cart discarded).
    A completed sale is never edited or deleted: goods coming back are a
    `SaleReturn`.

    `subtotal − discount_amount = total_amount`, and `total_amount` is exactly
    what the linked `Payment` took. For a registered patient `charge` is the
    Pharmacy charge on their statement that payment settled; a walk-in
    customer has neither a patient nor a charge.
    """
    PREFIX = "POS"
    STATUS = [("held", "Held"), ("completed", "Completed"), ("cancelled", "Cancelled")]
    CUSTOMER = [("walk_in", "Walk-in customer"), ("patient", "Registered patient")]
    DISCOUNT = [("percent", "Percentage"), ("amount", "Fixed amount")]

    register = models.ForeignKey(PosRegister, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="sales")
    status = models.CharField(max_length=12, choices=STATUS, default="held")
    customer_type = models.CharField(max_length=10, choices=CUSTOMER, default="walk_in")
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="pos_sales")
    customer_name = models.CharField(max_length=120, blank=True)
    customer_phone = models.CharField(max_length=40, blank=True)
    sold_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT, blank=True)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_reason = models.TextField(blank=True)
    discount_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="pos_discounts_applied")
    # Who authorised a discount above the cashier's configured limit, when one
    # was needed (`sales/discount_policy.py`). NULL means none was required —
    # not that none was checked. The two are deliberately separate columns:
    # "who gave it" and "who allowed it" are the same person only when an
    # accountant or an administrator is working the till, and an audit has to
    # be able to tell those cases apart.
    discount_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="pos_discounts_approved")
    discount_at = models.DateTimeField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    payment_method = models.CharField(max_length=20, choices=Payment.METHOD, blank=True)
    amount_tendered = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    change_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # SET_NULL rather than PROTECT only so a patient purged from Django admin
    # (rule 37) can take their payments with them; nothing else deletes one.
    payment = models.OneToOneField(Payment, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="pos_sale")
    charge = models.OneToOneField(Charge, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="pos_sale")
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="pos_sales_cancelled")
    # The till's idempotency key: a double-tapped "Complete" sends the same
    # token twice, and the unique constraint lets exactly one sale exist.
    client_token = models.UUIDField(null=True, blank=True, unique=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "completed_at"]),
            models.Index(fields=["customer_type", "completed_at"]),
        ]

    def __str__(self):
        return f"{self.reference or 'POS'} — {self.customer_label}"

    @property
    def customer_label(self):
        if self.patient_id:
            return self.patient.display_name
        return self.customer_name or "Walk-in customer"

    @property
    def amount_returned(self):
        annotated = getattr(self, "returned_total", None)
        if annotated is not None:
            return Decimal(annotated)
        return self.returns.aggregate(v=models.Sum("amount"))["v"] or Decimal("0")


class SaleLine(TimeStampedModel):
    """
    One product on the receipt. On a held sale only `item` and `quantity`
    mean anything; completing the sale prices it from the batches it came
    off and spreads the sale's discount across the lines.
    """
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="pos_sale_lines")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    gross = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # The line's whole discount: its own (below) plus its share of any
    # sale-wide discount — so `gross − discount = net` whatever mix was
    # applied, and a return values the goods at what was actually paid.
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # A discount on this line alone, as asked (`type` / `value`) and as priced
    # once the batches were known (`line_discount`). Blank when none was given.
    line_discount_type = models.CharField(max_length=10, choices=Sale.DISCOUNT, blank=True)
    line_discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.item.name} ×{self.quantity}"


class SaleItem(TimeStampedModel):
    """
    The batch a line actually came off, how many and at what price — one row
    per batch FEFO took from. The link a recall, an expiry audit and a return
    follow from a receipt back to a lot.
    """
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    line = models.ForeignKey(SaleLine, null=True, blank=True, on_delete=models.CASCADE,
                             related_name="pieces")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="pos_sale_items")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.batch} ×{self.quantity}"

    @property
    def gross(self):
        return self.unit_price * self.quantity

    @property
    def returned_quantity(self):
        return self.return_lines.aggregate(v=models.Sum("quantity"))["v"] or 0

    @property
    def returnable_quantity(self):
        return max(self.quantity - self.returned_quantity, 0)


class SaleReturn(_Referenced):
    """
    Goods brought back against a completed sale.

    The original sale and its payment are untouched. The money goes back as a
    `billing.Refund` against that payment; the medicine is received into the
    returns quarantine location, where it cannot be sold or dispensed until it
    has been inspected.
    """
    PREFIX = "RET"
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    register = models.ForeignKey(PosRegister, on_delete=models.PROTECT, related_name="returns")
    reason = models.TextField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    refund_method = models.CharField(max_length=20, choices=Payment.METHOD, default="cash")
    refund = models.OneToOneField(Refund, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="pos_return")
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     related_name="pos_returns_processed")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference or 'RET'} of {self.sale_id}"


class SaleReturnLine(TimeStampedModel):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="lines")
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name="return_lines")
    quantity = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        """What came back off which sold line, and how much of it."""
        return f"{self.sale_item} ×{self.quantity}"
