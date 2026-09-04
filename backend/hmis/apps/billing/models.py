from decimal import Decimal
from django.conf import settings
from django.db import models
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

class Charge(TimeStampedModel):
    STATUS = [("unpaid", "Unpaid"), ("partial", "Part paid"), ("paid", "Paid"), ("waived", "Waived"), ("cancelled", "Cancelled")]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="charges")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)
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
    status = models.CharField(max_length=15, choices=STATUS, default="unpaid")
    source_type = models.CharField(max_length=50, blank=True); source_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    class Meta: ordering = ["-created_at"]

    @property
    def payable(self):
        """What is actually collectable: the face value less what was given away."""
        return self.amount - self.amount_discounted - self.amount_waived

    @property
    def balance(self):
        return self.amount - self.amount_paid - self.amount_discounted - self.amount_waived

    @property
    def discount_percent(self):
        if not self.amount:
            return 0
        return round(self.amount_discounted / self.amount * 100, 1)

    @property
    def active_deferral(self):
        """The live "pay later" authorisation on this charge, if there is one."""
        if self.balance <= 0:
            return None
        return self.deferrals.filter(released_at__isnull=True).first()

    @property
    def settlement_status(self):
        """
        What the money actually says, rather than a stored flag: a charge is
        only paid when nothing is left on it, and "deferred" is an
        authorisation to proceed while still owing — never a kind of paid.
        """
        if self.status == "cancelled":
            return "cancelled"
        if self.balance <= 0:
            if self.amount_waived > 0 and self.amount_paid <= 0:
                return "waived"
            return "paid"
        if self.active_deferral is not None:
            return "deferred"
        return "partial" if self.amount_paid > 0 else "unpaid"

class Payment(TimeStampedModel):
    METHOD = [("cash", "Cash"), ("card", "Card"), ("transfer", "Transfer"), ("insurance", "Insurance")]
    # Where the money was taken. Set from the collecting user's role, not the
    # client, so the pharmacy counter's takings can be reconciled separately
    # from the front desk's.
    CHANNEL = [("front_desk", "Front desk"), ("pharmacy", "Pharmacy"), ("cashier", "Cashier")]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD, default="cash")
    channel = models.CharField(max_length=20, choices=CHANNEL, default="front_desk")
    reference = models.CharField(max_length=100, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    class Meta: ordering = ["-created_at"]

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
    KIND = [("discount", "Discount"), ("waiver", "Waiver"), ("refund", "Refund")]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="adjustments")
    charge = models.ForeignKey(Charge, null=True, blank=True, on_delete=models.SET_NULL, related_name="adjustments")
    kind = models.CharField(max_length=20, choices=KIND); amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(); approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    class Meta: ordering = ["-created_at"]
