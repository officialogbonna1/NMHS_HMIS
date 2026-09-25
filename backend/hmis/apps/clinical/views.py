from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db import models, transaction
from django.utils import timezone

from .models import Vitals, ConsultationNote, ConsultationNoteAmendment, NursingNote
from . import serializers
from .serializers import may_amend
from apps.accounts.permissions import (CLINICIAN_ROLES, ClinicalRecordAccess,
                                       ClinicianOrNurse, IsNurse, NursingStaff)
from apps.patients.access import (doctor_patient_q, doctors_for_patient, may_act_for,
                                  patient_queryset_for)
from . import eye_exam
from . import services as note_services
from apps.core.services import audit_event, notify
from apps.workflow.models import PatientRoute


# The figures worth seeing at a glance on a list of the day's readings —
# the full record is on the patient's chart.
SUMMARY_FIELDS = [
    ("temperature_c", "Temp", "°C"),
    ("heart_rate", "HR", "bpm"),
    ("respiratory_rate", "RR", "/min"),
    ("sao2", "SaO₂", "%"),
]


def _name(user):
    if not user:
        return None
    return user.get_full_name() or user.username


def _readings(vitals):
    out = []
    if vitals.bp_systolic and vitals.bp_diastolic:
        out.append({"label": "BP", "value": f"{vitals.bp_systolic}/{vitals.bp_diastolic}", "unit": "mmHg"})
    out.extend(
        {"label": label, "value": str(getattr(vitals, field)), "unit": unit}
        for field, label, unit in SUMMARY_FIELDS
        if getattr(vitals, field) is not None
    )
    return out


def _tell_the_doctors(vitals, nurse):
    """
    A reading is only useful to whoever is waiting on it. The hand-off tells
    a doctor a patient is coming; this tells them a fresh set of figures has
    landed on a chart they are already holding — a re-check on the ward, or
    a reading taken while the patient sits in their queue.

    Only doctors the patient is actually in front of are told, so nobody is
    notified about a chart they cannot open.
    """
    summary = ", ".join(f"{label} {getattr(vitals, field)}{unit}"
                        for field, label, unit in SUMMARY_FIELDS
                        if getattr(vitals, field) is not None)
    if vitals.bp_systolic and vitals.bp_diastolic:
        summary = f"BP {vitals.bp_systolic}/{vitals.bp_diastolic}mmHg" + (f", {summary}" if summary else "")

    # Clinicians, not only general doctors: the eye doctor holding the patient
    # reads the same chart and is waiting on the same figures.
    for doctor in doctors_for_patient(vitals.patient, roles=CLINICIAN_ROLES):
        if doctor.pk == nurse.pk:
            continue
        notify(
            recipient=doctor,
            title=f"New vitals: {vitals.patient.display_name}",
            message=f"{summary or 'A new reading'} — recorded by {_name(nurse)}.",
            category="clinical",
            action_url=f"/patients/{vitals.patient.uuid}/vitals",
        )


