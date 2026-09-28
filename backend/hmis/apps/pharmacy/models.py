from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel
from apps.patients.models import Patient
from apps.inventory.models import Item


class Prescription(TimeStampedModel):
    """
    Written by a doctor, filled by the pharmacy. A prescription is a request
    until a pharmacist dispenses it — stock only moves at that point, so an
    unfilled or cancelled prescription never touches the shelf count.
    """
    STATUS_CHOICES = [
        ("pending", "Awaiting dispensing"),
        ("dispensed", "Dispensed"),
        ("cancelled", "Cancelled"),
    ]
    ROUTE_CHOICES = [
        ("oral", "Oral"), ("sublingual", "Sublingual"), ("iv", "Intravenous (IV)"),
        ("im", "Intramuscular (IM)"), ("sc", "Subcutaneous (SC)"), ("topical", "Topical"),
        ("inhaled", "Inhaled"), ("nasal", "Nasal"), ("ophthalmic", "Eye (ophthalmic)"),
        ("otic", "Ear (otic)"), ("rectal", "Rectal"), ("vaginal", "Vaginal"), ("other", "Other"),
    ]
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="prescriptions")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="prescriptions_written")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    # The dose itself ("1 tablet"). Frequency, duration, route and notes sit
    # beside it rather than inside one free-text line, so the pharmacy label
    # and the chart can print each part. All optional: an existing script with
    # only `dosage_instructions` still reads exactly as it did.
    dosage_instructions = models.CharField(max_length=255, blank=True)
    frequency = models.CharField(max_length=60, blank=True)
    duration = models.CharField(max_length=60, blank=True)
    route = models.CharField(max_length=20, choices=ROUTE_CHOICES, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    dispensed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="prescriptions_dispensed",
    )
    dispensed_at = models.DateTimeField(null=True, blank=True)
    # What the dispensed batches actually came to — the amount billed to the
    # patient, kept here so the pharmacy counter can show it without
    # re-deriving it from stock movements.
    dispensed_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)

    # **The bill for this script line**, raised when the doctor prescribes
    # (`services.bill_prescription`) — the laboratory's arrangement
    # (`LabOrderTest.charge`) applied to the pharmacy. This link, and never the
    # patient's total balance, is what the dispensing gate reads: a patient
    # owing ₦30,000 for a consultation may still collect a script they have
    # paid for, and paying for one script unlocks no other.
    charge = models.ForeignKey("billing.Charge", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="prescriptions")
    # What the line was priced at when it was billed: the FEFO lots on the
    # pharmacy shelf at that moment, each at its own sale price — the rule
    # dispensing always priced by, applied when the debt is created. An
    # order-time snapshot (rule 21): re-pricing a batch tomorrow does not
    # rewrite this bill. NULL means the line was never priced — a script
    # written before prescriptions were billed — and is not a zero.
    quoted_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # The prescribing screen's one token per submission, so a retried or
    # double-clicked request returns the script it already wrote instead of
    # writing — and billing — it twice. The POS's `Sale.client_token` pattern.
    client_token = models.UUIDField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One line per drug per submission — what a retry racing its
            # original meets, in the one place that cannot be interleaved.
            models.UniqueConstraint(
                fields=["client_token", "item"],
                condition=models.Q(client_token__isnull=False),
                name="one_line_per_drug_per_submission"),
        ]

    def __str__(self):
        return f"{self.item.name} ×{self.quantity} for {self.patient}"
