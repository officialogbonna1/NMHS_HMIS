"""
One definition of "which patients is this clinician assigned to".

Patient identity, the health-record tiles, and Vitals all have to agree on
this: a doctor who can open a patient must also be able to read that
patient's vitals, otherwise the chart silently shows nothing. Keeping the Q
objects here means adding a new way to assign a patient (a route, a queue
entry) updates every one of those places at once.
"""
from django.db import models


def _prefixed(prefix, lookup):
    return f"{prefix}__{lookup}" if prefix else lookup


def doctor_patient_q(user, prefix=""):
    """
    A patient belongs to a doctor when the front desk has put that patient in
    front of them: as the visit's attending doctor, via a route assigned to
    them, or via an appointment queued to them.
    """
    return (
        models.Q(**{_prefixed(prefix, "visits__attending_doctor"): user})
        | models.Q(**{_prefixed(prefix, "visits__routes__assigned_to"): user})
        | models.Q(**{_prefixed(prefix, "appointments__doctor"): user})
    )


def nurse_patient_q(user, prefix=""):
    """A nurse works the patients routed to them or to their department."""
    return (
        models.Q(**{_prefixed(prefix, "visits__routes__assigned_to"): user})
        | models.Q(**{_prefixed(prefix, "visits__routes__department__staff"): user})
        | models.Q(**{_prefixed(prefix, "visits__routes__department__name__iexact"): user.department})
    )


def patient_queryset_for(user):
    """
    The patients a role sees. Lives here with the other assignment rules so
    that the Patients page and anything that counts it — the dashboard card
    that opens it, above all — can never disagree about who is on the list.
    """
    from .models import Patient

    if user.role in {"admin", "hospital_admin", "reception"}:
        return Patient.objects.all()
    if user.role == "doctor":
        return Patient.objects.filter(doctor_patient_q(user)).distinct()
    if user.role == "nurse":
        return Patient.objects.filter(nurse_patient_q(user)).distinct()
    if user.role == "pharmacist":
        # Prescriptions waiting to be filled, plus anyone the pharmacy has
        # dispensed to but not yet been paid by — the counter still has to
        # take that money.
        return Patient.objects.filter(
            models.Q(prescriptions__status="pending")
            | models.Q(prescriptions__status="dispensed", charges__status__in=["unpaid", "partial"])
        ).distinct()
    if user.role in {"laboratory", "radiology", "optometrist", "ophthalmologist"}:
        return Patient.objects.filter(investigation_orders__status__in=["requested", "collected", "in_progress"]).distinct()
    if user.role in {"cashier", "accountant"}:
        return Patient.objects.filter(models.Q(charges__status__in=["unpaid", "partial"]) | models.Q(payments__isnull=False)).distinct()
    if user.role == "ward_manager":
        return Patient.objects.filter(admissions__status="admitted").distinct()
    return Patient.objects.none()


def doctors_for_patient(patient):
    """
    The mirror of `doctor_patient_q`: given a patient, which doctors is that
    patient in front of *right now*. Used to tell a doctor when something
    lands on a chart they are holding — the same assignment rule read the
    other way round, so a doctor is never told about a patient they cannot
    open, and never missed for one they can.

    "Right now" is deliberately narrower than the Q: a closed visit or a
    finished route is history, not a claim on the doctor's attention.
    """
    # Imported here rather than at module scope: workflow's models already
    # depend on patients', and access.py is pulled in early by the viewsets.
    from apps.workflow.models import Visit, PatientRoute
    from apps.appointments.models import Appointment

    ids = set(
        Visit.objects.filter(patient=patient, status="open", attending_doctor__isnull=False)
        .values_list("attending_doctor_id", flat=True)
    )
    ids |= set(
        PatientRoute.objects.filter(
            visit__patient=patient, status__in=["queued", "in_progress"],
            assigned_to__role="doctor",
        ).values_list("assigned_to_id", flat=True)
    )
    ids |= set(
        Appointment.objects.filter(
            patient=patient, status__in=["queued", "accepted", "in_progress"],
        ).values_list("doctor_id", flat=True)
    )
    ids.discard(None)
    if not ids:
        return []

    from apps.accounts.models import User
    return list(User.objects.filter(pk__in=ids, is_active=True, role="doctor"))
