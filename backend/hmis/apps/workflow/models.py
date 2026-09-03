from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel
from apps.departments.models import Department
from apps.patients.models import Patient


class Visit(TimeStampedModel):
    STATUS = [("open", "Open"), ("completed", "Completed"), ("cancelled", "Cancelled")]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="visits")
    visit_type = models.CharField(max_length=20, choices=[("opd", "Outpatient"), ("ipd", "Inpatient"), ("emergency", "Emergency")], default="opd")
    status = models.CharField(max_length=20, choices=STATUS, default="open")
    attending_doctor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_visits")
    reason = models.CharField(max_length=255, blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="visits_opened")
    class Meta: ordering = ["-created_at"]


class PatientRoute(TimeStampedModel):
    STATUS = [("queued", "Queued"), ("in_progress", "In progress"), ("completed", "Completed"), ("cancelled", "Cancelled")]
    # Why the patient was sent, so the receiving unit's queue says what is
    # being asked of them — a nurse's list reads "vitals", not just a name.
    PURPOSE = [("vitals", "Vitals"), ("consultation", "Consultation"), ("procedure", "Procedure"),
               ("laboratory", "Laboratory"), ("ultrasound", "Ultrasound / Imaging"), ("eye", "Eye clinic"),
               # Kept for routes raised before the three above existed.
               ("investigation", "Investigation"), ("other", "Other")]
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="routes")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="routes")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_routes")
    purpose = models.CharField(max_length=20, choices=PURPOSE, default="consultation")
    priority = models.CharField(max_length=10, choices=[("routine", "Routine"), ("urgent", "Urgent"), ("emergency", "Emergency")], default="routine")
    status = models.CharField(max_length=20, choices=STATUS, default="queued")
    notes = models.TextField(blank=True)
    routed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="routes_created")
    # What came back. A referral the doctor cannot read the answer to is a
    # patient sent away and lost: the unit writes its finding when it closes
    # the work, and it lands on the chart under that visit.
    result = models.TextField(blank=True)
    result_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="route_results")
    result_at = models.DateTimeField(null=True, blank=True)
    class Meta: ordering = ["priority", "created_at"]
