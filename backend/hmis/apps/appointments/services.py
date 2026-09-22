from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.billing.notifications import announce
from apps.billing.services import refresh_ledger
from apps.core.services import audit_event

from . import booking
from .models import OPEN_STATUSES, Appointment


def open_appointment_for(*, doctor, patient):
    """
    The patient's existing live queue entry with this doctor, if any.

    Reads the same `OPEN_STATUSES` the database constraint is conditioned on,
    so the readable refusal and the uninterleavable one describe one rule. This
    is still the guard that answers the ordinary repeat — a double-click, a
    resubmit — with a sentence somebody can act on; the constraint is what
    catches the race behind it.
    """
    return Appointment.objects.filter(
        doctor=doctor, patient=patient, status__in=OPEN_STATUSES
    ).first()


#: `Appointment.service_name` is 160 characters. A booking of eight scans
#: would not fit, so past that it says how many rather than being truncated
#: mid-word.
_LABEL_LIMIT = 160


def combined_label(names):
    """
    What several services are called on one row.

    One service is its own name — which is what a single-service booking has
    always stored, so nothing about those rows changes.
    """
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    joined = " + ".join(names)
    if len(joined) <= _LABEL_LIMIT:
        return joined
    return f"{names[0]} + {len(names) - 1} more"


@transaction.atomic
def queue_appointment(*, appointment, service_key, actor):
    """
    Put the booked service or services on a queued appointment, and raise
    their bills.

    This is what turns "patient → doctor" into "patient → department →
    service(s) → provider" without any of it becoming a second system:

    * the **services** are resolved from `booking.py`, so a key that names
      nothing bookable never gets this far (the view refuses it first);
    * the **name and fee are snapshotted** onto the row (rule 21) — re-pricing
      the catalogue tomorrow leaves this appointment saying what the patient
      was quoted today — and `services` keeps the same snapshot per service,
      so a basket is readable line by line as well as in total;
    * the **department** is the one their category resolves to, which is the
      same row the charges are attributed to, so the appointment and its bills
      can never name two different units;
    * the **charges** are ordinary `add_charge` calls at the catalogue's
      prices, with the category as `source_type` and the appointment as
      `source_id` — one per service, indistinguishable from the same services
      billed at the counter, and therefore already understood by the ledger,
      the statement, discounts, waivers, refunds, cancellations and every
      report.

    **One charge per service, one announcement for the booking.** A patient
    sent for three scans has one bill to settle at one window, and the cash
    desk wants one line about it (rule 35) — the same reason
    `laboratory.services.add_tests` passes `notify=False` down and announces
    the total itself.

    A service priced at zero raises **nothing**: a zero charge is noise on a
    bill, and "no charge" is a real answer (`billing.status.unbilled`). It is
    still recorded in `services`, because it was still booked.

    Called only with an appointment that has just been created, inside the
    same transaction, so a refused charge takes the booking with it — and so
    does the database refusing a second open appointment for this patient and
    provider.
    """
    keys = service_key if isinstance(service_key, (list, tuple)) else [service_key]
    services, _ = booking.services_for([key for key in keys if key not in (None, "")])
    if not services:
        return appointment

    from apps.billing.services import add_charge

    booked, raised = [], []
    for service in services:
        fee = Decimal(service["price"])
        charge = None
        if fee > 0:
            charge = add_charge(
                patient=appointment.patient,
                description=service["name"],
                amount=fee,
                created_by=actor,
                source_type=service["source_type"],
                source_id=appointment.pk,
                # Announced once below, for the whole booking.
                notify=False,
            )
            raised.append(charge)
        booked.append({
            "key": service["key"],
            "id": service["id"],
            "name": service["name"],
            "fee": f"{fee:.2f}",
            "charge": charge.pk if charge is not None else None,
        })

    appointment.services = booked
    # The single-service columns keep their exact meaning: with one service
    # these are that service's own name and fee, which is what every existing
    # reader and every historical row already says.
    appointment.service_id = services[0]["id"]
    appointment.service_name = combined_label([row["name"] for row in booked])
    appointment.service_fee = sum((Decimal(row["fee"]) for row in booked), Decimal("0"))
    appointment.department = booking.department_of(services[0])
    appointment.charge = raised[0] if raised else None
    appointment.save(update_fields=["services", "service", "service_name", "service_fee",
                                    "department", "charge"])

    if raised:
        announce(
            event="charge_raised",
            patient=appointment.patient,
            amount=sum((charge.amount for charge in raised), Decimal("0")),
            actor=actor,
            detail=(appointment.service_name if len(raised) == 1
                    else f"Appointment — {len(raised)} services"),
            outstanding=refresh_ledger(appointment.patient).outstanding_balance,
        )
    return appointment


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
