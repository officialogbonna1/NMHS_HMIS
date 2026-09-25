"""
Who Maternity's patients are, who the midwives are, and who is responsible
for whom.

**Nothing here is a second assignment system.** A patient belongs to Maternity
because the front desk filed a `PatientRoute` against the Maternity department
— the same mechanism that sends a patient to the eye clinic or to triage — and
the midwife responsible for her is that route's `assigned_to`, which has been
an optional column since the routing was written. What this module adds is the
*reading*: the ward's patient list, the ward's staff list, and the one place
that says which route is the maternity one.

The security rule is `patients.access.patient_queryset_for` and is not
restated here. Every function below narrows what that already allows; none of
them widens it, so a role that cannot see a patient at all cannot reach her
through maternity either.
"""
from django.db import models

from apps.accounts.models import User
from apps.accounts.permissions import MATERNITY_ROLES
from apps.patients.access import MATERNITY_DEPARTMENT_CODE, maternity_patient_q, patient_queryset_for
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute

#: The role that *is* the labour ward. `MATERNITY_ROLES` is wider — a doctor
#: and a general nurse work maternity too (rule 56) — but "the responsible
#: midwife" names this role, which is why the assignee dropdown offers it and
#: `PURPOSE_ROLE["maternity"]` holds it alone.
MIDWIFE_ROLE = "maternity_nurse"

#: Route statuses that still mean "she is in Maternity's care". A completed or
#: cancelled route is history — but it is history that still makes her a
#: maternity patient for the picker, which is why `maternity_patient_q` does
#: not filter on status and this does. The two answer different questions:
#: "has this ward ever had her" and "is she here now".
OPEN_ROUTE_STATUSES = ["queued", "in_progress"]


def maternity_patients_for(user, *, search="", scope="maternity"):
    """
    The maternity patients this user may see, newest attendance first.

    `scope="maternity"` (the default) is the ward's list. `scope="all"` drops
    the maternity filter and returns the caller's own authorised patients —
    which is how the front desk finds a woman who is not in Maternity *yet*,
    the first step of putting her there. It is not a way round anything: for
    a midwife the two answers are identical, because her authorised set is
    already exactly Maternity's, and the test that proves it is the point.
    """
    patients = patient_queryset_for(user)
    if scope != "all":
        patients = patients.filter(maternity_patient_q())
    term = (search or "").strip()
    if term:
        patients = patients.filter(
            models.Q(first_name__icontains=term)
            | models.Q(last_name__icontains=term)
            | models.Q(patient_number__icontains=term)
            | models.Q(phone_number__icontains=term)
        )
    return patients.distinct()


def assigned_doctors_for(patients):
    """
    The responsible doctor for each of many patients, in one query — the
    mirror of `assigned_nurses_for`, read off `Visit.attending_doctor`
    because that is where a responsible doctor has always been recorded.
    """
    ids = [p.pk for p in patients]
    if not ids:
        return {}
    rows = (PatientRoute.objects
            .filter(visit__patient_id__in=ids,
                    department__code__iexact=MATERNITY_DEPARTMENT_CODE)
            .order_by(models.Case(models.When(status__in=OPEN_ROUTE_STATUSES, then=1),
                                  default=0, output_field=models.IntegerField()),
                      "created_at")
            .values_list("visit__patient_id", "visit__attending_doctor_id"))
    found = {}
    for patient_id, doctor_id in rows:
        found[patient_id] = doctor_id
    doctor_ids = {doctor_id for doctor_id in found.values() if doctor_id}
    people = ({u.pk: u for u in User.objects.filter(pk__in=doctor_ids)}
              if doctor_ids else {})
    return {patient_id: people.get(doctor_id) for patient_id, doctor_id in found.items()}


def maternity_route_for(patient):
    """
    The route that puts this patient in Maternity's care — the live one if
    there is one, else the most recent.

    One definition, read by the assignment services, by the serializer that
    shows the desk who is responsible, and by the refusal when somebody tries
    to name a nurse for a woman who is not in Maternity at all.
    """
    routes = (PatientRoute.objects
              .filter(visit__patient=patient,
                      department__code__iexact=MATERNITY_DEPARTMENT_CODE)
              .select_related("assigned_to", "department", "visit__patient"))
    return (routes.filter(status__in=OPEN_ROUTE_STATUSES).order_by("-created_at").first()
            or routes.order_by("-created_at").first())


