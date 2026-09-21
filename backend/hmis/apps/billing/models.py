from decimal import Decimal
from django.conf import settings
from django.db import models
from apps.core import labels
from apps.core.mixins import TimeStampedModel
from apps.patients.models import Patient
from apps.departments.models import Department

class BillingItem(TimeStampedModel):
    """
    Priced items the counter bills directly: hospital cards sold at
    registration, consultation fees billed when booking with a doctor, and
    the services a doctor refers a patient on to — lab, ultrasound, eye.

    Referring and billing stay separate acts. A doctor raising a lab request
    does not raise the charge; the counter bills it from this catalogue, so
    money is only ever created by the roles that collect it.
    """
    CATEGORY = [
        ("consultation", "Consultation Fee"), ("card", "Card"),
        ("laboratory", "Laboratory"), ("ultrasound", "Ultrasound / Imaging"),
        ("eye", "Eye clinic"), ("procedure", "Procedure"), ("other", "Other"),
    ]
    category = models.CharField(max_length=20, choices=CATEGORY, default="card")
    name = models.CharField(max_length=100, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    class Meta: ordering = ["category", "name"]
    def __str__(self): return self.name

class PatientLedger(TimeStampedModel):
    patient = models.OneToOneField(Patient, on_delete=models.CASCADE, related_name="ledger")
    total_charges = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_payments = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_adjustments = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # Without an ordering the paginated ledger list can repeat or drop rows.
    class Meta: ordering = ["patient__last_name", "patient__first_name"]
    @property
    def outstanding_balance(self): return self.total_charges - self.total_payments - self.total_adjustments

    def __str__(self):
        """One patient's running account, labelled by what it says they owe."""
        return f"{self.patient} · outstanding {labels.money(self.outstanding_balance)}"

class Charge(TimeStampedModel):
    STATUS = [("unpaid", "Unpaid"), ("partial", "Part paid"), ("paid", "Paid"), ("waived", "Waived"), ("cancelled", "Cancelled")]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="charges")
    # PROTECT, not SET_NULL: a charge's department is part of the financial
    # record, and deleting the department must never silently erase which unit
    # earned the money. A department that has taken a charge is retired with
    # `is_active = False` — it keeps its history and stops being offered for
    # new work. Still nullable, because a write-in charge genuinely has no
    # department the system can know (and 19 historical rows have none).
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # How much of this specific charge has been settled. Payments are taken
    # against the patient's balance, then allocated across their open charges
    # oldest-first (billing.services.record_payment), so a charge can say
    # whether it is paid rather than every charge reading "unpaid" forever.
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Money written off this charge — a percentage discount the counter
    # granted. Held here as well as on the Adjustment so the charge's own
    # balance is true; the Adjustment carries the reason and who approved it.
    amount_discounted = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Money written off outright rather than reduced — the hospital has
    # decided nobody will collect it. Held separately from a discount so a
    # bill can show Original / Discount / Waived / Payable, which is what an
    # audit asks for, and so `balance` stops reading as owed the moment it
    # is waived.
    amount_waived = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Goods the patient brought back. Only a pharmacy POS return writes it
    # (`billing.services.credit_returned_goods`, with an `Adjustment(kind=
    # "return")` naming who took the return and why). It is not a discount —
    # nothing was given away — and not a waiver — nothing was forgiven: the
    # obligation for the returned medicine simply ended when the medicine came
    # back. Rule 25's way to add a reduction: its own column, never an edit to
    # `amount`.
    amount_returned = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=15, choices=STATUS, default="unpaid")
    source_type = models.CharField(max_length=50, blank=True); source_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    # --- cancellation ------------------------------------------------------
    # `status = "cancelled"` already existed and is still the state; these three
    # columns are what it was missing — who withdrew the bill, when, and why.
    # A cancellation is a financial decision like a waiver, and a decision with
    # no name against it is indistinguishable from an accident six months later.
    #
    # They live on the Charge rather than in a table of their own because a
    # charge is cancelled at most once: a second row could only ever disagree
    # with the status. Nothing here is ever cleared — a cancelled charge keeps
    # its amount, its department and its whole history (rule 38).
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="charges_cancelled")
    cancellation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        # The financial report filters on the date a charge was raised and
        # groups by where it came from. Without these it is a table scan per
        # card, which is fine at 26 rows and not at 260,000.
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["source_type", "created_at"]),
        ]

    def __str__(self):
        """
        The bill as the counter reads it out: who owes it, what for, how much
        and whether it has been settled. The state is part of the label
        because a cancelled charge keeps its row and its amount (rule 38),
        and a list of identical descriptions would otherwise not say which is
        which.
        """
        return (f"{self.patient} · {self.description} · {labels.money(self.amount)} "
                f"({self.get_status_display()})")

    @property
    def payable(self):
        """What is actually collectable: the face value less what was given away or brought back."""
        return self.amount - self.amount_discounted - self.amount_waived - self.amount_returned

    @property
    def balance(self):
        """
        The charge's own arithmetic: what is left on the bill itself.

        Deliberately *not* zeroed for a cancelled charge — this is the figure
        an audit reads to see what was withdrawn, and `allocate_to_charges`,
        `waive_charge` and `defer_charge` all reach cancelled charges through
        a status filter rather than through this number. What the patient
        actually owes is `outstanding` below.
        """
        return (self.amount - self.amount_paid - self.amount_discounted - self.amount_waived
                - self.amount_returned)

    @property
    def is_cancelled(self):
        return self.status == "cancelled"

    @property
    def outstanding(self):
        """
        What the patient still owes **on this charge, today**.

        A cancelled charge owes nothing: the hospital withdrew the bill, so the
        financial responsibility is gone even though the row and its original
        amount stay exactly where they were. That is the whole distinction
        between cancelling a service and refunding money — a refund hands cash
        back and leaves the debt standing; a cancellation ends the debt.

        This is the charge-level mirror of what `refresh_ledger` already does
        for the patient: it excludes cancelled charges from `total_charges`,
        which is why a cancelled bill contributes zero to the ledger too.
        """
        if self.is_cancelled:
            return Decimal("0.00")
        return self.balance

    @property
    def amount_refunded(self):
        """How much of what was paid against this charge has gone back out."""
        annotated = getattr(self, "refunded_total", None)
        if annotated is not None:
            return Decimal(annotated)
        return self.refund_allocations.aggregate(v=models.Sum("amount"))["v"] or Decimal("0")

    @property
    def refundable_amount(self):
        """
        What could still be handed back on this charge: money that actually
        landed on it and has not already been returned.

        `amount_paid` is already net of previous refunds (`_reverse_allocations`
        decrements it), so this is simply what the charge is still holding.
        """
        return max(self.amount_paid, Decimal("0"))

    @property
    def discount_percent(self):
        if not self.amount:
            return 0
        return round(self.amount_discounted / self.amount * 100, 1)

    @property
    def active_deferral(self):
        """
        The live "pay later" authorisation on this charge, if there is one.

        Reads a `to_attr="open_deferrals"` prefetch where the caller supplied
        one, so a page of charges costs one query for the lot rather than one
        per row — the same prefetch `billing.reporting.transactions()` uses.
        """
        if self.outstanding <= 0:
            return None
        prefetched = getattr(self, "open_deferrals", None)
        if prefetched is not None:
            return prefetched[0] if prefetched else None
        return self.deferrals.filter(released_at__isnull=True).first()

    def settlement_with(self, deferral):
        """
        The settlement rule itself, given the live deferral rather than
        looking it up.

        It is split out so a report listing hundreds of charges can hand in a
        prefetched deferral instead of asking the database once per row — the
        rule stays in one place, and the N+1 stays out of the dashboard.
        """
        if self.status == "cancelled":
            return "cancelled"
        if self.balance <= 0:
            if self.amount_waived > 0 and self.amount_paid <= 0:
                return "waived"
            return "paid"
        if deferral is not None:
            return "deferred"
        return "partial" if self.amount_paid > 0 else "unpaid"

    @property
    def settlement_status(self):
        """
        What the money actually says, rather than a stored flag: a charge is
        only paid when nothing is left on it, and "deferred" is an
        authorisation to proceed while still owing — never a kind of paid.
        """
        return self.settlement_with(self.active_deferral)

