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
    status = models.CharField(max_length=15, choices=STATUS, default="unpaid")
    source_type = models.CharField(max_length=50, blank=True); source_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    class Meta: ordering = ["-created_at"]

    @property
    def balance(self):
        return self.amount - self.amount_paid - self.amount_discounted

    @property
    def discount_percent(self):
        if not self.amount:
            return 0
        return round(self.amount_discounted / self.amount * 100, 1)

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

class Adjustment(TimeStampedModel):
    KIND = [("discount", "Discount"), ("waiver", "Waiver"), ("refund", "Refund")]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="adjustments")
    charge = models.ForeignKey(Charge, null=True, blank=True, on_delete=models.SET_NULL, related_name="adjustments")
    kind = models.CharField(max_length=20, choices=KIND); amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(); approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    class Meta: ordering = ["-created_at"]
