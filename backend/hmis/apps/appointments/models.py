from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel
from apps.patients.models import Patient


class Appointment(TimeStampedModel):
    """
    A queue entry, not a calendar slot. Reception picks the patient and the
    doctor; the doctor decides when to see them. start_time/end_time are
    stamped by the doctor's start/end transitions, so they record what
    actually happened rather than an intended booking.
    """
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("accepted", "Accepted"),
        ("in_progress", "In progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="appointments")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.CharField(max_length=255)
    symptoms = models.CharField(max_length=255, blank=True)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")

    class Meta:
        ordering = ["created_at"]  # first queued, first seen