class VitalsViewSet(viewsets.ModelViewSet):
    """
    Nurses (and doctors) can create vitals. Once saved, is_locked=True is
    set automatically by LockedRecordMixin — update() calls from anyone
    but admin then raise PermissionDenied via the model's save().
    """
    queryset = Vitals.objects.all()
    serializer_class = serializers.VitalsSerializer
    permission_classes = [ClinicianOrNurse]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["patient"]

    def get_queryset(self):
        user = self.request.user
        if user.role in {"admin", "hospital_admin"}:
            return Vitals.objects.all()
        if user.role == "nurse":
            # A nurse can review their own readings, not the whole chart
            # (rule 11). Unchanged.
            return Vitals.objects.filter(recorded_by=user)
        # Everyone else reads the vitals of the patients on their own list,
        # whoever took them — so the reading a nurse recorded this morning is
        # on the chart by the time the patient walks in.
        #
        # `patient_queryset_for` rather than `doctor_patient_q`: it is the same
        # set for a doctor **plus** the maternity team's, which is what lets a
        # midwife read back the history of a mother she is holding and a
        # maternity doctor read one taken by a colleague. One rule, the one the
        # chart and the patient list already use.
        return Vitals.objects.filter(patient__in=patient_queryset_for(user)).distinct()

    def get_permissions(self):
        if self.action == "create":
            return [NursingStaff()]
        return [ClinicianOrNurse()]

    def perform_create(self, serializer):
        vitals = serializer.save(recorded_by=self.request.user)
        _tell_the_doctors(vitals, self.request.user)

    @action(detail=False, methods=["get"], url_path="recorded-today")
    def recorded_today(self, request):
        """
        The nurse's own day: who they took vitals for, what they wrote
        alongside it, and — the part they cannot get anywhere else — which
        doctor the patient went on to. A nurse's queue empties as they work,
        so without this there is no way to look back at what they did or to
        answer "who has that patient now?".

        The onward doctor is the first consultation raised for that patient
        after the reading. Vitals carry no visit of their own, so "after this
        reading" is what ties the two together.
        """
        user = request.user
        today = timezone.localdate()
        readings = list(
            self.get_queryset()
            .filter(created_at__date=today)
            .select_related("patient", "recorded_by")
            .order_by("-created_at")
        )
        if not readings:
            return Response([])

        # Two lookups for the whole page rather than two per row.
        notes = {
            note.vitals_id: note
            for note in NursingNote.objects.filter(vitals__in=readings).select_related("nurse")
        }
        patient_ids = {r.patient_id for r in readings}
        # Every consultation for these patients, not only the ones raised
        # after a reading: a re-check taken while the patient is already with
        # a doctor was reading "Not sent to a doctor yet", which is the
        # opposite of the truth — the doctor had them the whole time.
        consultations = PatientRoute.objects.filter(
            purpose="consultation", visit__patient__in=patient_ids,
        ).select_related("assigned_to", "visit").order_by("created_at")

        by_patient = {}
        for route in consultations:
            by_patient.setdefault(route.visit.patient_id, []).append(route)

        payload = []
        for reading in readings:
            note = notes.get(reading.id)
            for_patient = by_patient.get(reading.patient_id, [])
            # The hand-off this reading led to…
            sent = next((r for r in for_patient if r.created_at >= reading.created_at), None)
            # …or, failing that, whoever was already holding the patient when
            # it was taken.
            holding = None
            if not sent:
                holding = next(
                    (r for r in reversed(for_patient)
                     if r.created_at <= reading.created_at and r.status != "cancelled"),
                    None,
                )
            payload.append({
                "id": reading.id,
                "visit_time": reading.visit_time,
                "created_at": reading.created_at,
                "recorded_by": _name(reading.recorded_by),
                "patient_id": reading.patient_id,
                # What the station links to; `patient_id` stays for the
                # `?patient=` filters the tabs still use.
                "patient_uuid": str(reading.patient.uuid),
                "patient_name": reading.patient.display_name,
                "patient_number": reading.patient.patient_number,
                "patient_file_number": reading.patient.patient_number,
                "readings": _readings(reading),
                "note": None if not note else {
                    "id": note.id, "complaint": note.complaint, "observation": note.observation,
                },
                "sent_to": None if not sent else {
                    "route_id": sent.id,
                    "doctor": _name(sent.assigned_to),
                    "status": sent.status,
                    "status_label": sent.get_status_display(),
                    "created_at": sent.created_at,
                },
                # Whoever already had the patient when this was taken. The
                # station shows it as "Already with Dr X" so a re-check does
                # not read as work nobody has picked up.
                "with_doctor": None if not holding else {
                    "route_id": holding.id,
                    "doctor": _name(holding.assigned_to),
                    "status": holding.status,
                    "status_label": holding.get_status_display(),
                },
            })
        return Response(payload)

    def perform_update(self, serializer):
        try:
            serializer.save(admin_override=self.request.user.is_admin)
        except DjangoPermissionDenied as exc:
            raise PermissionDenied(str(exc))


