from rest_framework import viewsets, permissions, status as drf_status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import models, transaction
from django.utils import timezone
from datetime import timedelta
from apps.core.models import HospitalSettings, Notification
from apps.core.models import AuditLog
from apps.patients.models import Patient
from apps.patients.access import patient_queryset_for
from apps.departments.models import Department
from apps.appointments.models import Appointment
from apps.clinical.models import Vitals, NursingNote
from apps.inventory.models import Item, StockRecord
from apps.pharmacy.models import Prescription
from apps.inpatient.models import Admission, Bed
from apps.billing.models import Charge, Payment, PatientLedger, Adjustment
from django_filters.rest_framework import DjangoFilterBackend
from apps.core.services import audit_event, notify
from .models import Visit, PatientRoute
from .serializers import VisitSerializer, PatientRouteSerializer
from apps.accounts.models import User
from apps.accounts.permissions import IsReception, RoleRequired


# What a route is for decides who it is waiting on. Department membership is
# bookkeeping that is easy to leave unset; the purpose is chosen every time a
# patient is routed, so it is the reliable signal.
PURPOSE_ROLE = {
    "vitals": ["nurse"],
    "consultation": ["doctor"],
    "procedure": ["doctor"],
    "laboratory": ["laboratory"],
    "ultrasound": ["radiology"],
    # An eye referral is worked by whichever of the two is on — sending it to
    # one role only strands the patient when that person is off.
    "eye": ["optometrist", "ophthalmologist"],
    "investigation": ["laboratory"],
}
ROLE_PURPOSES = {}
for _purpose, _roles in PURPOSE_ROLE.items():
    for _role in _roles:
        ROLE_PURPOSES.setdefault(_role, []).append(_purpose)

# What a doctor can send a patient on to. Consultation is not here: handing a
# patient to another doctor is the nurse's `send-to-doctor`, and a doctor
# referring sideways would quietly reassign the chart.
REFERRAL_PURPOSES = ["laboratory", "ultrasound", "eye", "procedure"]

# Every role a route can be waiting on. They all need the start/complete
# transitions and a queue page to work from — see main.jsx's /queue guard,
# which has to list the same roles or a referral notification is a dead link.
WORKING_ROLES = sorted({role for roles in PURPOSE_ROLE.values() for role in roles})

# Roles that work from a station of their own rather than the shared queue.
ROLE_STATION_PURPOSE = {
    "laboratory": "laboratory",
    "radiology": "ultrasound",
    "optometrist": "eye",
    "ophthalmologist": "eye",
}
STATION_ROLES = set(ROLE_STATION_PURPOSE)


def _display_name(user):
    return user.get_full_name() or user.username


# Roles that work a shared queue: the whole team sees the list, one person
# claims each patient. Broadcasting unassigned work to all of them is right —
# it is how a lab request reaches whoever is on the bench tonight.
#
# A doctor is not a pool. A patient belongs to *their* doctor, so telling
# every doctor in the hospital about one referral is noise that trains people
# to ignore the bell — see `_doctor_targets`.
POOLED_ROLES = {"nurse", "laboratory", "radiology", "optometrist", "ophthalmologist"}


def _doctor_targets(route):
    """
    Which doctor should hear about doctor-work (a consultation, a procedure).

    Whoever it was assigned to; otherwise the doctors who actually hold this
    patient right now — the same definition the chart uses
    (`patients.access.doctors_for_patient`). If nobody holds them, nobody is
    pinged: the route still sits in `/queue`, which every doctor can see, and
    a queue is the right place for unclaimed work. A notification to all of
    them is not.
    """
    from apps.patients.access import doctors_for_patient
    return list(doctors_for_patient(route.visit.patient))


def route_targets(route):
    """
    Who should hear about this route — and, just as important, who should not.

    Never the person who raised it: telling a doctor about the referral she
    just wrote is pure noise, and it is how a bell full of your own actions
    stops being read.
    """
    raiser_id = route.routed_by_id

    if route.assigned_to_id:
        targets = [route.assigned_to] if route.assigned_to.is_active else []
    else:
        roles = PURPOSE_ROLE.get(route.purpose)
        if roles is None:
            # A purpose with no role of its own ("other"). Department
            # membership is the only signal left.
            targets = list(route.department.staff.filter(is_active=True))
        elif set(roles) & POOLED_ROLES:
            # Unassigned work goes to everyone who can pick it up, not only
            # the staff someone remembered to add to the department.
            targets = list(User.objects.filter(role__in=roles, is_active=True))
        else:
            targets = _doctor_targets(route)

    return [user for user in targets if user.pk != raiser_id]


def _vitals_taken_on(route):
    """
    Has a reading actually been taken for this route? Vitals carry no visit
    of their own, so "since reception raised the route" is what separates
    this attendance from whatever was recorded the last time the patient
    came in — otherwise a returning patient would count as already done.
    """
    return Vitals.objects.filter(patient=route.visit.patient, created_at__gte=route.created_at).exists()


NO_VITALS_WARNING = (
    "No vitals have been recorded for {patient} on this visit. "
    "Send them to the doctor anyway?"
)


def _acknowledged(request, field="acknowledge_no_vitals"):
    """
    The nurse has been shown *this* warning and chosen to go on regardless.
    Each question is acknowledged by name: waving past "no vitals" is not
    consent to move a patient off another doctor's list.
    """
    return request.data.get(field) in (True, "true", "True", "1", 1, "on", "yes")


def _no_vitals_refusal(patient, request):
    """
    Sending a patient on with nothing recorded is a judgement call, not an
    error: a patient can be too distressed for a reading, or the doctor may
    have asked for them straight away. So the nurse is warned once and can
    go ahead — and when they do, the doctor is told what is missing rather
    than opening a chart and finding out.

    Returns a refusal the UI can recognise by `code`, or None to proceed.
    """
    if _acknowledged(request):
        return None
    return Response(
        {"code": "no_vitals", "detail": NO_VITALS_WARNING.format(patient=patient)},
        status=drf_status.HTTP_400_BAD_REQUEST,
    )


