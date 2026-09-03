from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel, LockedRecordMixin
from apps.patients.models import Patient


class Vitals(LockedRecordMixin, TimeStampedModel):
    """
    Taken by nurses (or doctors). Locks immediately after save — nobody
    but admin can edit it afterward, matching the 'nurse can take vitals
    but can't edit after saving' requirement.
    """
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="vitals")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    visit_time = models.DateTimeField()

    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    heart_rate = models.PositiveIntegerField(null=True, blank=True)
    respiratory_rate = models.PositiveIntegerField(null=True, blank=True)
    sao2 = models.PositiveIntegerField(null=True, blank=True)

    glucose_level = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    glucose_time_of_day = models.CharField(max_length=20, blank=True)
    glucose_fasting = models.BooleanField(null=True, blank=True)

    bp_systolic = models.PositiveIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveIntegerField(null=True, blank=True)
    bp_extremity = models.CharField(max_length=30, blank=True)
    bp_position = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["-visit_time"]


class ConsultationNote(LockedRecordMixin, TimeStampedModel):
    """
    A doctor's per-appointment note. Locks after save. Per the manual's
    pattern: a doctor can view any colleague's notes but only ever edits
    (amends) their own, and even then only before it locks.
    """
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="notes")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notes_written")
    visit_time = models.DateTimeField()

    reason_for_visit = models.CharField(max_length=255)
    chief_complaint = models.CharField(max_length=255, blank=True)
    note_text = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    plan = models.TextField(blank=True)

    class Meta:
        ordering = ["-visit_time"]


class NursingNote(LockedRecordMixin, TimeStampedModel):
    """
    What the nurse observed while working the patient — recorded alongside
    the vitals, at the same station. Locks on save like Vitals do, so the
    observation stands as it was written; the doctor reads it on the
    patient's chart before they see them.
    """
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="nursing_notes")
    nurse = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="nursing_notes")
    vitals = models.ForeignKey(
        Vitals, null=True, blank=True, on_delete=models.SET_NULL, related_name="nursing_notes",
        help_text="The reading this note was taken alongside, when there was one.",
    )
    observation = models.TextField()
    complaint = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Nursing note for {self.patient} ({self.created_at:%Y-%m-%d})"


class ConsultationNoteAmendment(TimeStampedModel):
    """
    Archived snapshot created every time an admin amends a locked note,
    matching the manual's 'Medical Note Archiving' behavior — each edit
    keeps a timestamped copy rather than overwriting history.
    """
    note = models.ForeignKey(ConsultationNote, on_delete=models.CASCADE, related_name="amendments")
    amended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    previous_note_text = models.TextField(blank=True)
    previous_diagnosis = models.TextField(blank=True)
    previous_plan = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
