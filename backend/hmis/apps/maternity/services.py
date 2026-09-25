"""
What the maternity desk is allowed to do, and what it must never do.

The one rule this module exists to enforce: **a woman who comes back is the
same woman, in the same pregnancy, on a new visit.** Every function here is
written so that returning cannot create a second patient and cannot create a
second pregnancy — the two mistakes that make a maternity record unreadable.
"""
from django.db import transaction

from apps.core.services import audit_event, notify

from .models import MaternityEncounter, MaternityVisitType, Pregnancy


class MaternityError(Exception):
    """A refusal the desk can act on. Carries the `code` the screen branches on."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def active_pregnancy_for(patient):
    """
    The pregnancy she is currently in, or None.

    One query and one answer, read by the lookup, by the API's refusals and by
    the tests, so the screen and the server can never disagree about whether
    she has an open episode.
    """
    if patient is None:
        return None
    return (Pregnancy.objects
            .filter(patient=patient, status__in=Pregnancy.OPEN_STATUSES)
            .order_by("-number")
            .first())


def pregnancy_history_for(patient):
    """Every pregnancy she has had here, newest first — active and finished."""
    if patient is None:
        return Pregnancy.objects.none()
    return (Pregnancy.objects.filter(patient=patient)
            .prefetch_related("encounters__visit_type")
            .order_by("-number"))


@transaction.atomic
def start_pregnancy(*, patient, actor, lmp=None, edd=None, gravida=None, para=None, notes=""):
    """
    Open a **new** episode for a woman who has no open one.

    Refused outright while one is active (`pregnancy_already_active`): a
    returning patient continues what she is already in, and "start pregnancy"
    pressed by mistake must not silently fork her record. The database says
    the same thing under a race (`one_active_pregnancy_per_patient`).

    Her previous pregnancies are read only to work out the next number. None
    of them is touched — that is what makes pregnancy #1 still true after
    pregnancy #3.
    """
    if active_pregnancy_for(patient) is not None:
        raise MaternityError(
            f"{patient.display_name} already has an active pregnancy. "
            "Continue it rather than starting another.",
            code="pregnancy_already_active",
        )
    last = Pregnancy.objects.filter(patient=patient).order_by("-number").first()
    pregnancy = Pregnancy.objects.create(
        patient=patient,
        number=(last.number + 1) if last else 1,
        lmp=lmp, edd=edd, gravida=gravida, para=para, notes=notes or "",
        opened_by=actor,
    )
    audit_event(actor=actor, action="maternity.pregnancy_started", instance=pregnancy,
                details={"patient": patient.patient_number, "number": pregnancy.number})
    return pregnancy


@transaction.atomic
def close_pregnancy(*, pregnancy, actor, outcome, ended_on=None, notes=""):
    """
    End an episode, keeping every visit in it.

    Nothing is deleted and nothing is rewritten: the status moves, the outcome
    is recorded, and the encounters stay exactly as they were written. She can
    start a new pregnancy afterwards, which will be a new row beside this one.
    """
    import datetime

    if not pregnancy.is_active:
        raise MaternityError("That pregnancy has already ended.", code="pregnancy_not_active")
    if outcome not in dict(Pregnancy.OUTCOME):
        raise MaternityError("That is not a recorded pregnancy outcome.",
                             code="unknown_outcome")
    pregnancy.status = "completed" if outcome == "delivered" else "ended"
    pregnancy.outcome = outcome
    pregnancy.ended_on = ended_on or datetime.date.today()
    if notes:
        pregnancy.notes = f"{pregnancy.notes}\n{notes}".strip()
    pregnancy.save(update_fields=["status", "outcome", "ended_on", "notes", "updated_at"])
    audit_event(actor=actor, action="maternity.pregnancy_closed", instance=pregnancy,
                details={"outcome": outcome})
    return pregnancy


@transaction.atomic
def open_encounter(*, pregnancy, visit_type, actor, provider=None, visit=None, summary=""):
    """
    Record today's attendance — a **new row**, always.

    This is the whole of "she came back": no earlier encounter is read, copied
    or modified, so ANC 3 cannot overwrite ANC 2 and a labour assessment does
    not become an ANC follow-up. The pregnancy must still be open, because an
    attendance inside a finished episode is a record nobody can interpret.
    """
    if not pregnancy.is_active:
        raise MaternityError(
            "That pregnancy has ended. Start a new pregnancy to record a visit.",
            code="pregnancy_not_active")
    if not visit_type.is_active:
        raise MaternityError("That visit type is no longer offered.",
                             code="visit_type_not_available")
    encounter = MaternityEncounter.objects.create(
        pregnancy=pregnancy, visit_type=visit_type, visit=visit,
        provider=provider, summary=summary or "", recorded_by=actor,
    )
    audit_event(actor=actor, action="maternity.encounter_opened", instance=encounter,
                details={"pregnancy": pregnancy.reference, "type": visit_type.code})
    return encounter


def bookable_visit_types():
    """The clinics the hospital currently runs."""
    return MaternityVisitType.objects.filter(is_active=True)


def desk_view_for(patient):
    """
    What the maternity desk needs to know the moment a patient is chosen.

    The answer to section 1 and 2 in one read: is she known, is she pregnant
    right now, what has she been through before, and therefore **which of the
    two buttons should exist**. Reception is never left to work that out from
    memory, and the answer is the server's so the screen cannot invent a third
    possibility.
    """
    active = active_pregnancy_for(patient)
    history = list(pregnancy_history_for(patient))
    return {
        "patient": patient,
        "active_pregnancy": active,
        "history": history,
        # Exactly one of these is true for a known patient, which is what
        # makes the screen unambiguous.
        "can_continue": active is not None,
        "can_start": active is None,
        "has_history": bool(history),
    }


# --------------------------------------------------------------------------
# Phase 2 — labour, delivery, newborns, postpartum.
#
# The same rule runs through all four: **the pregnancy is the spine**. Labour
# belongs to a pregnancy, the delivery belongs to that labour, the babies
# belong to that delivery, and the postpartum checks belong to it too. Nothing
# here creates a patient, a pregnancy or a second copy of anything the
# hospital already records — the admission is `inpatient.Admission`, the
# observations are `clinical.Vitals`, the money is `billing.Charge`.
# --------------------------------------------------------------------------


def clinicians_holding(pregnancy, *, exclude=None):
    """
    Who is actually looking after this woman right now.

    The maternity reading of rule 14: the clinicians who have *seen her in
    this pregnancy* (the providers on its encounters), plus the doctor
    attending her admission if she has one — and never the person who just
    performed the action, because being told about your own delivery is noise.

    Deliberately **not** "every nurse and doctor in the hospital". Maternity
    is worked by two broad roles (rule 56), so broadcasting to the role would
    ring a bell for fifty people about one woman's labour, which is how a bell
    stops being read.
    """
    from apps.accounts.models import User

    people = set()
    for encounter in pregnancy.encounters.all():
        if encounter.provider_id:
            people.add(encounter.provider_id)
    for labour in pregnancy.labour_episodes.select_related("admission"):
        if labour.admission_id and labour.admission.attending_doctor_id:
            people.add(labour.admission.attending_doctor_id)
    people.discard(getattr(exclude, "pk", None))
    return User.objects.filter(pk__in=people, is_active=True)


def _tell_them(pregnancy, *, actor, title, message):
    """One notification per person who is holding her, through the existing bell."""
    for person in clinicians_holding(pregnancy, exclude=actor):
        notify(recipient=person, title=title, message=message,
               category="clinical", action_url="/maternity")


def open_labour(*, pregnancy, actor, started_at=None, onset="spontaneous",
                admission=None, notes=""):
    """
    Open the labour episode for a pregnancy that is still running.

    Refused while one is already open (`labour_already_open`): she cannot be
    in two labours at once, and a second row would leave the delivery with no
    single episode to belong to. The database says the same under a race.

    It does **not** admit her. Admission is the hospital's existing workflow
    and stays that way: `attach_admission` links the two once the ward has a
    bed for her, so the maternity record never becomes a second bed board.
    """
    from django.utils import timezone

    from .models import LabourEpisode

    if not pregnancy.is_active:
        raise MaternityError("That pregnancy has ended.", code="pregnancy_not_active")
    if LabourEpisode.objects.filter(pregnancy=pregnancy, status="in_progress").exists():
        raise MaternityError("A labour is already open for this pregnancy.",
                             code="labour_already_open")

    labour = LabourEpisode.objects.create(
        pregnancy=pregnancy, onset=onset, admission=admission,
        started_at=started_at or timezone.now(), notes=notes or "", opened_by=actor,
    )
    audit_event(actor=actor, action="maternity.labour_opened", instance=labour,
                details={"pregnancy": pregnancy.reference, "onset": onset})
    _tell_them(pregnancy, actor=actor,
               title=f"In labour: {pregnancy.patient.display_name}",
               message=labour.get_onset_display())
    return labour


def attach_admission(*, labour, admission, actor):
    """
    Link the labour to the admission the ward made.

    The bed and the ward are read *through* the admission and never copied, so
    a bed transfer moves the labour record with it without maternity being
    told. A labour that ends without her being admitted keeps `admission`
    null, which is a true statement rather than a gap.
    """
    if admission.patient_id != labour.pregnancy.patient_id:
        raise MaternityError("That admission belongs to a different patient.",
                             code="admission_patient_mismatch")
    labour.admission = admission
    labour.save(update_fields=["admission", "updated_at"])
    audit_event(actor=actor, action="maternity.labour_admitted", instance=labour,
                details={"admission": admission.pk})
    return labour


def record_observation(*, labour, actor, observed_at=None, **readings):
    """
    One check on the partogram — **a new row, always**.

    No earlier observation is read or modified, so the 4 a.m. check still says
    what it said after the 6 a.m. one. Nothing is mandatory (rule 20): a blank
    column means the midwife did not measure it, never zero.
    """
    from django.utils import timezone

    from .models import LabourObservation

    if not labour.is_open:
        raise MaternityError("That labour is closed.", code="labour_not_open")
    fields = {key: value for key, value in readings.items() if value not in (None, "")}
    observation = LabourObservation.objects.create(
        labour=labour, observed_at=observed_at or timezone.now(),
        recorded_by=actor, **fields)
    if fields.get("cervical_dilation_cm") is not None:
        _advance_stage(labour, fields["cervical_dilation_cm"])
    return observation


def _advance_stage(labour, dilation):
    """
    Move the labour's stage to match the dilation just recorded.

    Latent below 4 cm, active from 4, second stage at full dilation — the
    conventional reading, applied only forwards: a stage never goes back
    because one reading was lower than the last, which is measurement noise
    rather than labour reversing.
    """
    order = [stage for stage, _ in labour.STAGE]
    wanted = "second" if dilation >= 10 else ("active" if dilation >= 4 else "latent")
    if order.index(wanted) > order.index(labour.stage):
        labour.stage = wanted
        labour.save(update_fields=["stage", "updated_at"])


def record_delivery(*, labour, actor, delivered_at=None, delivery_type,
                    newborns=(), **details):
    """
    Close the labour with the delivery it ended in, and the baby or babies
    that arrived.

    **Twins are two `Newborn` rows against one delivery.** Not two deliveries,
    and above all not two pregnancies — that is the single thing this function
    exists to make impossible, and the `OneToOneField` behind it means the
    database agrees.

    One transaction: the delivery, every baby and the labour's closure land
    together or not at all, so there is no state where a birth is recorded
    against a labour that still reads as in progress.
    """
    from django.utils import timezone

    from .models import Delivery, MaternityOption, Newborn

    if not labour.is_open:
        raise MaternityError("That labour is already closed.", code="labour_not_open")
    if hasattr(labour, "delivery"):
        raise MaternityError("This labour already has a delivery recorded.",
                             code="delivery_already_recorded")
    if delivery_type is None or delivery_type.kind != MaternityOption.DELIVERY_TYPE:
        raise MaternityError("Name the delivery type.", code="delivery_type_required")

    with transaction.atomic():
        delivery = Delivery.objects.create(
            labour=labour, delivered_at=delivered_at or timezone.now(),
            delivery_type=delivery_type, recorded_by=actor,
            **{key: value for key, value in details.items() if key != "complications"},
        )
        if details.get("complications"):
            delivery.complications.set(details["complications"])

        for order, baby in enumerate(newborns or [], start=1):
            Newborn.objects.create(
                delivery=delivery, birth_order=baby.get("birth_order", order),
                recorded_by=actor,
                **{key: value for key, value in baby.items() if key != "birth_order"},
            )

        labour.status = "delivered"
        labour.stage = "third"
        labour.ended_at = delivery.delivered_at
        labour.save(update_fields=["status", "stage", "ended_at", "updated_at"])

    audit_event(actor=actor, action="maternity.delivery_recorded", instance=delivery,
                details={"pregnancy": labour.pregnancy.reference,
                         "type": delivery_type.code,
                         "babies": delivery.newborns.count()})
    babies = delivery.newborns.count()
    _tell_them(labour.pregnancy, actor=actor,
               title=f"Delivered: {labour.pregnancy.patient.display_name}",
               message=f"{delivery_type.name} · {babies} baby" + ("" if babies == 1 else "/babies"))
    return delivery


def add_newborn(*, delivery, actor, **baby):
    """
    A further baby on the same delivery — the second twin, the third triplet.

    `birth_order` defaults to the next one, and is unique within the delivery
    so a set of twins cannot become one row saved twice.
    """
    from .models import Newborn

    last = delivery.newborns.order_by("-birth_order").first()
    order = baby.pop("birth_order", None) or ((last.birth_order + 1) if last else 1)
    newborn = Newborn.objects.create(delivery=delivery, birth_order=order,
                                     recorded_by=actor, **baby)
    audit_event(actor=actor, action="maternity.newborn_recorded", instance=newborn,
                details={"delivery": delivery.reference, "birth_order": order})
    return newborn


def record_postpartum(*, delivery, actor, seen_at=None, **findings):
    """
    A postpartum check — again, a new row each time.

    Her blood pressure is not stored here: `vitals` points at the
    `clinical.Vitals` row taken at the existing triage station, so the
    hospital keeps one set of observations for this woman.
    """
    from django.utils import timezone

    from .models import PostpartumVisit

    visit = PostpartumVisit.objects.create(
        delivery=delivery, seen_at=seen_at or timezone.now(), recorded_by=actor,
        **{key: value for key, value in findings.items() if value not in (None, "")})
    audit_event(actor=actor, action="maternity.postpartum_recorded", instance=visit,
                details={"delivery": delivery.reference})
    return visit


def options_for(kind):
    """The hospital's current vocabulary for one list."""
    from .models import MaternityOption

    return MaternityOption.objects.filter(kind=kind, is_active=True)