def _needs_vitals_first(route):
    if route.purpose == "vitals" and not _vitals_taken_on(route):
        return "Record the patient's vitals before marking this done."
    return None


def _as_id(value):
    """An id off the wire is a string, and an empty select box sends "" —
    which blows up a pk lookup rather than reading as "nothing chosen"."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _queue_consultation(*, visit, doctor, nurse, notes, department, priority, closing, request,
                        without_vitals=False, replacing=None):
    """
    The one act behind both nursing hand-offs: close whatever nursing still
    has open on this visit, queue the consultation in the doctor's name, make
    them attending — that is what opens the chart — and tell them.

    `without_vitals` means the nurse chose to send with nothing recorded. It
    is not hidden: it goes on the route, into the doctor's notification and
    into the audit trail, because the doctor's first question will be where
    the figures are.
    """
    replacing = list(replacing or [])
    flag = "No vitals recorded for this visit." if without_vitals else ""
    if flag:
        notes = f"{flag} {notes}".strip()

    with transaction.atomic():
        for route in closing:
            route.status = "completed"
            route.save(update_fields=["status"])
        # A consultation being handed to somebody else is cancelled, not
        # completed: the first doctor never saw the patient, and a queue that
        # says otherwise is a lie the ward will act on.
        for route in replacing:
            route.status = "cancelled"
            route.save(update_fields=["status"])
        onward = PatientRoute.objects.create(
            visit=visit, department=department, purpose="consultation",
            assigned_to=doctor, priority=priority, notes=notes, routed_by=nurse,
        )
        # Attending is what opens the chart. On a reassignment it has to move
        # with the patient, or the doctor who is off keeps the record open and
        # the one actually seeing them cannot read it.
        if replacing or not visit.attending_doctor_id:
            visit.attending_doctor = doctor
            visit.save(update_fields=["attending_doctor"])

    for route in replacing:
        if route.assigned_to_id and route.assigned_to_id != doctor.pk:
            notify(recipient=route.assigned_to,
                   title=f"Reassigned: {visit.patient}",
                   message=f"{_display_name(nurse)} moved this patient to {_display_name(doctor)}.",
                   category="routing", action_url="/queue")

    audit_event(actor=nurse, action="patient.forwarded_to_doctor", instance=onward,
                details={"doctor": _display_name(doctor), "without_vitals": without_vitals,
                         "reassigned_from": [_display_name(r.assigned_to) for r in replacing if r.assigned_to]},
                request=request)
    notify(recipient=doctor,
           title=f"{'⚠ No vitals — ' if without_vitals else ''}Queued for consultation: {visit.patient}",
           message=notes or f"Sent from Nursing by {_display_name(nurse)} — vitals are on the chart.",
           category="routing", action_url=f"/patients/{visit.patient_id}")
    return onward


# Which health-record test type each unit's work is filed under.
PURPOSE_TEST_TYPE = {
    "laboratory": "labs",
    "ultrasound": "ultrasound",
    "eye": "other",
    "procedure": "other",
    "investigation": "labs",
}


def _file_result_on_the_record(route, author, title="", document=None):
    """
    Put the result in the patient's permanent record, not only on the route.

    A route is one errand: it closes, the visit ends, and next year nobody
    can find the blood work. A MedicalTest sits on the chart's Tests &
    Diagnostics tile with its document attached, which is where a doctor
    looks for a past result.

    Re-saving a result updates the same row rather than filing a second copy
    of the same test.
    """
    from apps.patients.models import MedicalTest

    existing = MedicalTest.objects.filter(
        patient=route.visit.patient, source_route=route,
    ).first()
    name = (title or "").strip() or (route.notes or "").strip()[:200] or route.get_purpose_display()

    if existing:
        existing.title = name
        existing.impressions = route.result
        if document:
            existing.file = document
        existing.save()
        return existing

    return MedicalTest.objects.create(
        patient=route.visit.patient,
        source_route=route,
        title=name,
        test_type=PURPOSE_TEST_TYPE.get(route.purpose, "other"),
        test_date=timezone.localdate(),
        impressions=route.result,
        notes=f"Requested by {_display_name(route.routed_by)}." if route.routed_by else "",
        file=document,
    )


# Which tab on the patient's chart holds each unit's answers. A result
# notification should open on the answer, not on the chart's front page with
# the doctor left to hunt for it. Keep in step with PatientDetail's tabs.
PURPOSE_CHART_TAB = {
    "laboratory": "lab",
    "investigation": "lab",
    "ultrasound": "ultrasound",
    "eye": "eye",
    "procedure": "procedure",
}


def chart_url_for(route):
    tab = PURPOSE_CHART_TAB.get(route.purpose)
    base = f"/patients/{route.visit.patient_id}"
    return f"{base}/{tab}" if tab else base


def _notify_result(route, author):
    """
    Send the finding back to whoever asked for it. A result nobody is told
    about is a patient waiting in a corridor for an answer that is already
    on the system.
    """
    doctor = route.routed_by
    if not doctor or not doctor.is_active or doctor.pk == author.pk:
        return
    summary = route.result[:300] or "The report has been uploaded to the chart."
    notify(recipient=doctor,
           title=f"{route.get_purpose_display()} result: {route.visit.patient}",
           message=f"{summary} — {_display_name(author)}",
           category="clinical", action_url=chart_url_for(route))


# Where each unit actually works. A notification has to land on the page
# that can act on it, not the generic queue — same reason a nurse is sent to
# /vitals. Keep in step with the <Route path=…> guards in main.jsx.
PURPOSE_STATION = {
    "laboratory": "/laboratory",
    "ultrasound": "/ultrasound",
    "eye": "/eye",
    "vitals": "/vitals",
}


def _notify_referral(route, doctor):
    """
    Tell the unit, and say who is asking — a lab request with no name on it
    is one nobody can query.
    """
    station = PURPOSE_STATION.get(route.purpose, "/queue")
    targets = route_targets(route)
    for user in targets:
        notify(recipient=user,
               title=f"{route.get_purpose_display()} requested: {route.visit.patient}",
               message=(route.notes or "No clinical note given.") + f" — Dr. {_display_name(doctor)}",
               category="routing", action_url=station)
    return targets


def _notify_route(route):
    for user in route_targets(route):
        notify(recipient=user, title=f"{route.get_purpose_display()} requested: {route.visit.patient}",
               message=route.notes, category="routing",
               action_url="/vitals" if user.role == "nurse" else f"/patients/{route.visit.patient_id}")


def work_routes_for(user, statuses=("queued", "in_progress")):
    """
    The routes this user works.

    `statuses` narrows it to the live queue, which is what a queue page wants.
    A *document* outlives the queue — the bench reprints the request form for
    a scan it finished this morning — so the printable endpoint passes
    `statuses=None` and gets the same ownership rule without the time limit.
    """
    routes = PatientRoute.objects.all() if statuses is None \
        else PatientRoute.objects.filter(status__in=list(statuses))
    if user.role in {"admin", "hospital_admin"}:
        return routes
    if user.role == "reception":
        # **The front desk sees the work it raised, and nothing else.**
        #
        # Reception's queue used to be every route in the hospital, which put
        # the clinical routing on the front-desk screen: a doctor's
        # laboratory referral reading "Do malaria test", a nurse's hand-off
        # reading "No vitals recorded for this visit". Those notes are the
        # chart — written by a clinician, for a clinician — and the desk has
        # no business in them.
        #
        # What reception legitimately does with this list is call off work it
        # queued (rule 17), so what it needs to see is exactly that. Scoped
        # to the desk rather than to one person: a route raised on the morning
        # shift still has to be cancellable in the afternoon.
        return routes.filter(routed_by__role="reception")
    # Unclaimed work reaches the role the purpose calls for — a vitals
    # request is a nurse's, not every doctor who happens to be listed in that
    # department. Department membership is the fallback only for purposes
    # that map to no particular role.
    unclaimed_for_me = models.Q(assigned_to__isnull=True, purpose__in=ROLE_PURPOSES.get(user.role, []))
    unmapped = models.Q(assigned_to__isnull=True) & ~models.Q(purpose__in=list(PURPOSE_ROLE))
    by_department = unmapped & (
        models.Q(department__staff=user)
        | models.Q(department__name__iexact=user.department)
        | models.Q(department__code__iexact=user.department)
    )
    return routes.filter(
        models.Q(assigned_to=user) | unclaimed_for_me | by_department
    ).distinct()


class VisitViewSet(viewsets.ModelViewSet):
    queryset = Visit.objects.select_related("patient", "attending_doctor"); serializer_class = VisitSerializer; filter_backends = [DjangoFilterBackend]; filterset_fields = ["patient", "status", "visit_type"]

    def get_queryset(self):
        user = self.request.user
        if user.role in {"admin", "hospital_admin", "reception"}:
            return Visit.objects.select_related("patient", "attending_doctor")
        if user.role == "doctor":
            return Visit.objects.filter(models.Q(attending_doctor=user) | models.Q(routes__assigned_to=user)).distinct()
        if user.role == "nurse":
            return Visit.objects.filter(routes__in=work_routes_for(user)).distinct()
        return Visit.objects.none()

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [IsReception()]
        return [RoleRequired(["reception", "doctor", "nurse"])]
    def perform_create(self, serializer):
        visit = serializer.save(opened_by=self.request.user); audit_event(actor=self.request.user, action="visit.opened", instance=visit, request=self.request)
class PatientRouteViewSet(viewsets.ModelViewSet):
    queryset = PatientRoute.objects.select_related("visit__patient", "department", "assigned_to"); serializer_class = PatientRouteSerializer; filter_backends = [DjangoFilterBackend]; filterset_fields = ["visit", "department", "status", "assigned_to"]

    def get_queryset(self):
        if self.action == "document":
            return self._document_queryset()
        return work_routes_for(self.request.user).select_related("visit__patient", "department", "assigned_to")

    def _document_queryset(self):
        """
        Which routes this user may print a form for.

        Wider than the queue in one direction only — time. A request form and
        the report that answers it are documents: the bench reprints the form
        for a scan it finished this morning, and the doctor who raised the
        referral reprints it for the patient's folder. So `status` is not a
        filter here, but *who owns the work* still is.

        A pooled unit (rule 14: laboratory, radiology, the eye clinic, the
        nurses) shares its queue, so it shares its paperwork — any route with
        that unit's purpose. A doctor is not a pool: theirs are the routes
        they raised, were sent, or that belong to a patient they are holding —
        `patients.access.patient_queryset_for`, the same definition of "my
        patient" the chart itself filters by, so a doctor who can open the
        chart can print what is on it and a covering colleague is not locked
        out of a form the doctor who is off raised.
        """
        user = self.request.user
        base = PatientRoute.objects.select_related(
            "visit__patient", "department", "assigned_to", "routed_by", "result_by")
        if user.role in {"admin", "hospital_admin"}:
            return base
        if user.role == "reception":
            # Same boundary as the queue: the desk prints the forms it raised.
            # A doctor's referral form carries the doctor's clinical note, so
            # hiding it from the queue and leaving it printable would only
            # move the leak one URL along.
            return base.filter(routed_by__role="reception")
        mine = (models.Q(assigned_to=user) | models.Q(routed_by=user)
                | models.Q(visit__patient__in=patient_queryset_for(user)))
        if user.role in POOLED_ROLES:
            mine |= models.Q(purpose__in=ROLE_PURPOSES.get(user.role, []))
        return base.filter(mine).distinct()

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [IsReception()]
        if self.action in {"start", "complete", "accept", "record_result"}:
            # Everyone a route can be sent to has to be able to say they have
            # started it and finished it, or referred work sits in a queue
            # nobody can clear.
            return [RoleRequired(WORKING_ROLES)]
        if self.action in {"forward", "send_to_doctor"}:
            return [RoleRequired(["nurse"])]
        if self.action == "refer":
            return [RoleRequired(["doctor"])]
        if self.action == "cancel":
            return [RoleRequired(["reception", "doctor", "nurse"])]
        # Reading the queue: everyone a route can be sent to, or referred
        # work lands somewhere the unit cannot even list.
        return [RoleRequired(["reception"] + WORKING_ROLES)]
    def perform_create(self, serializer):
        route = serializer.save(routed_by=self.request.user); audit_event(actor=self.request.user, action="patient.routed", instance=route, details={"department": route.department.name, "purpose": route.purpose}, request=self.request)
        _notify_route(route)

    def _own_route(self, route):
        """The staff member the work is actually waiting on."""
        user = self.request.user
        if user.is_admin or route.assigned_to_id == user.id:
            return True
        if route.assigned_to_id:
            return False
        if route.purpose in ROLE_PURPOSES.get(user.role, []):
            return True
        if route.purpose in PURPOSE_ROLE:
            return False
        return route.department.staff.filter(pk=user.pk).exists() or route.department.name.lower() == (user.department or "").lower()

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """
        Claim an unassigned patient. Taking the patient is what puts them in
        your name — they drop out of everyone else's queue, so two nurses
        cannot both start on the same person.
        """
        # Looked up outside the queue filter on purpose: the moment another
        # nurse claims it the route leaves this one's queue, and a 404 tells
        # them nothing about why the button stopped working.
        route = PatientRoute.objects.filter(pk=pk).select_related("visit__patient", "assigned_to", "department").first()
        if route is None:
            raise NotFound()
        user = request.user
        eligible = (
            user.is_admin
            or route.assigned_to_id == user.id
            or route.purpose in ROLE_PURPOSES.get(user.role, [])
            or route.department.staff.filter(pk=user.pk).exists()
        )
        if not eligible:
            return Response({"detail": "This patient was not sent to you."}, status=drf_status.HTTP_403_FORBIDDEN)
        if route.status not in ("queued", "in_progress"):
            return Response({"detail": f"This route is already {route.get_status_display().lower()}."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if route.assigned_to_id and route.assigned_to_id != user.id:
            return Response({"detail": f"{_display_name(route.assigned_to)} has already accepted this patient."},
                            status=drf_status.HTTP_409_CONFLICT)

        updated = PatientRoute.objects.filter(
            pk=route.pk, status="queued"
        ).filter(models.Q(assigned_to__isnull=True) | models.Q(assigned_to=user)).update(
            assigned_to=user, status="in_progress",
        )
        if not updated and route.assigned_to_id != user.id:
            return Response({"detail": "Somebody else accepted this patient a moment ago."},
                            status=drf_status.HTTP_409_CONFLICT)

        route.refresh_from_db()
        audit_event(actor=user, action="patient.route_accepted", instance=route, request=request)
        notify(recipient=route.routed_by, title=f"{_display_name(user)} accepted {route.visit.patient}",
               message=route.get_purpose_display(), category="routing", action_url="/queue")
        return Response(self.get_serializer(route).data)

    @action(detail=True, methods=["post"])
    def forward(self, request, pk=None):
        """
        Hand the patient on to a doctor once the vitals are in: this route is
        closed, a consultation route is opened in the doctor's name, and the
        doctor becomes attending — which is what gives them the chart.
        """
        route = self.get_object()
        user = request.user
        if not self._own_route(route):
            return Response({"detail": "This patient is not in your queue."}, status=drf_status.HTTP_403_FORBIDDEN)
        if route.status not in ("queued", "in_progress"):
            return Response({"detail": f"This route is already {route.get_status_display().lower()}."},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        doctor = User.objects.filter(pk=_as_id(request.data.get("doctor")), role="doctor", is_active=True).first()
        if not doctor:
            return Response({"doctor": "Choose a doctor to send this patient to."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        without_vitals = not _vitals_taken_on(route)
        if without_vitals:
            refusal = _no_vitals_refusal(route.visit.patient, request)
            if refusal:
                return refusal

        onward = _queue_consultation(
            visit=route.visit, doctor=doctor, nurse=user, notes=request.data.get("notes", ""),
            department=route.department, priority=route.priority, closing=[route], request=request,
            without_vitals=without_vitals,
        )
        return Response(PatientRouteSerializer(onward).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="send-to-doctor")
    def send_to_doctor(self, request):
        """
        Nursing's hand-off as its own errand, not a button on one queue row:
        pick the patient, pick the doctor. It is the same act as `forward` —
        the doctor is queued, made attending and told — but it still works
        once the nurse has closed their vitals route, which `forward` cannot
        reach. Any vitals route the nurse still has open for that visit is
        closed with it, so the patient is not left sitting in two queues.
        """
        user = request.user
        patient = Patient.objects.filter(pk=_as_id(request.data.get("patient"))).first()
        if not patient:
            return Response({"patient": "Choose the patient you are sending."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        doctor = User.objects.filter(pk=_as_id(request.data.get("doctor")), role="doctor", is_active=True).first()
        if not doctor:
            return Response({"doctor": "Choose a doctor to send this patient to."},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        visit = Visit.objects.filter(patient=patient, status="open").order_by("-created_at").first()
        if not visit:
            return Response({"detail": f"{patient} has no open visit — the front desk opens one when they arrive."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        without_vitals = not Vitals.objects.filter(patient=patient, created_at__gte=visit.created_at).exists()
        if without_vitals:
            refusal = _no_vitals_refusal(patient, request)
            if refusal:
                return refusal

        # Somebody is already holding this patient. Sending them to the same
        # doctor twice is a mistake; sending them to a different one is the
        # answer to "the doctor who saw them is not on seat" — so it is a
        # question, not a refusal.
        waiting = visit.routes.filter(purpose="consultation", status__in=["queued", "in_progress"]).first()
        replacing = []
        if waiting:
            who = _display_name(waiting.assigned_to) if waiting.assigned_to else "a doctor"
            if waiting.assigned_to_id == doctor.pk:
                return Response({"code": "already_queued",
                                 "detail": f"{patient} is already queued for consultation with {who}."},
                                status=drf_status.HTTP_409_CONFLICT)
            if not _acknowledged(request, "acknowledge_reassign"):
                return Response({
                    "code": "reassign",
                    "current_doctor": who,
                    "detail": (f"{patient} is already with {who}. Move them to "
                               f"{_display_name(doctor)} instead?"),
                }, status=drf_status.HTTP_400_BAD_REQUEST)
            replacing = [waiting]

        # The consultation is raised in the department the patient was already
        # being seen in; a visit with no route yet needs one naming it.
        previous = visit.routes.order_by("-created_at").first()
        department = Department.objects.filter(pk=_as_id(request.data.get("department"))).first() or (
            previous.department if previous else None)
        if not department:
            return Response({"department": "Choose the department the doctor is consulting in."},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        mine = visit.routes.filter(purpose="vitals", status__in=["queued", "in_progress"]).filter(
            models.Q(assigned_to=user) | models.Q(assigned_to__isnull=True))
        priority = request.data.get("priority") or (previous.priority if previous else "routine")
        if priority not in dict(PatientRoute._meta.get_field("priority").choices):
            priority = "routine"

        onward = _queue_consultation(
            visit=visit, doctor=doctor, nurse=user, notes=request.data.get("notes", ""),
            department=department, priority=priority, closing=list(mine), request=request,
            without_vitals=without_vitals, replacing=replacing,
        )
        return Response(PatientRouteSerializer(onward).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="refer")
    def refer(self, request):
        """
        The doctor's hand-off, mirroring nursing's: send the patient on to
        the lab, to ultrasound, to the eye clinic or for a procedure.

        The consultation stays open — a referral is work done *during* it,
        not the end of it, and the patient usually comes back with a result.
        Unassigned work reaches every active member of the role the purpose
        calls for, so a referral never strands because nobody was added to a
        Department.
        """
        user = request.user
        patient = Patient.objects.filter(pk=_as_id(request.data.get("patient"))).first()
        if not patient:
            return Response({"patient": "Choose the patient you are referring."},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        purpose = request.data.get("purpose")
        if purpose not in REFERRAL_PURPOSES:
            return Response({"purpose": f"Choose where to send them: {', '.join(REFERRAL_PURPOSES)}."},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        visit = Visit.objects.filter(patient=patient, status="open").order_by("-created_at").first()
        if not visit:
            return Response({"detail": f"{patient} has no open visit — the front desk opens one when they arrive."},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        already = visit.routes.filter(purpose=purpose, status__in=["queued", "in_progress"]).first()
        if already:
            return Response({"code": "already_referred",
                             "detail": f"{patient} is already waiting on {already.get_purpose_display()}."},
                            status=drf_status.HTTP_409_CONFLICT)

        # Named person optional; it has to be somebody who can do the work.
        assigned_to = None
        if request.data.get("assigned_to"):
            assigned_to = User.objects.filter(
                pk=_as_id(request.data.get("assigned_to")),
                role__in=PURPOSE_ROLE[purpose], is_active=True,
            ).first()
            if not assigned_to:
                return Response({"assigned_to": "That member of staff cannot take this work."},
                                status=drf_status.HTTP_400_BAD_REQUEST)

        previous = visit.routes.order_by("-created_at").first()
        department = Department.objects.filter(pk=_as_id(request.data.get("department"))).first() or (
            previous.department if previous else Department.objects.filter(is_active=True).first())
        if not department:
            return Response({"department": "No department to refer into — an admin adds these."},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        priority = request.data.get("priority") or "routine"
        if priority not in dict(PatientRoute._meta.get_field("priority").choices):
            priority = "routine"

        route = PatientRoute.objects.create(
            visit=visit, department=department, purpose=purpose, assigned_to=assigned_to,
            priority=priority, notes=request.data.get("notes", "") or "", routed_by=user,
        )
        audit_event(actor=user, action="patient.referred", instance=route,
                    details={"purpose": purpose, "department": department.name}, request=request)
        told = _notify_referral(route, user)

        data = PatientRouteSerializer(route).data
        # Doctor-work with nobody holding the patient reaches no inbox on
        # purpose — telling every doctor in the hospital about one dressing
        # is how a bell stops being read. But silence must not be a surprise,
        # so the referrer is told the patient is in the shared queue and can
        # name somebody instead.
        data["notified"] = [_display_name(person) for person in told]
        if not told:
            data["notice"] = (
                f"{patient} is in the queue, but nobody has been notified — no doctor is "
                "currently holding them. Name who should do this if it is urgent."
            )
        return Response(data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._transition(status="in_progress", from_statuses={"queued"}, action_name="patient.route_started")

    @action(detail=True, methods=["post"],
            parser_classes=[MultiPartParser, FormParser, JSONParser])
    def complete(self, request, pk=None):
        """
        "Done" is a claim that the work happened, so a vitals route needs a
        reading behind it. Nothing was taken? The way out is `cancel`, which
        says the patient left rather than that they were seen.

        A unit closing referred work can send its finding along in `result`;
        it is stamped with who wrote it and lands on the doctor's chart.
        """
        return self._transition(status="completed", from_statuses={"queued", "in_progress"},
                                action_name="patient.route_completed", guard=_needs_vitals_first,
                                result=request.data.get("result", ""))

    @action(detail=True, methods=["post"], url_path="record-result",
            parser_classes=[MultiPartParser, FormParser, JSONParser])
    def record_result(self, request, pk=None):
        """
        The finished test: what it showed, and the report itself.

        Kept separate from closing, so a unit can file a result before it is
        done with the patient. Multipart, because the result is usually a
        scanned printout or a photo of one as well as typed values.
        """
        return self._save_result(
            self.get_object(),
            request.data.get("result", ""),
            document=request.FILES.get("file"),
            title=request.data.get("title", ""),
        )

    @action(detail=True, methods=["get"])
    def document(self, request, pk=None):
        """
        The referral as a printable document: the request the unit works
        from, and — once it has been written — the finding that answers it.

        One payload, two documents, because they are two halves of the same
        sheet and a form that disagrees with the report it is stapled to is
        worse than no form at all.

        **The finding is not for everybody.** Reception raises and cancels
        routes (rule 17) and so can reach this endpoint, but a scan report is
        clinical: `result` is included only for the unit that wrote it, the
        clinician who asked, and admin. The request half — who, what, how
        urgent — is what the front desk needs and all it gets.
        """
        route = self.get_object()
        patient = route.visit.patient
        user = request.user
        # A finding is clinical, so the front desk never reads one — not even
        # on a route reception raised itself. Reception asking a nurse for
        # vitals does not make the reading ("BP 180/110, referred urgently")
        # the desk's to read back; being the referrer is what earns the answer
        # only when the referrer is a clinician.
        may_read_result = user.role != "reception" and (
            user.is_admin
            or route.routed_by_id == user.pk
            or route.assigned_to_id == user.pk
            or route.result_by_id == user.pk
            or user.role in PURPOSE_ROLE.get(route.purpose, [])
        )
        filed = route.filed_tests.first()
        try:
            file_url = filed.file.url if filed and filed.file else None
        except ValueError:
            file_url = None

        data = {
            "route": {
                "id": route.pk,
                "purpose": route.purpose,
                "purpose_label": route.get_purpose_display(),
                "priority": route.get_priority_display(),
                "status": route.get_status_display(),
                "department": route.department.name,
                "notes": route.notes,
                "created_at": route.created_at,
                "routed_by": _display_name(route.routed_by) if route.routed_by else None,
                "assigned_to": _display_name(route.assigned_to) if route.assigned_to else None,
                "visit": route.visit_id,
                "reference": f"REF-{route.pk:06d}",
            },
            "patient": {
                "id": patient.pk,
                "uuid": str(patient.uuid),
                "name": f"{patient.last_name}, {patient.first_name}",
                "patient_number": patient.patient_number,
                "file_number": patient.patient_number,
                "sex": patient.get_sex_display(),
                "age": patient.age_display,
                "birthdate": patient.birthdate,
                "phone_number": patient.phone_number,
            },
            "result": None,
        }
        if may_read_result and (route.result or filed):
            data["result"] = {
                "text": route.result,
                "title": filed.title if filed else "",
                "recorded_by": _display_name(route.result_by) if route.result_by else None,
                "recorded_at": route.result_at,
                "file_url": file_url,
                "file_name": filed.file.name.rsplit("/", 1)[-1] if file_url else None,
            }
        return Response(data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """
        Calling the visit off — the patient left, or was routed by mistake.
        Reception can do this because it is not a claim that the clinical work
        happened; completing a route is, so that stays with the clinician.
        """
        return self._transition(status="cancelled", from_statuses={"queued", "in_progress"},
                                action_name="patient.route_cancelled", front_desk_allowed=True)

    def _save_result(self, route, text, document=None, title=""):
        user = self.request.user
        if not self._own_route(route):
            return Response({"detail": "This patient is not in your queue."},
                            status=drf_status.HTTP_403_FORBIDDEN)
        if not (text or "").strip() and not document:
            return Response({"result": "Write what the test showed, or attach the report."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        route.result = (text or "").strip()
        route.result_by = user
        route.result_at = timezone.now()
        route.save(update_fields=["result", "result_by", "result_at"])
        # The route is the errand and closes with the visit; the patient's
        # health record is where a result has to live so it is still there
        # next time they come in. `filed_test` is that permanent copy.
        _file_result_on_the_record(route, user, title=title, document=document)
        audit_event(actor=user, action="patient.route_result_recorded", instance=route,
                    request=self.request)
        _notify_result(route, user)
        return Response(self.get_serializer(route).data)

    def _transition(self, *, status, from_statuses, action_name, front_desk_allowed=False,
                    guard=None, result=""):
        route = self.get_object()
        user = self.request.user
        front_desk = front_desk_allowed and user.role in {"reception", "admin", "hospital_admin"}
        if not front_desk and not self._own_route(route):
            return Response({"detail": "This patient is not in your queue."}, status=drf_status.HTTP_403_FORBIDDEN)
        if route.status not in from_statuses:
            return Response({"detail": f"This route is already {route.get_status_display().lower()}."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        refusal = guard(route) if guard else None
        if refusal:
            return Response({"detail": refusal}, status=drf_status.HTTP_400_BAD_REQUEST)
        route.status = status
        fields = ["status"]
        document = self.request.FILES.get("file")
        if (result or "").strip() or document:
            route.result = (result or "").strip()
            route.result_by = user
            route.result_at = timezone.now()
            fields += ["result", "result_by", "result_at"]
        route.save(update_fields=fields)
        audit_event(actor=self.request.user, action=action_name, instance=route, request=self.request)
        if "result" in fields:
            _file_result_on_the_record(route, user, title=self.request.data.get("title", ""),
                                       document=document)
            _notify_result(route, user)
        return Response(self.get_serializer(route).data)


class DashboardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        today = timezone.localdate()
        # Presentation follows the assigned HMIS role. A superuser can still
        # administer the system, but changing that account to Nurse must not
        # keep showing the executive dashboard.
        is_admin_dashboard = user.role in {"admin", "hospital_admin"}
        routes = work_routes_for(user) if not is_admin_dashboard else PatientRoute.objects.filter(status__in=["queued", "in_progress"])

        cards = self._cards_for(user, routes, today)
        task_routes = routes.select_related("visit__patient", "department", "assigned_to")[:8]
        tasks = [{
            "id": route.id,
            "patient": str(route.visit.patient),
            "patient_id": route.visit.patient_id,
            "department": route.department.name,
            "purpose": route.get_purpose_display(),
            "priority": route.priority,
            "status": route.status,
            "assigned_to": _display_name(route.assigned_to) if route.assigned_to else "Unassigned",
            "created_at": route.created_at,
            # Nurses work the queue from their own station; everyone else
            # goes straight to the patient's chart.
            "href": "/vitals" if user.role == "nurse" else f"/patients/{route.visit.patient_id}",
        } for route in task_routes]

        if is_admin_dashboard:
            activity = AuditLog.objects.select_related("actor").all()[:10]
            recent_activity = [{
                "id": event.id, "action": event.action.replace(".", " ").replace("_", " ").title(),
                "actor": event.actor.get_full_name() or event.actor.username if event.actor else "System",
                "created_at": event.created_at,
            } for event in activity]
        else:
            recent_activity = [{"id": note.id, "action": note.title, "actor": "", "created_at": note.created_at}
                               for note in Notification.objects.filter(recipient=user)[:10]]

        return Response({
            "today": today,
            "role": user.get_role_display(),
            "is_admin": is_admin_dashboard,
            "cards": cards,
            "tasks": tasks,
            "recent_activity": recent_activity,
            "alerts": self._alerts_for(user, is_admin_dashboard),
        })

    def _cards_for(self, user, routes, today):
        unread = Notification.objects.filter(recipient=user, is_read=False).count()
        if user.role == "nurse":
            return self._nurse_cards(user, routes, today, unread)
        if user.role in {"cashier", "accountant"}:
            return self._cash_desk_cards(user, today, unread)
        if user.role in {"admin", "hospital_admin"}:
            payments_today = Payment.objects.filter(created_at__date=today).aggregate(total=models.Sum("amount"))["total"] or 0
            outstanding = PatientLedger.objects.aggregate(total=models.Sum(models.F("total_charges") - models.F("total_payments") - models.F("total_adjustments")))["total"] or 0
            return [
                {"label": "Registered patients", "value": Patient.objects.count(), "href": "/patients", "tone": "blue"},
                {"label": "Open visits", "value": Visit.objects.filter(status="open").count(), "href": "/patients", "tone": "violet"},
                {"label": "Active admissions", "value": Admission.objects.filter(status="admitted").count(), "href": "/admissions", "tone": "amber"},
                {"label": "Today’s collections", "value": payments_today, "format": "currency", "href": "/billing", "tone": "green"},
                {"label": "Outstanding balance", "value": outstanding, "format": "currency", "href": "/billing", "tone": "red"},
                {"label": "Unread notifications", "value": unread, "href": "/notifications", "tone": "slate"},
            ]
        role = user.role
        # Each card opens the page it is counting. "My queue" used to land on
        # the patient list, so a doctor with somebody waiting had no way in
        # from here; the queue page is only routable for the roles that have
        # one, so the rest keep the patient list.
        queue_href = PURPOSE_STATION.get(ROLE_STATION_PURPOSE.get(role), "/queue") if role in STATION_ROLES \
            else ("/queue" if role in {"doctor", "nurse", "reception"} else "/patients")
        cards = [
            {"key": "my_patients", "label": "My patients",
             "value": patient_queryset_for(user).count(), "href": "/patients", "tone": "violet"},
            {"key": "my_queue", "label": "My queue", "value": routes.count(), "href": queue_href, "tone": "blue"},
            {"key": "unread", "label": "Unread notifications", "value": unread, "href": "/notifications", "tone": "slate"},
        ]
        # The eye roles are clinicians *and* run a station, so the station
        # branch below is a separate `if` — an elif chain gave them the
        # doctor's cards and never their own.
        if role == "doctor":
            cards.append({"key": "seen_today", "label": "Patients seen today", "value": Visit.objects.filter(attending_doctor=user, created_at__date=today).count(), "href": "/patients", "tone": "green"})
        elif role == "pharmacist":
            cards.append({"label": "Prescriptions awaiting dispensing", "value": Prescription.objects.filter(status="pending").count(), "href": "/pharmacy", "tone": "amber"})
            cards.append({"label": "Dispensed, awaiting payment", "value": Charge.objects.filter(source_type="prescription", status__in=["unpaid", "partial"]).count(), "href": "/pharmacy", "tone": "red"})
        if role in STATION_ROLES:
            # Their own station, counting the referrals actually waiting on
            # them. It used to count InvestigationOrders and link to a page
            # that was never built, so the number was wrong *and* the card
            # was a dead end.
            purpose = ROLE_STATION_PURPOSE[role]
            station = PURPOSE_STATION[purpose]
            waiting = routes.filter(purpose=purpose, status="queued").count()
            in_progress = routes.filter(purpose=purpose, status="in_progress").count()
            cards.append({"key": "referrals_waiting", "label": "Referrals waiting",
                          "value": waiting, "href": station, "tone": "amber"})
            cards.append({"key": "referrals_in_progress", "label": "In progress",
                          "value": in_progress, "href": station, "tone": "violet"})
            cards.append({"key": "results_today", "label": "Results filed today",
                          "value": PatientRoute.objects.filter(result_by=user, result_at__date=today).count(),
                          "href": station, "tone": "green"})
        return cards

    def _cash_desk_cards(self, user, today, unread):
        """
        A cashier's day is money, not a patient queue — routes never reach
        them, so the generic "My queue" card was always zero. These are the
        figures they are asked for at the end of a shift, each opening the
        page that can explain it.
        """
        paid_today = Payment.objects.filter(created_at__date=today)
        adjusted_today = Adjustment.objects.filter(created_at__date=today)

        def total(queryset):
            return queryset.aggregate(total=models.Sum("amount"))["total"] or 0

        outstanding = PatientLedger.objects.aggregate(
            total=models.Sum(models.F("total_charges") - models.F("total_payments") - models.F("total_adjustments"))
        )["total"] or 0

        return [
            {"key": "taken_today", "label": "Taken today", "value": total(paid_today),
             "format": "currency", "href": "/billing", "tone": "green"},
            {"key": "cash_today", "label": "Cash today", "value": total(paid_today.filter(method="cash")),
             "format": "currency", "href": "/billing", "tone": "green"},
            # What the desk collected itself, as against the pharmacy counter
            # — the two reconcile separately (Payment.channel).
            {"key": "my_desk_today", "label": "Taken at this desk",
             "value": total(paid_today.filter(received_by=user)),
             "format": "currency", "href": "/billing", "tone": "blue"},
            {"key": "part_paid", "label": "Part-paid charges",
             "value": Charge.objects.filter(status="partial").count(), "href": "/billing", "tone": "amber"},
            {"key": "unpaid_charges", "label": "Unpaid charges",
             "value": Charge.objects.filter(status__in=["unpaid", "partial"]).count(),
             "href": "/billing", "tone": "red"},
            {"key": "outstanding", "label": "Outstanding balance", "value": outstanding,
             "format": "currency", "href": "/billing", "tone": "red"},
            {"key": "discounted_today", "label": "Discounted today",
             "value": total(adjusted_today.filter(kind="discount")),
             "format": "currency", "href": "/transactions", "tone": "violet"},
            {"key": "waived_today", "label": "Waived today",
             "value": total(adjusted_today.filter(kind="waiver")),
             "format": "currency", "href": "/transactions", "tone": "violet"},
            {"key": "refunded_today", "label": "Refunded today",
             "value": total(adjusted_today.filter(kind="refund")),
             "format": "currency", "href": "/transactions", "tone": "slate"},
            {"key": "unread", "label": "Unread notifications", "value": unread,
             "href": "/notifications", "tone": "slate"},
        ]

    def _nurse_cards(self, user, routes, today, unread):
        """
        A nurse's day is the vitals queue: who is waiting, who they are part
        way through, and what they have already recorded. Every card lands on
        the station they actually work from, not the generic patient list.
        """
        waiting = routes.filter(purpose="vitals", status="queued").count()
        in_progress = routes.filter(status="in_progress").count()
        # `key` is the stable handle other screens read these by — the Vitals
        # station pulls its "today" totals from here rather than counting a
        # paginated list client-side and getting it wrong past page one.
        return [
            {"key": "vitals_waiting", "label": "Waiting for vitals", "value": waiting, "href": "/vitals", "tone": "amber"},
            {"key": "vitals_in_progress", "label": "In progress", "value": in_progress, "href": "/vitals", "tone": "blue"},
            # The two "today" cards look back at work already done, so they
            # open the station's day list rather than the live queue — which
            # by then has emptied of the very patients they are counting.
            {"key": "vitals_today", "label": "Vitals recorded today", "value": Vitals.objects.filter(recorded_by=user, created_at__date=today).count(), "href": "/vitals?tab=today", "tone": "green"},
            {"key": "nursing_notes_today", "label": "Notes written today", "value": NursingNote.objects.filter(nurse=user, created_at__date=today).count(), "href": "/vitals?tab=today", "tone": "violet"},
            {"key": "unread", "label": "Unread notifications", "value": unread, "href": "/notifications", "tone": "slate"},
        ]

    def _alerts_for(self, user, is_admin_dashboard):
        alerts = []
        # The thresholds are configuration, not constants: an admin sets them
        # on Administration → Hospital settings and every alert below follows.
        settings_row = HospitalSettings.load()
        if is_admin_dashboard or user.role in {"pharmacist", "inventory_manager"}:
            # Where a stock alert lands depends on the workspace it is raised
            # in. Administration runs inventory from /inventory; the pharmacy
            # works its own shelf from its own page, and can no longer open
            # the inventory desk at all — so sending a pharmacist there would
            # be a link straight into a refusal.
            stock_href = "/pharmacy?tab=stock" if user.role == "pharmacist" else "/inventory"
            low_stock = sum(1 for item in Item.objects.all() if item.is_low_stock)
            # Counted over stock records, so a lot standing in two locations
            # is two things to walk to — which is what the alert is for.
            expiry_days = settings_row.expiry_warning_days
            expiring = StockRecord.objects.filter(
                quantity__gt=0,
                batch__expiry_date__gte=timezone.localdate(),
                batch__expiry_date__lte=timezone.localdate() + timedelta(days=expiry_days),
            ).count()
            if low_stock:
                alerts.append({"label": f"{low_stock} item(s) are below their reorder threshold",
                               "level": "warning", "href": stock_href})
            if expiring:
                alerts.append({"label": f"{expiring} batch(es) expire within {expiry_days} days",
                               "level": "warning", "href": stock_href})
        if user.role == "nurse":
            wait_minutes = settings_row.vitals_wait_alert_minutes
            waiting_long = work_routes_for(user).filter(
                purpose="vitals", status="queued",
                created_at__lt=timezone.now() - timedelta(minutes=wait_minutes),
            ).count()
            if waiting_long:
                alerts.append({"label": f"{waiting_long} patient(s) have been waiting over {wait_minutes} minutes for vitals",
                               "level": "warning", "href": "/vitals"})
        if user.role in {"cashier", "accountant"}:
            # A bill raised at the front desk is money this desk has to
            # collect, and the patient is usually walking over right now.
            hours = settings_row.unpaid_charge_alert_hours
            waiting = Charge.objects.filter(
                status__in=["unpaid", "partial"],
                created_at__gte=timezone.now() - timedelta(hours=hours),
            ).count()
            if waiting:
                alerts.append({"label": f"{waiting} charge(s) raised in the last {hours} hours are still unpaid",
                               "level": "warning", "href": "/billing"})
        if is_admin_dashboard:
            occupied = Admission.objects.filter(status="admitted").count()
            active_beds = Bed.objects.filter(is_active=True).count()
            alerts.append({"label": f"Bed occupancy: {occupied}/{active_beds}", "level": "info", "href": "/admissions"})
        return alerts