def assigned_nurses_for(patients):
    """
    The responsible midwife for each of many patients, in **one** query.

    The picker opens on up to fifty mothers, and asking per row is fifty
    queries to draw one list. The live route wins over a closed one, which is
    the same preference `maternity_route_for` makes for a single patient —
    stated once here, as ordering, so the two cannot disagree.
    """
    ids = [p.pk for p in patients]
    if not ids:
        return {}
    found = {}
    routes = (PatientRoute.objects
              .filter(visit__patient_id__in=ids,
                      department__code__iexact=MATERNITY_DEPARTMENT_CODE)
              .select_related("assigned_to")
              # Oldest first, live last: the last write into the dict wins, so
              # the newest live route is the one that lands.
              .order_by(models.Case(models.When(status__in=OPEN_ROUTE_STATUSES, then=1),
                                    default=0, output_field=models.IntegerField()),
                        "created_at")
              .values_list("visit__patient_id", "assigned_to_id"))
    people = {}
    for patient_id, nurse_id in routes:
        found[patient_id] = nurse_id
    nurse_ids = {nurse_id for nurse_id in found.values() if nurse_id}
    if nurse_ids:
        people = {u.pk: u for u in User.objects.filter(pk__in=nurse_ids)}
    return {patient_id: people.get(nurse_id) for patient_id, nurse_id in found.items()}


def assigned_nurse_for(patient):
    """The midwife responsible for her, or None. Responsibility, not access."""
    route = maternity_route_for(patient)
    return route.assigned_to if route is not None else None


def assigned_doctor_for(patient):
    """
    The doctor responsible for her, or None — read off `Visit.attending_doctor`,
    the column that has always meant that. Responsibility, not access: the team
    is the department (`in_maternity_team`).
    """
    route = maternity_route_for(patient)
    return route.visit.attending_doctor if route is not None else None


def current_admission(patient):
    """
    Where she is lying **right now**, read off the hospital's own
    `inpatient.Admission` — the bed names the ward, so nothing here stores
    either. A bed transfer made on the ward board moves this answer with it
    without maternity being told, which is the whole reason it is derived.

    Returns None when she is not admitted, which is a state and not a gap:
    "Not admitted" is what the screen says.
    """
    from apps.inpatient.models import Admission

    return (Admission.objects
            .filter(patient=patient, status="admitted")
            .select_related("bed__ward", "attending_doctor")
            .order_by("-admitted_at")
            .first())


def admission_state(patient):
    """The admission as a screen reads it: admitted or not, and where."""
    admission = current_admission(patient)
    if admission is None:
        return {"admitted": False, "ward": None, "bed": None,
                "admitted_at": None, "reference": None}
    return {
        "admitted": True,
        "ward": admission.bed.ward.name,
        "bed": admission.bed.number,
        "admitted_at": admission.admitted_at,
        "reference": f"ADM-{admission.pk:06d}",
        "attending_doctor": (admission.attending_doctor.get_full_name()
                             or admission.attending_doctor.username)
        if admission.attending_doctor_id else None,
    }


def in_maternity(patient):
    """Is she the Maternity department's? The picker's filter, asked of one row."""
    return Patient.objects.filter(pk=patient.pk).filter(maternity_patient_q()).exists()


