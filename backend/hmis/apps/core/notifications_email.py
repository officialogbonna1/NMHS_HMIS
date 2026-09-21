"""
The four events the administrator is emailed about.

One function per event, each turning a hospital record into a subject, a
template and a list of labelled rows. They exist so that no view or service
assembles email content itself: a handler calls `patient_registered(patient)`
and is done, and what an email says is changed in one file.

**Every one of them returns None and raises nothing.** They are called from
inside hospital transactions that have already succeeded, and
`core.email.dispatch_admin_email` queues on commit — so a rolled-back
transaction sends nothing, and a failed send is a log line rather than an
exception climbing back into the caller. There is no path from here that can
fail a registration, a bill, a payment or a discharge.

**What goes in an email, and what does not.** The administrator is being told
that something happened and given enough to find it: who, when, which
reference, how much. A billed *service* is named because that is the line on
the bill the patient is already holding — the same boundary rule 35 draws for
the on-screen notification. A diagnosis, a drug, a test result or the course of
a stay is not, in any of them. The discharge letter carries the clinical
detail, printed and handed over; an inbox is the wrong place for it.
"""
from .email import dispatch_admin_email

#: Rendered in front of every money figure. The hospital bills in naira.
CURRENCY = "₦"


def _money(amount):
    try:
        return f"{CURRENCY}{float(amount):,.2f}"
    except (TypeError, ValueError):
        return f"{CURRENCY}{amount}"


def _when(moment):
    from django.utils import timezone
    if moment is None:
        return ""
    try:
        return timezone.localtime(moment).strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return str(moment)


def _who(user):
    if not user:
        return "—"
    return user.get_full_name() or user.username


def _patient_rows(patient):
    """The identity block every one of these emails opens with."""
    return [
        ("Patient", patient.display_name),
        ("Hospital number", getattr(patient, "patient_number", "") or "—"),
    ]


def _chart_url(patient):
    """
    The patient's own chart, addressed by UUID — never the integer primary key
    (rule 32). An email travels outside the application and a guessable id in
    a link is exactly what the UUID exists to avoid.
    """
    uuid = getattr(patient, "uuid", None)
    return f"/patients/{uuid}" if uuid else ""


def _send(*, event, reference, subject, template, context, patient=None):
    from .email import base_url

    url = _chart_url(patient) if patient is not None else ""
    dispatch_admin_email(
        event=event, reference=str(reference or ""), subject=subject, template=template,
        context={**context,
                 "action_url": f"{base_url()}{url}" if url and base_url() else "",
                 "action_label": "Open patient record" if url else ""},
    )


# --------------------------------------------------------------------- events


def patient_registered(patient, *, registered_by=None):
    """A — a new patient is on the register."""
    from django.utils import timezone

    _send(
        event="patient.registered",
        # The hospital number is the event: one patient, one registration, and
        # it never changes. A re-submitted form cannot email twice.
        reference=getattr(patient, "patient_number", "") or getattr(patient, "pk", ""),
        subject="New Patient Registered — NMHS",
        template="patient_registered",
        patient=patient,
        context={
            "kicker": "Patient registration",
            "title": "New patient registered",
            "summary": f"{patient.display_name} has been registered at the hospital.",
            "rows": [
                *_patient_rows(patient),
                ("Sex", getattr(patient, "get_sex_display", lambda: "")() or "—"),
                ("Age", getattr(patient, "age_display", "") or "—"),
                ("Phone", getattr(patient, "phone_number", "") or "—"),
                ("Registered by", _who(registered_by)),
                ("Registered", _when(getattr(patient, "created_at", None) or timezone.now())),
            ],
        },
    )


def patient_billed(*, charge, patient, actor, outstanding=None):
    """B — a charge has been raised."""
    rows = [
        *_patient_rows(patient),
        ("Bill reference", f"CHG-{charge.pk:06d}"),
        ("Service", getattr(charge, "description", "") or "—"),
        ("Department", getattr(getattr(charge, "department", None), "name", "") or "—"),
        ("Status", str(getattr(charge, "status", "") or "—").replace("_", " ").title()),
        ("Billed by", _who(actor)),
        ("Raised", _when(getattr(charge, "created_at", None))),
    ]
    if outstanding is not None:
        rows.append(("Outstanding after", _money(outstanding)))
    _send(
        event="patient.billed", reference=charge.pk,
        subject="Patient Billing Notification — NMHS",
        template="patient_billed", patient=patient,
        context={
            "kicker": "Billing",
            "title": "Patient billed",
            "summary": f"A charge has been raised on {patient.display_name}'s account.",
            "amount_label": "Amount billed",
            "amount": _money(getattr(charge, "amount", 0)),
            "rows": rows,
        },
    )


def payment_received(*, payment, patient, actor, outstanding=None):
    """C — money has been taken."""
    rows = [
        *_patient_rows(patient),
        ("Payment reference", f"PAY-{payment.pk:06d}"),
        ("Method", str(getattr(payment, "method", "") or "—").replace("_", " ").title()),
        ("Received at", str(getattr(payment, "channel", "") or "—").replace("_", " ").title()),
        ("Received by", _who(actor)),
        ("Received", _when(getattr(payment, "created_at", None))),
    ]
    if getattr(payment, "reference", ""):
        rows.append(("Their reference", payment.reference))
    if outstanding is not None:
        rows.append(("Outstanding after", _money(outstanding)))
    _send(
        event="patient.payment", reference=payment.pk,
        subject="Payment Received — NMHS",
        template="payment_received", patient=patient,
        context={
            "kicker": "Payment",
            "title": "Payment received",
            "summary": f"A payment has been recorded against {patient.display_name}'s account.",
            "amount_label": "Amount paid",
            "amount": _money(getattr(payment, "amount", 0)),
            "rows": rows,
        },
    )


def patient_discharged(*, summary, admission, patient, actor):
    """
    D — a patient has left the ward.

    Administrative only: the diagnosis, the course of the stay and the
    instructions given are on the discharge letter, which is printed and handed
    over. What is here is who left, when, from where, on whose authority and
    under which reference.
    """
    _send(
        event="patient.discharged",
        # The discharge record itself. Reopening or reprinting it cannot
        # produce a second email, because there is only ever one of these.
        reference=summary.pk,
        subject="Patient Discharged — NMHS",
        template="patient_discharged", patient=patient,
        context={
            "kicker": "Discharge",
            "title": "Patient discharged",
            "summary": f"{patient.display_name} has been discharged from the ward.",
            "rows": [
                *_patient_rows(patient),
                ("Discharge reference", getattr(summary, "reference", "") or f"DCH-{summary.pk:06d}"),
                ("Admission reference", getattr(admission, "reference", "") or f"ADM-{admission.pk:06d}"),
                ("Ward / bed", _bed(admission)),
                ("Admitted", _when(getattr(admission, "admitted_at", None))),
                ("Discharged", _when(getattr(admission, "discharged_at", None))),
                ("Discharge status", str(getattr(admission, "status", "") or "").title() or "—"),
                ("Discharged by", _who(actor)),
            ],
        },
    )


def _bed(admission):
    bed = getattr(admission, "bed", None)
    if not bed:
        return "—"
    ward = getattr(getattr(bed, "ward", None), "name", "")
    return f"{ward} · Bed {bed.number}" if ward else f"Bed {bed.number}"


__all__ = ["patient_registered", "patient_billed", "payment_received", "patient_discharged"]
