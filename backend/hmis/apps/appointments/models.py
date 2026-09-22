from django.conf import settings
from django.db import models
from apps.core import labels
from apps.core.mixins import TimeStampedModel
from apps.patients.models import Patient


#: The statuses that mean a queue entry is still live.
#:
#: `services.open_appointment_for` has always used exactly this set to refuse a
#: second booking, and `Appointment.Meta.constraints` writes the same invariant
#: where two concurrent requests cannot both get past it. Module level because
#: a nested `Meta` cannot see its own class's attributes — and because one list
#: read by both is the point.
OPEN_STATUSES = ["queued", "accepted", "in_progress"]


class Appointment(TimeStampedModel):
    """
    A queue entry, not a calendar slot. Reception picks the patient and the
    provider; the provider decides when to see them. start_time/end_time are
    stamped by the provider's start/end transitions, so they record what
    actually happened rather than an intended booking.

    **Still a queue.** There is no booked time here and nothing below adds
    one. What the department / service / fee columns add is *where* the
    patient is going and *what for*, so one queue can serve the eye clinic and
    the scanner room as well as a consultation — the same statuses, the same
    transitions, the same notification.
    """
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("accepted", "Accepted"),
        ("in_progress", "In progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="appointments")
    # The provider the patient is queued to. Still called `doctor`, because it
    # is the same column, the same PROTECT and the same person the existing
    # transitions, queue filter and notification read — an eye doctor or a
    # sonographer is a provider this field can already hold. The API exposes
    # `provider` / `provider_name` as read-only aliases beside it, the way
    # `file_number` aliases `patient_number` (rule 32).
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    # ------------------------------------------------------------------ #
    # Where the patient is going, and what for.
    #
    # All five are nullable and every one of them is optional: an appointment
    # queued the way it always has been — patient, doctor, reason — is still a
    # valid appointment, still queued, still notified. Nothing below changes a
    # transition, a status or who may perform one.
    # ------------------------------------------------------------------ #
    # PROTECT, matching `Charge.department`: a department that has taken an
    # appointment is part of the record and is retired rather than deleted.
    department = models.ForeignKey("departments.Department", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="appointments")
    # The configured service booked, from the one catalogue the counter bills
    # and the doctor orders from (`billing.BillingItem`, rule 50). SET_NULL,
    # because retiring a service must never make a past appointment
    # unreadable — the two snapshots below are what the record actually reads.
    service = models.ForeignKey("billing.BillingItem", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="appointments")
    # What the service was called and what it cost **when it was booked**
    # (rule 21). Re-price Eye Consultation to ₦7,000 tomorrow and this
    # appointment still says what the patient was quoted today.
    service_name = models.CharField(max_length=160, blank=True)
    service_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # The bill this booking raised, where it raised one. Raised through
    # `billing.services.add_charge` like every other charge — one ledger, one
    # debtors list, one place payments are allocated. A service with no price
    # raises nothing: a zero charge is noise on a bill.
    #
    # With several services booked this is the **first** charge raised, and
    # every one of them is reachable as `Charge.objects.filter(
    # source_id=appointment.pk, ...)` — see `charges` on the model. The column
    # is kept because it is what a single-service booking has always meant.
    charge = models.ForeignKey("billing.Charge", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="appointments")
    # Every service booked, snapshotted at booking: the catalogue key, the
    # name, the fee and the charge it raised.
    #
    # **A list rather than a related model.** `PatientRoute.result_data` (rule
    # 53) and `LabOrderTest.parameters` (rule 21) are the precedent: a
    # snapshot that no query filters on, no report groups by and nothing joins
    # to belongs on the row it describes. What *is* queried — the money — is
    # the `Charge` rows themselves, which already carry the order-time price,
    # the department, the settlement status and every adjustment against them.
    # So this adds a reading of what was booked and no second record of it.
    #
    # The single-service columns above keep their exact meaning: with one
    # service `service_name` is its name and `service_fee` is its fee, and
    # with several they are the combined label and the total — which for one
    # service is the same thing. Historical rows have an empty list and read
    # exactly as they did.
    services = models.JSONField(
        default=list, blank=True,
        help_text="Order-time snapshot of every service booked: key, name, fee and charge.")
    reason = models.CharField(max_length=255)
    symptoms = models.CharField(max_length=255, blank=True)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")

    class Meta:
        ordering = ["created_at"]  # first queued, first seen
        constraints = [
            # One open appointment per patient per provider — the last line of
            # defence under the guard that was already there.
            #
            # `open_appointment_for` is an unlocked SELECT, and
            # `select_for_update` is a documented no-op on SQLite (rule 30), so
            # two genuinely concurrent bookings could both read "nothing open"
            # and both write. The button is disabled while pending and the
            # service still refuses the sequential case with a readable
            # message; this is what catches the race, in the one place that
            # cannot be interleaved. The loser's `IntegrityError` becomes a 409
            # through `apps/core/exceptions.py` (rule 47) and takes its charge
            # with it, because both are written inside one `transaction.atomic`.
            #
            # Partial, on the open statuses only: a completed or cancelled
            # appointment is history, and the same patient must be able to come
            # back to the same doctor next week.
            models.UniqueConstraint(
                fields=["patient", "doctor"],
                condition=models.Q(status__in=OPEN_STATUSES),
                name="one_open_appointment_per_patient_and_provider",
            ),
        ]

    @property
    def charges(self):
        """
        Every charge this booking raised, newest last.

        Found by the link `add_charge` already writes — `source_id` is the
        appointment's own primary key — so several services need no join
        table to stay traceable. Each charge keeps its own description, price,
        department, settlement status, discounts, waivers and refunds, which
        is exactly the per-service traceability the counter's multi-select
        gets (rule 52).
        """
        from apps.billing.models import Charge

        ids = [row.get("charge") for row in (self.services or []) if row.get("charge")]
        if not ids:
            return list(Charge.objects.filter(pk=self.charge_id)) if self.charge_id else []
        return list(Charge.objects.filter(pk__in=ids).order_by("pk"))

    def __str__(self):
        """
        A queue entry names the patient, the doctor they are waiting for and
        where it has got to — the three things that distinguish one row from
        the next, since there is no booked time to name it by.
        """
        return f"{self.patient} · {labels.person(self.doctor)} ({self.get_status_display()})"