def in_maternity_team(user):
    """
    Is this person **authorized maternity clinical staff**?

    This is the question the whole team-visibility rule turns on, and the
    answer is the hospital's existing one — not a new flag, a new role or a
    new table. Two ways, both already in use elsewhere:

    - the `maternity_nurse` role, which *is* the labour ward; and
    - membership of the Maternity `Department` — its `staff` list, or the
      `user.department` text beside it — which is exactly the clause
      `patients.access.nurse_patient_q` and `workflow.work_routes_for` have
      always used to mean "authorized in this department".

    A doctor becomes a maternity doctor by being put on Maternity's staff, the
    same way a doctor becomes a theatre doctor. **Deliberately not "every
    doctor"**: an unrelated doctor keeps the access the existing rules give
    them and gains nothing from a patient being in Maternity.

    It is not read as a gate on the assigned person — `doctor_patient_q`
    already gives the assigned doctor access through `Visit.attending_doctor`,
    and `assigned_to` gives the assigned nurse hers. This is what the *rest of
    the team* holds, which is the point.
    """
    from apps.accounts.departments import works_in

    role = getattr(user, "role", None)
    if role is None:
        return False
    if role == MIDWIFE_ROLE:
        return True
    if role not in MATERNITY_ROLES:
        return False
    # **The hospital's own rule, not a maternity one.** `works_in` reads the
    # `Department.staff` relation an administrator sets, plus the legacy text
    # — so a doctor is authorised here exactly the way they are authorised in
    # Theatre or the Laboratory, and maternity contributes no authorisation
    # logic of its own. The role check above is what keeps it **role AND
    # department**: a cashier posted to Maternity is still a cashier.
    return works_in(user, MATERNITY_DEPARTMENT_CODE)


def maternity_nurses():
    """
    Who may be named as the **responsible midwife**.

    The role first — every active `maternity_nurse` — plus any nurse on the
    Maternity department's own staff list. The role is what stops an unfilled
    staff list stranding the ward (rule 16).
    """
    return _roster(["nurse", MIDWIFE_ROLE], always=[MIDWIFE_ROLE])


def maternity_doctors():
    """
    Who may be named as the **responsible doctor**.

    There is no `maternity_doctor` role and inventing one is exactly what this
    work is not for, so the roster is the Maternity department's own doctors —
    the existing mechanism. **With one fallback**: where nobody has been put
    on that staff list, every active doctor is offered, because a ward that
    cannot name a doctor at all is rule 16's stranding with a patient in it.

    The fallback widens *who can be named*, never *who can see the ward*.
    `in_maternity_team` has no fallback: naming a doctor is what gives him the
    patient (through `Visit.attending_doctor`, the rule that already existed),
    and it is a decision somebody takes rather than one the system assumes.
    """
    named = _roster(["doctor"])
    return named if named.exists() else User.objects.filter(
        role="doctor", is_active=True).order_by("last_name", "first_name", "username")


def _roster(roles, always=()):
    """
    Active staff in `roles` who are authorised in Maternity — plus a role in
    `always` that *is* the ward by definition.

    `accounts.departments.staff_of` is the department half, so the people a
    selector offers are exactly the people `works_in` will accept. A selector
    that offered anybody else would be offering a choice the server refuses.
    """
    from apps.accounts.departments import staff_of
    from apps.departments.models import Department

    department = Department.objects.filter(code__iexact=MATERNITY_DEPARTMENT_CODE).first()
    posted = list(staff_of(department, roles=roles).values_list("pk", flat=True))
    people = models.Q(pk__in=posted)
    if always:
        people |= models.Q(role__in=list(always))
    return User.objects.filter(people, is_active=True).distinct().order_by(
        "last_name", "first_name", "username")


def maternity_staff():
    """
    The ward's whole roster — both rosters, as one list for a screen that
    shows staff rather than one that is filling a particular column.

    It is the union of `maternity_nurses` and `maternity_doctors` and holds no
    rule of its own, so there is no third definition of "who is maternity's"
    free to drift from the two that decide anything.
    """
    return User.objects.filter(
        pk__in=list(maternity_nurses().values_list("pk", flat=True))
        + list(maternity_doctors().values_list("pk", flat=True)),
    ).order_by("last_name", "first_name", "username")


def may_be_assigned(user, *, as_doctor=False):
    """
    Could this account actually carry the responsibility being handed to it?

    The two rosters are asked separately, so a doctor on Maternity's staff
    cannot be filed as the responsible *midwife* and a midwife cannot be filed
    as the responsible doctor. One column each, meaning one thing each.
    """
    if user is None:
        return False
    roster = maternity_doctors() if as_doctor else maternity_nurses()
    return roster.filter(pk=user.pk).exists()