class NursingNoteViewSet(viewsets.ModelViewSet):
    """
    Written by nurses at the vitals station; read by the doctor the patient
    is assigned to. Locks on save like Vitals, so there is no edit path —
    a follow-up observation is a new note.
    """
    queryset = NursingNote.objects.select_related("patient", "nurse")
    serializer_class = serializers.NursingNoteSerializer
    permission_classes = [ClinicianOrNurse]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["patient", "nurse"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        base = NursingNote.objects.select_related("patient", "nurse")
        if user.role in {"admin", "hospital_admin"}:
            return base
        if user.role == "nurse":
            return base.filter(nurse=user)
        return base.filter(patient__in=patient_queryset_for(user)).distinct()

    def get_permissions(self):
        if self.action == "create":
            return [NursingStaff()]
        return [ClinicianOrNurse()]

    def perform_create(self, serializer):
        serializer.save(nurse=self.request.user)


class ConsultationNoteViewSet(viewsets.ModelViewSet):
    """
    Any doctor can view any note (per the manual: 'you are able to view
    the notes written by other physicians'), but editing is restricted to
    the authoring doctor before it locks, or admin afterward.
    """
    queryset = ConsultationNote.objects.select_related("patient", "doctor").prefetch_related(
        "amendments__amended_by")
    serializer_class = serializers.ConsultationNoteSerializer
    permission_classes = [ClinicalRecordAccess]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["patient", "doctor"]

    def get_queryset(self):
        user = self.request.user
        base = ConsultationNote.objects.select_related("patient", "doctor").prefetch_related(
            "amendments__amended_by")
        if user.role in {"admin", "hospital_admin"}:
            return base
        return base.filter(
            models.Q(doctor=user) | doctor_patient_q(user, "patient")
        ).distinct()

    def get_serializer_context(self):
        # `can_amend` is per-caller, so the serializer needs the request.
        return {**super().get_serializer_context(), "request": self.request}

    def perform_create(self, serializer):
        if not may_act_for(self.request.user, serializer.validated_data["patient"]):
            raise PermissionDenied("This patient is not one of yours.")
        note = serializer.save(doctor=self.request.user)
        # The clinical record's own audit row, through the shared service —
        # the same one Django admin writes (`clinical/services.py`).
        note_services.audit_created(note=note, actor=self.request.user, request=self.request)

    @action(detail=False, methods=["get"], url_path="eye-examination-fields")
    def eye_examination_fields(self, request):
        """What an eye examination may hold — the note form draws itself from this."""
        return Response(eye_exam.schema())

    def update(self, request, *args, **kwargs):
        """
        Ownership is settled **before the body is validated**.

        DRF validates the payload and only then reaches `perform_update`, so a
        field-level 400 was answering first: a doctor amending a colleague's
        eye note was told "only the eye doctor records an eye examination"
        rather than "this is not your note", which is both the wrong reason and
        one that says something about a record the caller has no claim on.
        """
        if not may_amend(request.user, self.get_object()):
            raise PermissionDenied("You can only amend your own notes.")
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        """
        Correcting a saved note — an **amendment**, never an in-place edit.

        The note locks on its first save (`LockedRecordMixin`), and that stays
        true: nothing here writes over clinical history without a trace. What
        this does is let the note's **own author** correct it the way an admin
        already could, through the same archive — a snapshot of every field the
        note carries is written first, stamped with who is amending it and why,
        and only then is the note itself saved.

        So the record keeps both halves: the note says what is true now, and
        the trail says what it said before and who changed it. A colleague's
        note is still readable and still unamendable (`may_amend`), `Vitals`
        and `NursingNote` are untouched and remain absolutely locked, and a
        correction with no reason is refused.
        """
        note = self.get_object()
        user = self.request.user
        # `update` above has already settled this; repeated here because this
        # is the method that writes the amendment, and it must not become
        # reachable without the check if the call path ever changes.
        if not may_amend(user, note):
            raise PermissionDenied("You can only amend your own notes.")

        amending = note.is_locked
        reason = (self.request.data.get("amendment_reason") or "").strip()
        detail = (self.request.data.get("amendment_detail") or "").strip()
        if amending and not note_services.is_valid_reason(reason):
            # Named by `code` so the form can recognise it, the way the
            # laboratory's released-result amendment already does (rule 22).
            raise ValidationError({
                "code": note_services.REASON_REQUIRED,
                "amendment_reason": "Say why this note is being amended.",
                "choices": note_services.reason_choices(),
            })

        # The snapshot and the edit are one act: a refused save must not leave
        # an archive row describing an amendment that never happened.
        try:
            with transaction.atomic():
                if amending:
                    note_services.archive(note=note, actor=user, reason=reason, detail=detail)
                serializer.save(admin_override=amending)
        except DjangoPermissionDenied as exc:
            raise PermissionDenied(str(exc))

        if amending:
            note_services.audit_amended(note=note, actor=user, reason=reason, detail=detail,
                                        request=self.request)


class ConsultationNoteAmendmentViewSet(viewsets.ReadOnlyModelViewSet):
    """Admin-only amendment trail — read-only via API; created server-side
    whenever an admin overrides a lock (wire this into the amend action)."""
    queryset = ConsultationNoteAmendment.objects.all()
    serializer_class = serializers.ConsultationNoteAmendmentSerializer
    permission_classes = [ClinicalRecordAccess]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["note"]

    def get_queryset(self):
        user = self.request.user
        if user.role in {"admin", "hospital_admin"}:
            return ConsultationNoteAmendment.objects.all()
        return ConsultationNoteAmendment.objects.filter(note__doctor=user)
