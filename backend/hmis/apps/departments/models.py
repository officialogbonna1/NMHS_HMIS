from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel


class Department(TimeStampedModel):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    # Whether Reception may send a patient here by booking an appointment.
    #
    # **Deliberately not `is_active`.** Those are two different questions and
    # the hospital needs both answers: `is_active` says the department exists
    # and is running — it keeps its staff, its services, its stock, its
    # charges and every workflow it already has — and this says whether it is
    # somewhere a patient is *booked into*. Pharmacy is active every day of
    # the week and is never an appointment destination; turning this off takes
    # nothing away from the POS, the dispensing queue or its billing.
    #
    # Default False, because a department is not an appointment destination
    # until somebody says it is. `departments/0004` turns it on for the ones
    # that already had bookable services, so nothing that worked yesterday
    # stops working.
    #
    # It governs **new bookings only**. An appointment already made keeps its
    # department, its services, its provider and its place in the queue
    # (`appointments/booking.py` filters what is offered, and never what
    # exists).
    is_appointment_available = models.BooleanField(
        "available for appointments", default=False,
        help_text="When enabled, Reception can select this department when booking an "
                  "appointment. It does not affect anything else the department does.")
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_departments")
    staff = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="department_memberships")
    visible_domains = models.JSONField(default=list, blank=True, help_text="Approved record domains, e.g. demographics, clinical, billing")

    class Meta:
        ordering = ["name"]

    def __str__(self): return self.name


class Service(TimeStampedModel):
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="services")
    name = models.CharField(max_length=150)
    code = models.SlugField()
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["department", "code"], name="unique_department_service_code")]
        ordering = ["department__name", "name"]

    def __str__(self):
        """The service and the unit that performs it — `code` is unique only
        within a department, so the name alone can repeat across the list."""
        return f"{self.name} — {self.department.name}"