class Payment(TimeStampedModel):
    METHOD = [("cash", "Cash"), ("card", "Card"), ("transfer", "Transfer"), ("insurance", "Insurance")]
    # Where the money was taken. Set from the collecting user's role, not the
    # client, so the pharmacy counter's takings can be reconciled separately
    # from the front desk's.
    CHANNEL = [("front_desk", "Front desk"), ("pharmacy", "Pharmacy"), ("cashier", "Cashier")]
    # Nullable for one reason only: a walk-in customer buying medicine at the
    # pharmacy POS is not a hospital patient, and inventing a `Patient` to
    # satisfy this column would put a stranger on the register. Such a
    # payment is written by `billing.services.record_pos_payment` alone and is
    # always linked to its `sales.Sale` (`pos_sale`); every payment taken
    # against a patient still names them, and `record_payment` still requires
    # one.
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD, default="cash")
    channel = models.CharField(max_length=20, choices=CHANNEL, default="front_desk")
    reference = models.CharField(max_length=100, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-created_at"]
        # Takings are always asked for by date, and reconciled per method and
        # per collector.
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["method", "created_at"]),
        ]

    def __str__(self):
        """
        Money received. The patient is optional — a walk-in buying medicine at
        the POS is not one (rule 39) — so the label says so rather than
        leaving the row nameless.
        """
        who = self.patient if self.patient_id else "Walk-in customer"
        return (f"{who} · {labels.money(self.amount)} {self.get_method_display()} "
                f"({self.get_channel_display()})")

    @property
    def amount_refunded(self):
        """
        How much of this payment has been handed back. Reads the annotation a
        list view attaches (`refunded_total`) where there is one, so a page of
        payments costs one query rather than one per row.
        """
        annotated = getattr(self, "refunded_total", None)
        if annotated is not None:
            return Decimal(annotated)
        return self.refunds.aggregate(v=models.Sum("amount"))["v"] or Decimal("0")

    @property
    def refundable_balance(self):
        """What is still refundable — never below zero, whatever else happened."""
        return max(self.amount - self.amount_refunded, Decimal("0"))

    @property
    def is_fully_refunded(self):
        return self.refundable_balance <= 0


