from django.conf import settings
from django.db import models
from apps.core import labels
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

    def __str__(self):
        """
        One attendance: who came, what kind of visit it was, and whether it
        is still open. A patient has many over time, so the date is what
        tells two of them apart in a dropdown.
        """
        return (f"{self.patient} · {self.get_visit_type_display()} "
                f"{labels.on(self.created_at)} ({self.get_status_display()})")


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
    # The structured half of the finding, for the units whose report has a
    # shape — imaging's technique / findings / measurements / impression
    # (`workflow/report_fields.py`). `result` above stays the rendered text
    # every existing reader shows, so the chart, the printed sheet, the
    # permanent `MedicalTest` copy and the doctor's notification needed to
    # learn nothing. A unit that writes prose leaves this null.
    result_data = models.JSONField(
        null=True, blank=True,
        help_text="Structured report sections, where the purpose has them. Prose lives in `result`.")
    class Meta: ordering = ["priority", "created_at"]

    def __str__(self):
        """Who was sent where, for what, and how far it has got."""
        return (f"{self.visit.patient} · {self.get_purpose_display()} → "
                f"{self.department.name} ({self.get_status_display()})")


class RouteService(TimeStampedModel):
    """
    A configured service a referral actually asked for, and the charge it
    raised.

    **Why this exists.** A `PatientRoute` says where the patient was sent and
    why (`purpose`), which is all a nurse's vitals hand-off needs. It does not
    say *what was ordered*: "Ultrasound / Imaging" is a destination, and
    "Obstetric ultrasound — ₦12,000" is the request. The laboratory solved the
    same problem with `LabOrderTest`, which carries the ordered test, the
    order-time price and the charge; the bench needed a specimen, a status and
    a page of parameters beside it, so it has an order model of its own.
    Imaging needs none of that — the report is prose, and the route already
    carries it (`result`) — so this is the same idea at the size the work
    actually is: one row per examination requested on one referral.

    **Order-time price, like everything else in this hospital.** `name` and
    `unit_price` are snapshots of the catalogue row as it stood when the
    doctor ordered (rule 21). Re-price the examination tomorrow and yesterday's
    bill still says what the patient was quoted.

    **It does not keep books.** The charge is raised by
    `billing.services.add_charge` like every other charge — one ledger, one
    debtors list, one place payments are allocated (rule 24's exception for
    the laboratory, applied to the second unit that orders a priced service).
    Nothing here writes an amount, a status or a payment.

    Deliberately not imaging-specific: `item.category` decides the charge's
    source type, so the eye clinic or a procedure can be ordered the same way
    on the day the hospital decides they should be. Only ultrasound uses it
    today, and only because the doctor's referral screen offers it.
    """
    route = models.ForeignKey(PatientRoute, on_delete=models.CASCADE, related_name="services")
    # The configured service. SET_NULL rather than PROTECT: a retired price
    # list row must not make a finished referral unreadable, and `name` and
    # `unit_price` above are what the record actually reads.
    item = models.ForeignKey("billing.BillingItem", null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="route_services")
    name = models.CharField(max_length=160)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # The bill this request raised, where it raised one. A service with no
    # price in the catalogue raises nothing — a zero charge is noise on a bill
    # — and the row still records that the examination was asked for.
    charge = models.ForeignKey("billing.Charge", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="route_services")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="route_services_requested")

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            # One referral, one of each examination. Asking twice is a
            # mis-click, and a mis-click must not bill the patient twice.
            models.UniqueConstraint(fields=["route", "item"], name="unique_route_service_item"),
        ]

    def __str__(self):
        return f"{self.route.visit.patient} · {self.name}"
