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
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="prescriptions")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="prescriptions_written")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    dosage_instructions = models.CharField(max_length=255, blank=True)
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

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.item.name} ×{self.quantity} for {self.patient}"