class PaymentAllocation(TimeStampedModel):
    """
    Which charge a payment actually settled, and by how much.

    A payment is taken against the patient's *balance*, then spread across
    their open charges oldest-first (`services.allocate_to_charges`). That
    spreading used to leave no trace: it moved `Charge.amount_paid` and
    threw the link away, so "how much did the laboratory collect in June?"
    was unanswerable — the money knew when it arrived, and the charge knew
    which department it belonged to, and nothing joined the two.

    This row is that join, written inside the same transaction as the
    allocation it records. It is an audit row, not a control: it changes no
    figure and no workflow, and `Charge.amount_paid` remains the charge's own
    settled amount. Sum these over a date range and you have revenue by
    department on a cash basis; sum `amount_paid` over a set of charges and
    you have what those charges have collected since. Both are true, and they
    answer different questions.
    """
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    charge = models.ForeignKey(Charge, on_delete=models.CASCADE, related_name="allocations")
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["charge", "payment"])]

    def __str__(self):
        return f"{self.amount} of payment {self.payment_id} to charge {self.charge_id}"


class Refund(TimeStampedModel):
    """
    Money handed back, recorded as its own transaction.

    **A refund never touches the payment it answers.** The `Payment` row stays
    exactly as it was written — the amount, the method, the time, the person
    who took it — because it is the evidence that the money did arrive. Undoing
    it by editing or deleting it would make the hospital's history say
    something that never happened, and no audit could tell the difference
    between "we refunded ₦3,000" and "we never took it".

    So the arithmetic is additive, the way a ledger's always is:

        Payment   +10,000
        Refund     −3,000
        Net         7,000

    `refundable_balance` on the payment is what is left to give back, so a
    payment can be refunded in parts and can never be refunded past its own
    total. `RefundAllocation` records which bills the money came off, mirroring
    `PaymentAllocation` in the other direction, so a refund is attributed to
    the same department that took the money.

    Every refund also writes an `Adjustment(kind="refund")` — the row the
    ledger, the write-off register and the patient's statement already read.
    That link is what keeps refunds visible everywhere they were visible
    before, rather than adding a second place money can hide.
    """
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    # Nullable exactly when the payment's is: a walk-in POS customer's money
    # going back. That refund has no ledger to touch, so it writes no
    # `Adjustment` either — but it is still a `Refund`, which is what Total
    # Facility Revenue and the refunds report subtract.
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    # How the money went back, and any receipt or transfer reference. Defaults
    # to the method it arrived by, which is what a desk does in practice.
    method = models.CharField(max_length=20, choices=Payment.METHOD, default="cash")
    reference = models.CharField(max_length=100, blank=True)
    # Who did it and who allowed it. The workflow does not force two people —
    # a cashier refunding at the window is both — but the columns are separate
    # so a hospital that adds an approval step has somewhere to put it, and so
    # "who authorised this?" is never answered by inference.
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     related_name="refunds_processed")
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                      related_name="refunds_authorized")
    # The ledger-side record of the same event. SET_NULL rather than CASCADE:
    # losing the adjustment must never take the refund with it.
    adjustment = models.OneToOneField("Adjustment", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="refund")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["payment"]),
        ]

    def __str__(self):
        return f"Refund {self.amount} of payment {self.payment_id}"


