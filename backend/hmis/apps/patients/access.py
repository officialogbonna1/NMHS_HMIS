"""
One definition of "which patients is this clinician assigned to".

Patient identity, the health-record tiles, and Vitals all have to agree on
this: a doctor who can open a patient must also be able to read that
patient's vitals, otherwise the chart silently shows nothing. Keeping the Q
objects here means adding a new way to assign a patient (a route, a queue
entry) updates every one of those places at once.
"""
from django.db import models

from apps.accounts.permissions import CLINICIAN_ROLES

# Roles whose *writes* are held to their own patient list, as well as their
# reads. The eye doctor was let into the chart, prescribing, referring and the
# ward with that boundary from the start: writing a note, a script or an
# admission for a patient who is not theirs is refused. The general doctor's
# writes predate the rule and are unchanged — widening it to them is one edit
# here, and a decision of its own.
ASSIGNED_WRITE_ROLES = {"ophthalmologist"}


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
    from apps.accounts.departments import authorized_departments

    return (
        models.Q(**{_prefixed(prefix, "visits__routes__assigned_to"): user})
        # Every department she is authorised in, not only the one her text
        # names — the relation has always allowed several and now one helper
        # reads it (`accounts/departments.py`).
        | models.Q(**{_prefixed(prefix, "visits__routes__department__in"):
                      authorized_departments(user)})
    )


#: The seeded Maternity department (`departments/0006`). A code, never a name:
#: an administrator may rename the department and every rule here follows.
MATERNITY_DEPARTMENT_CODE = "maternity"


def maternity_patient_q(prefix=""):
    """
    The **Maternity department's** patients — the ward's, never one midwife's.

    Note what it does not take: a user. That is the whole point. A maternity
    ward cannot depend on a patient being handed to one named nurse before the
    rest of the ward can see her — the midwife on at 3 a.m. is not the one the
    front desk typed in at noon. So the department is what confers visibility,
    and `PatientRoute.assigned_to` says only who is *responsible*, narrowing
    nothing.

    Two ways a woman is Maternity's: the front desk sent her there (a route
    filed against the Maternity department, which is the existing assignment
    mechanism — there is no second one), or she has a pregnancy record here,
    which is the same statement made clinically.
    """
    pregnancy = models.Q(**{_prefixed(prefix, "pregnancies__isnull"): False})
    department = models.Q(**{
        _prefixed(prefix, "visits__routes__department__code__iexact"): MATERNITY_DEPARTMENT_CODE})
    purpose = models.Q(**{_prefixed(prefix, "visits__routes__purpose"): "maternity"})
    return pregnancy | department | purpose


def _on_the_maternity_team(user):
    """
    `maternity.access.in_maternity_team`, reached lazily — that module reads
    this one at import time, so the other direction cannot be a module-level
    import. One definition either way.
    """
    from apps.maternity.access import in_maternity_team

    return in_maternity_team(user)


def _clinician_q(user):
    """A doctor's own patients, plus the maternity team's where they are on it."""
    q = doctor_patient_q(user)
    if _on_the_maternity_team(user):
        q |= maternity_patient_q()
    return q


