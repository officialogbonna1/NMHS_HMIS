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

    def __str__(self):
        """Who it was taken on and when — never "Vitals object (1)"."""
        return f"{self.patient} · vitals {self.visit_time:%d %b %Y %H:%M}"


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
    # The eye doctor's structured findings, on the note they belong to — so
    # they lock with it and an amendment snapshots them. The allowed keys and
    # values are `clinical/eye_exam.py`; null on every other note.
    eye_examination = models.JSONField(
        null=True, blank=True,
        help_text="Structured eye examination (see clinical/eye_exam.py). Empty on general notes.")

    class Meta:
        ordering = ["-visit_time"]

    def __str__(self):
        """
        How the record names itself — by the patient's hospital number and the
        date, never "ConsultationNote object (1)". The admin's page title, its
        breadcrumbs and every audit row read this, and an internal primary key
        means nothing to the person reading them (rule 32).
        """
        return f"{self.patient} · {self.visit_time:%d %b %Y}"


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
    Archived snapshot written every time a saved note is amended, matching the
    manual's 'Medical Note Archiving' behaviour — each correction keeps a
    timestamped copy of what the note said before, rather than overwriting it.

    **This is the note's history, and there is no second one.** The row holds
    every field the note carries, so the chain reads end to end: the newest
    amendment's snapshot is what the note said before the last correction, and
    the note itself holds what it says now. An older amendment's "new" values
    are the snapshot of the amendment that came after it, which is why no
    `new_*` column exists — storing both halves would be the same fact twice,
    free to disagree. `amended_by` and `created_at` answer "who last amended
    this, and when" without the note needing columns of its own.
    """

    # Why the record was changed. A medical record is amended for a reason and
    # the reason is part of the record — the laboratory already works this way
    # (`LabResultAmendment`, rule 22), so this is that rule applied to notes
    # rather than a second convention.
    REASONS = [
        ("correction", "Correction of error"),
        ("additional_information", "Additional clinical information"),
        ("clarification", "Clarification"),
        ("documentation", "Documentation correction"),
        ("other", "Other"),
    ]

    note = models.ForeignKey(ConsultationNote, on_delete=models.CASCADE, related_name="amendments")
    amended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    previous_reason_for_visit = models.CharField(max_length=255, blank=True)
    previous_chief_complaint = models.CharField(max_length=255, blank=True)
    previous_note_text = models.TextField(blank=True)
    previous_diagnosis = models.TextField(blank=True)
    previous_plan = models.TextField(blank=True)
    previous_eye_examination = models.JSONField(null=True, blank=True)

    # Required by the API, `blank=True` on the model on purpose: the rows
    # written before amendments carried a reason must stay readable, the same
    # way `Adjustment.charge` stays nullable while the serializer refuses a
    # new one without it (rule 33).
    reason = models.CharField(max_length=40, choices=REASONS, blank=True)
    detail = models.TextField(blank=True, help_text="What was corrected, in the amender's words.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Amendment to note {self.note_id} ({self.created_at:%Y-%m-%d %H:%M})"

    # The fields an amendment snapshots, as (snapshot column, note column).
    # Named once so the view that writes a snapshot and the serializer that
    # reads a diff out of it cannot disagree about what history covers.
    SNAPSHOT_FIELDS = (
        ("previous_reason_for_visit", "reason_for_visit"),
        ("previous_chief_complaint", "chief_complaint"),
        ("previous_note_text", "note_text"),
        ("previous_diagnosis", "diagnosis"),
        ("previous_plan", "plan"),
        ("previous_eye_examination", "eye_examination"),
    )

    @classmethod
    def snapshot_of(cls, note):
        """What `note` says right now, as the columns this model stores it in."""
        return {column: getattr(note, field) for column, field in cls.SNAPSHOT_FIELDS}