class RefundAllocation(TimeStampedModel):
    """
    Which charge a refund took money back off, and how much.

    The mirror of `PaymentAllocation`, and it exists for the same reason: the
    money knows when it left, the charge knows which department it belonged
    to, and without this row nothing joins the two. Sum these over a period
    and you have refunds by department — which is what lets a department's
    net revenue be honest rather than only its gross.
    """
    refund = models.ForeignKey(Refund, on_delete=models.CASCADE, related_name="allocations")
    charge = models.ForeignKey(Charge, on_delete=models.CASCADE, related_name="refund_allocations")
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["charge", "refund"])]

    def __str__(self):
        return f"{self.amount} of refund {self.refund_id} off charge {self.charge_id}"


class PaymentDeferral(TimeStampedModel):
    """
    "Pay later": an authorised decision to let a patient have the service
    before the money is collected.

    It is deliberately **not** a charge status. The charge stays unpaid or
    part-paid, keeps its balance, keeps appearing on the debtors list and
    keeps taking payment allocations — because the hospital is still owed
    the money. What this row adds is who said the patient could proceed, when,
    for how much, and why, so "the lab ran it without payment" has a name
    against it.
    """
    charge = models.ForeignKey(Charge, on_delete=models.CASCADE, related_name="deferrals")
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="deferrals")
    amount_deferred = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name="deferrals_approved")
    # Stamped when the charge is finally settled, so a deferral reads as
    # history rather than an open authorisation forever.
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Deferred {self.amount_deferred} on {self.charge_id}"


class Adjustment(TimeStampedModel):
    # `return` is written only by a pharmacy POS return
    # (`billing.services.credit_returned_goods`); `refund` only by the Refund
    # workflow. The generic adjustments endpoint accepts neither.
    KIND = [("discount", "Discount"), ("waiver", "Waiver"), ("refund", "Refund"),
            ("return", "Goods returned")]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="adjustments")
    charge = models.ForeignKey(Charge, null=True, blank=True, on_delete=models.SET_NULL, related_name="adjustments")
    kind = models.CharField(max_length=20, choices=KIND); amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(); approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["kind", "created_at"])]

    def __str__(self):
        """What was forgiven or given back, and off whose account."""
        return f"{self.patient} · {self.get_kind_display()} {labels.money(self.amount)}"