def patient_queryset_for(user):
    """
    The patients a role sees. Lives here with the other assignment rules so
    that the Patients page and anything that counts it — the dashboard card
    that opens it, above all — can never disagree about who is on the list.
    """
    from .models import Patient

    if user.role in {"admin", "hospital_admin", "reception"}:
        return Patient.objects.all()
    if user.role in CLINICIAN_ROLES:
        # The eye doctor holds a patient the same three ways a doctor does —
        # most often an eye referral they claimed or were named on. An
        # unclaimed referral is in the shared /eye queue, not on this list.
        #
        # **Plus the team's, for a doctor on Maternity's staff.** A maternity
        # ward is worked by its whole team: the patient must not disappear
        # from it because a colleague is the named doctor, which is what
        # `doctor_patient_q` alone would do. So the *department* confers the
        # visibility — the clause `nurse_patient_q` has always carried, read
        # here through `maternity.access.in_maternity_team`.
        #
        # It is the department and not the assignment: `doctor_patient_q`
        # already gives the assigned doctor the patient through
        # `Visit.attending_doctor`, so nothing here turns an assignment into
        # an ACL. And it is Maternity's staff and not every doctor, so an
        # unrelated doctor gains nothing from a patient being in Maternity.
        return Patient.objects.filter(_clinician_q(user)).distinct()
    if user.role == "nurse":
        # The same team clause for a general nurse on Maternity's staff. Her
        # own rule already carries the department (`nurse_patient_q`), so this
        # adds only the mother whose record is clinical rather than routed —
        # a pregnancy opened with no maternity route behind it yet.
        q = nurse_patient_q(user)
        if _on_the_maternity_team(user):
            q |= maternity_patient_q()
        return Patient.objects.filter(q).distinct()
    if user.role == "maternity_nurse":
        # The ward's list, not hers. She had no entry here at all, so this
        # fell through to `none()` and her patient picker was permanently
        # empty — which is what made the maternity desk look as though it
        # needed a hospital number typed in full before it would answer.
        #
        # It is also the boundary in the other direction: a midwife reaches
        # Maternity's patients and **no others**, so a cardiology admission
        # she has no part in is not on her list however she asks for it.
        return Patient.objects.filter(maternity_patient_q()).distinct()
    if user.role == "pharmacist":
        # The queue, plus everyone the pharmacy has actually served.
        #
        # A patient does not stop being the pharmacy's the moment they pay.
        # This used to require an unpaid charge alongside a dispensed
        # prescription, so somebody who collected their drugs and settled at
        # the counter dropped off the list — and "who did I hand that to on
        # Tuesday?" became unanswerable from the Patients page.
        #
        # (That condition was also wrong on its own terms: two multi-valued
        # relations in one Q match independently, so any unpaid charge at all
        # — a consultation fee, a lab test — satisfied it, whether or not it
        # was the pharmacy's.)
        return Patient.objects.filter(
            prescriptions__status__in=["pending", "dispensed"]
        ).distinct()
    if user.role in {"laboratory", "radiology", "optometrist"}:
        return Patient.objects.filter(investigation_orders__status__in=["requested", "collected", "in_progress"]).distinct()
    if user.role in {"cashier", "accountant"}:
        return Patient.objects.filter(models.Q(charges__status__in=["unpaid", "partial"]) | models.Q(payments__isnull=False)).distinct()
    if user.role == "ward_manager":
        return Patient.objects.filter(admissions__status="admitted").distinct()
    return Patient.objects.none()


def holds_patient(user, patient):
    """Is `patient` on this user's own list — the set `patient_queryset_for` returns."""
    return patient_queryset_for(user).filter(pk=patient.pk).exists()


def may_act_for(user, patient):
    """
    May this user write something onto `patient`'s record — a note, a script,
    a referral, an admission? Always, for a role outside ASSIGNED_WRITE_ROLES
    (their existing behaviour); only their own patients, for a role inside it.
    """
    if getattr(user, "role", None) not in ASSIGNED_WRITE_ROLES:
        return True
    return holds_patient(user, patient)


def doctors_for_patient(patient, roles=("doctor",)):
    """
    The mirror of `doctor_patient_q`: given a patient, which doctors is that
    patient in front of *right now*. Used to tell a doctor when something
    lands on a chart they are holding — the same assignment rule read the
    other way round, so a doctor is never told about a patient they cannot
    open, and never missed for one they can.

    "Right now" is deliberately narrower than the Q: a closed visit or a
    finished route is history, not a claim on the doctor's attention.

    `roles` defaults to the general doctor, because routing reads this to
    decide who hears about a consultation or a procedure — work only a doctor
    can pick up (rule 16). A caller telling clinicians about a *reading* on a
    chart they hold passes CLINICIAN_ROLES.
    """
    roles = list(roles)
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
            assigned_to__role__in=roles,
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
    return list(User.objects.filter(pk__in=ids, is_active=True, role__in=roles))