# ---------------------------------------------------------------------------
# Who is in Maternity's care, and who is responsible for her.
#
# **The department is what confers visibility; the nurse is only
# responsibility.** Every authorised midwife sees every Maternity patient
# (`patients.access.maternity_patient_q`), assigned or not — a ward cannot
# wait for somebody to be named before the rest of it can work. Naming a
# midwife says who is answerable for her, and narrows nothing.
#
# Both functions below write a `PatientRoute`, which is the hospital's
# existing assignment record. There is no maternity assignment table, no
# second department column on `Patient`, and no second audit system: the
# changes go to `audit_event` beside every other assignment in the HMIS.
# ---------------------------------------------------------------------------

@transaction.atomic
def assign_to_maternity(*, patient, actor, nurse=None, notes=""):
    """
    Put a patient in the Maternity department's care, optionally naming the
    midwife responsible.

    **Idempotent**: a woman already in Maternity is not filed there twice —
    the existing route is returned and, where a nurse is named, reassigned.
    Pressing the button again is how a desk confirms, not how it duplicates.

    It opens a `Visit` only if she has none open, because a route belongs to
    an attendance and inventing a second one would put the same arrival on the
    record twice.
    """
    from apps.departments.models import Department
    from apps.workflow.models import PatientRoute, Visit

    from .access import MATERNITY_DEPARTMENT_CODE, may_be_assigned

    if nurse is not None and not may_be_assigned(nurse):
        raise MaternityError(
            "Only a maternity nurse can be made responsible for a mother.",
            code="not_maternity_staff")

    department = Department.objects.filter(code__iexact=MATERNITY_DEPARTMENT_CODE).first()
    if department is None:
        raise MaternityError("This hospital has no Maternity department.",
                             code="no_maternity_department")

    existing = _live_maternity_route(patient)
    if existing is not None:
        if nurse is not None and existing.assigned_to_id != nurse.pk:
            return assign_nurse(patient=patient, actor=actor, nurse=nurse)
        return existing

    visit = (Visit.objects.filter(patient=patient, status="open")
             .order_by("-created_at").first())
    if visit is None:
        visit = Visit.objects.create(patient=patient, opened_by=actor,
                                     reason=notes or "Maternity")
    route = PatientRoute.objects.create(
        visit=visit, department=department, purpose="maternity",
        assigned_to=nurse, routed_by=actor, notes=notes or "")
    audit_event(actor=actor, action="maternity.department_assigned", instance=route,
                details={"patient": patient.patient_number,
                         "department": department.name,
                         "assigned_to": nurse.get_username() if nurse else None})
    _tell_the_midwife(route, actor)
    return route


