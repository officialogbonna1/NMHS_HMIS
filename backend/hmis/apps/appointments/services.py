from django.db import transaction
from django.utils import timezone
from apps.core.services import audit_event
from .models import Appointment


def open_appointment_for(*, doctor, patient):
    """The patient's existing live queue entry with this doctor, if any."""
    return Appointment.objects.filter(
        doctor=doctor, patient=patient, status__in=["queued", "accepted", "in_progress"]
    ).first()


@transaction.atomic
def accept_appointment(*, appointment, actor):
    if appointment.status != "queued":
        raise ValueError(f"Cannot accept an appointment that is {appointment.status}.")
    appointment.status = "accepted"
    appointment.save(update_fields=["status"])
    audit_event(actor=actor, action="appointment.accepted", instance=appointment)
    return appointment


@transaction.atomic
def cancel_appointment(*, appointment, actor):
    if appointment.status not in ("queued", "accepted"):
        raise ValueError(f"Cannot cancel an appointment that is {appointment.status}.")
    appointment.status = "cancelled"
    appointment.save(update_fields=["status"])
    audit_event(actor=actor, action="appointment.cancelled", instance=appointment)
    return appointment


@transaction.atomic
def start_appointment(*, appointment, actor):
    if appointment.status != "accepted":
        raise ValueError(f"Cannot start an appointment that is {appointment.status}.")
    appointment.status = "in_progress"
    # Times are recorded, never booked — this is when the doctor actually
    # started, which is why reception has no say over it.
    appointment.start_time = timezone.now()
    appointment.save(update_fields=["status", "start_time"])
    audit_event(actor=actor, action="appointment.started", instance=appointment)
    return appointment


@transaction.atomic
def end_appointment(*, appointment, actor):
    if appointment.status != "in_progress":
        raise ValueError(f"Cannot end an appointment that is {appointment.status}.")
    appointment.status = "completed"
    appointment.end_time = timezone.now()
    appointment.save(update_fields=["status", "end_time"])
    audit_event(actor=actor, action="appointment.completed", instance=appointment)
    return appointment