@transaction.atomic
def assign_nurse(*, patient, actor, nurse):
    """
    Change who is responsible for a mother already in Maternity. `nurse=None`
    clears it, which leaves her exactly as visible as she was.

    **It never touches the department.** Transferring a patient out of
    Maternity is the existing routing workflow and a decision of its own; a
    handover at shift change must not quietly move her somewhere else.
    """
    from .access import may_be_assigned

    route = _live_maternity_route(patient)
    if route is None:
        raise MaternityError(
            f"{patient.display_name} is not in Maternity's care. "
            "Assign her to the department first.",
            code="not_in_maternity")
    if nurse is not None and not may_be_assigned(nurse):
        raise MaternityError(
            "Only a maternity nurse can be made responsible for a mother.",
            code="not_maternity_staff")

    previous = route.assigned_to
    route.assigned_to = nurse
    route.save(update_fields=["assigned_to"])
    audit_event(actor=actor, action="maternity.nurse_assigned", instance=route,
                details={"patient": patient.patient_number,
                         # Both ends of the change, so the audit row answers
                         # "who had her before?" without reading the next one.
                         "previous_nurse": previous.get_username() if previous else None,
                         "new_nurse": nurse.get_username() if nurse else None,
                         "department": route.department.name})
    if nurse is not None and nurse.pk != actor.pk:
        _tell_the_midwife(route, actor)
    return route


def _live_maternity_route(patient):
    """The open maternity route, which is the one an assignment acts on."""
    from .access import OPEN_ROUTE_STATUSES, maternity_route_for

    route = maternity_route_for(patient)
    return route if route is not None and route.status in OPEN_ROUTE_STATUSES else None


def _tell_the_midwife(route, actor):
    """
    Tell the midwife she has been made responsible — and nobody else.

    Rule 14: never the person who raised it, and never the whole ward. An
    unassigned patient tells nobody, because she is already on every midwife's
    list and a bell for work that is simply *there* is the noise that stops
    bells being read.
    """
    nurse = route.assigned_to
    if nurse is None or not nurse.is_active or nurse.pk == getattr(actor, "pk", None):
        return
    patient = route.visit.patient
    notify(recipient=nurse, title="Assigned to you in Maternity",
           message=f"{patient.display_name} ({patient.patient_number}) is now yours.",
           category="routing", action_url="/maternity")


@transaction.atomic
def assign_doctor(*, patient, actor, doctor):
    """
    Change who is the **responsible doctor** for a mother in Maternity's care.
    `doctor=None` clears it.

    **It is `Visit.attending_doctor`** — the column that has always meant "the
    doctor responsible for this attendance", already read by
    `patients.access.doctor_patient_q`. So no table, no column and no second
    notion of a responsible doctor was added, and the assigned doctor gets the
    patient through the rule that was already there.

    **It is not the access boundary.** Every doctor on Maternity's staff sees
    her either way (`access.in_maternity_team`); this records who is
    answerable. Naming a different doctor tomorrow takes the patient away from
    nobody.

    It touches the department not at all, and the responsible *midwife* not at
    all — three separate decisions, three separate calls (section L).
    """
    from .access import may_be_assigned

    route = _live_maternity_route(patient)
    if route is None:
        raise MaternityError(
            f"{patient.display_name} is not in Maternity's care. "
            "Assign her to the department first.",
            code="not_in_maternity")
    if doctor is not None and not may_be_assigned(doctor, as_doctor=True):
        raise MaternityError(
            "Only a maternity doctor can be made responsible for a mother.",
            code="not_maternity_staff")

    visit = route.visit
    previous = visit.attending_doctor
    visit.attending_doctor = doctor
    visit.save(update_fields=["attending_doctor"])
    audit_event(actor=actor, action="maternity.doctor_assigned", instance=visit,
                details={"patient": patient.patient_number,
                         "previous_doctor": previous.get_username() if previous else None,
                         "new_doctor": doctor.get_username() if doctor else None,
                         "department": route.department.name})
    if doctor is not None and doctor.pk != getattr(actor, "pk", None) and doctor.is_active:
        notify(recipient=doctor, title="Assigned to you in Maternity",
               message=f"{patient.display_name} ({patient.patient_number}) is now yours.",
               category="routing", action_url="/maternity")
    return visit

