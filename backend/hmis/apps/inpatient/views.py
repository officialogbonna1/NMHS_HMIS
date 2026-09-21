"""
The ward: the bed board, who is in which bed, and the moves between.

Every endpoint here used to be `IsAuthenticated`, which meant a cashier or a
laboratory scientist could list every inpatient, admit somebody, move them
between beds and discharge them. The ward is worked by WARD_ROLES; wards and
beds themselves are back-office configuration, so they are readable by the
ward and writable only by an admin.
"""
from django.db import models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import SearchFilter
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from apps.accounts.permissions import (IsAdmin, OWN_PATIENT_WARD_ROLES, ReadOnlyForRoles,
                                       RoleRequired, WARD_ROLES)
from apps.core.config import ProtectedConfigMixin
from apps.core.services import audit_event
from apps.patients.access import holds_patient, patient_queryset_for
from .models import Ward, Bed, Admission, BedTransfer, DischargeSummary
from .services import AlreadyDischarged, discharge_patient
from .serializers import WardSerializer, BedSerializer, AdmissionSerializer, BedTransferSerializer, DischargeSummarySerializer

# The whole ward's workers, plus the eye doctor for their own patients. Every
# queryset and write below narrows OWN_PATIENT_WARD_ROLES to the patients
# `patient_queryset_for` gives them; WARD_ROLES keep the whole ward, unchanged.
BED_BOARD_ROLES = [*WARD_ROLES, *OWN_PATIENT_WARD_ROLES]


def _own_patients_only(user):
    return getattr(user, "role", None) in OWN_PATIENT_WARD_ROLES


def _scoped(user, queryset, patient_lookup):
    if not _own_patients_only(user):
        return queryset
    return queryset.filter(**{f"{patient_lookup}__in": patient_queryset_for(user)})


def _require_own_patient(user, patient):
    if _own_patients_only(user) and not holds_patient(user, patient):
        raise PermissionDenied("This patient is not one of yours.")


class WardViewSet(ProtectedConfigMixin, viewsets.ModelViewSet):
    """
    Wards: read by the ward, configured by an admin (`ReadOnlyForRoles`), and
    deactivated rather than deleted once beds hang off them.
    """
    queryset = Ward.objects.select_related("department")
    serializer_class = WardSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["is_active", "department"]
    protected_relations = ("beds",)

    def get_permissions(self): return [ReadOnlyForRoles(BED_BOARD_ROLES)]


class BedViewSet(ProtectedConfigMixin, viewsets.ModelViewSet):
    """
    Beds. A bed somebody has been admitted to is part of the record — the
    stay says which bed — so it is deactivated, never deleted; an empty bed
    added by mistake can go.
    """
    queryset = Bed.objects.select_related("ward")
    serializer_class = BedSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["ward", "is_active"]
    protected_relations = ("admissions", "transfers_out", "transfers_in")

    def get_permissions(self): return [ReadOnlyForRoles(BED_BOARD_ROLES)]


class AdmissionViewSet(viewsets.ModelViewSet):
    # `discharge_summary` joins because the serializer reports whether this
    # admission has been closed and under what reference — one join rather
    # than a query per row on a full ward.
    queryset = Admission.objects.select_related(
        "patient", "bed", "bed__ward", "discharge_summary", "attending_doctor", "admitted_by")
    serializer_class = AdmissionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["patient", "bed", "status"]

    def get_permissions(self): return [RoleRequired(BED_BOARD_ROLES)]

    def get_queryset(self):
        return _scoped(self.request.user, super().get_queryset(), "patient")

    @transaction.atomic
    def perform_create(self, serializer):
        _require_own_patient(self.request.user, serializer.validated_data["patient"])
        bed=Bed.objects.select_for_update().get(pk=serializer.validated_data["bed"].pk)
        if not bed.is_active or bed.admissions.filter(status="admitted").exists(): raise ValidationError({"bed":"This bed is unavailable."})
        admission=serializer.save(admitted_by=self.request.user); audit_event(actor=self.request.user,action="admission.created",instance=admission,request=self.request)


class BedTransferViewSet(viewsets.ModelViewSet):
    queryset = BedTransfer.objects.select_related(
        "admission", "from_bed", "from_bed__ward", "to_bed", "to_bed__ward", "transferred_by")
    serializer_class = BedTransferSerializer
    # A move history is read one admission at a time — the detail view asks
    # for that admission's moves rather than pulling the ward's and filtering
    # in the browser. Permissions are unchanged: `RoleRequired(BED_BOARD_ROLES)`
    # below still decides who sees any of it, and `_scoped` still narrows the
    # eye doctor to their own patients.
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["admission", "admission__patient", "from_bed", "to_bed"]
    ordering = ["created_at"]

    def get_permissions(self): return [RoleRequired(BED_BOARD_ROLES)]

    def get_queryset(self):
        return _scoped(self.request.user, super().get_queryset(), "admission__patient")

    @transaction.atomic
    def perform_create(self, serializer):
        _require_own_patient(self.request.user, serializer.validated_data["admission"].patient)
        admission=Admission.objects.select_for_update().get(pk=serializer.validated_data["admission"].pk); target=Bed.objects.select_for_update().get(pk=serializer.validated_data["to_bed"].pk)
        if admission.status != "admitted": raise ValidationError({"admission":"Only active admissions can be transferred."})
        if target.admissions.filter(status="admitted").exists(): raise ValidationError({"to_bed":"This bed is occupied."})
        transfer=serializer.save(from_bed=admission.bed,transferred_by=self.request.user); admission.bed=target; admission.save(update_fields=["bed"]); audit_event(actor=self.request.user,action="admission.bed_transferred",instance=transfer,request=self.request)


class DischargeSummaryViewSet(viewsets.ModelViewSet):
    """
    The ward's discharge, unchanged in who may use it: `BED_BOARD_ROLES`, with
    the eye doctor narrowed to their own patients (rule 43).

    What changed underneath is that the rule now lives in
    `inpatient/services.discharge_patient` — the same function the Admin
    Discharge workspace calls. One discharge model, one service, two doors.
    The service also settled three things this method used to get wrong: the
    status was checked *after* the record was written, a second discharge came
    back as a 500 out of the `OneToOneField`, and two people pressing at once
    could both pass the check.
    """
    queryset = DischargeSummary.objects.select_related(
        "admission", "admission__patient", "admission__bed", "admission__bed__ward",
        "completed_by")
    serializer_class = DischargeSummarySerializer
    http_method_names = ["get", "post", "head", "options"]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["admission", "admission__patient"]

    def get_permissions(self): return [RoleRequired(BED_BOARD_ROLES)]

    def get_queryset(self):
        return _scoped(self.request.user, super().get_queryset(), "admission__patient")

    def perform_create(self, serializer):
        data = serializer.validated_data
        _require_own_patient(self.request.user, data["admission"].patient)
        record = discharge_patient(
            admission=data["admission"], actor=self.request.user,
            diagnosis=data.get("diagnosis", ""), summary=data.get("summary", ""),
            instructions=data.get("instructions", ""),
            follow_up=data.get("follow_up"), condition=data.get("condition", ""),
        )
        # The serializer answers with the record the service actually wrote.
        serializer.instance = record


class AdminDischargeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    The Admin Discharge workspace: `/api/admin-discharges/`, Super Admin only.

    A second **door**, not a second system. Every write goes through the same
    `inpatient/services.discharge_patient` the ward's endpoint calls, onto the
    same `DischargeSummary` row, with the same audit action and the same email.
    What it adds is the administrator's view of it — every ward, every patient,
    unfiltered by whose patient it is — and the one action the ward endpoint
    has no equivalent of: discharging by naming the admission, with the
    already-discharged case answered rather than crashed.

    `IsAdmin` is the gate: **both** administrators — the Super Admin and the
    ordinary `hospital_admin` — reach this workspace. It was `IsSuperAdmin`,
    the boundary rule 37 draws on permanently deleting a patient; that was
    reconsidered and widened deliberately, because discharging is not
    irreversible in the way a purge is. A discharge is a *record*: it is
    written once, it keeps its reference, its audit row and its letter, and a
    patient readmitted tomorrow is a new admission rather than an edit to this
    one. Running the wards is ordinary hospital administration, which is what
    `hospital_admin` is for.

    A clinical role is still refused — `IsAdmin` is the two administrators and
    nobody else — and the ward's own discharge is untouched: a nurse, a ward
    manager, a doctor and the eye doctor discharge from the bed board exactly
    as before, through `DischargeSummaryViewSet`.
    """
    queryset = DischargeSummary.objects.select_related(
        "admission", "admission__patient", "admission__bed", "admission__bed__ward",
        "completed_by").order_by("-created_at")
    serializer_class = DischargeSummarySerializer
    permission_classes = [IsAdmin]
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["admission", "admission__patient", "admission__status",
                        "admission__bed__ward"]
    # Worked by name, by hospital number, or by either reference off a letter
    # somebody is holding.
    search_fields = ["admission__patient__first_name", "admission__patient__last_name",
                     "admission__patient__patient_number", "reference", "condition",
                     "diagnosis"]

    def get_queryset(self):
        """
        The discharged list, filtered by date in the database.

        `?discharged_from` / `?discharged_to` are days, inclusive, read against
        the admission's own `discharged_at`. Applied to `list` only — never to
        `get_object` — so narrowing the list can never hide a record from the
        detail route or from printing, which is the trap rule 21 describes.
        """
        queryset = super().get_queryset()
        if self.action != "list":
            return queryset
        params = self.request.query_params
        start, end = params.get("discharged_from"), params.get("discharged_to")
        if start:
            queryset = queryset.filter(admission__discharged_at__date__gte=start)
        if end:
            queryset = queryset.filter(admission__discharged_at__date__lte=end)
        return queryset

    @action(detail=False, methods=["get"], url_path="dischargeable")
    def dischargeable(self, request):
        """
        Who is currently on a ward — step 1 and 2 of the workspace: pick a
        patient, then read the admission before discharging it.

        The admission serializer already carries the ward, the bed, the
        attending doctor and the patient's identity, so the screen needs no
        second call to show what it is about to act on.
        """
        admissions = (Admission.objects
                      .filter(status="admitted")
                      .select_related("patient", "bed", "bed__ward", "discharge_summary",
                                      "attending_doctor", "admitted_by")
                      .order_by("admitted_at"))
        ward = (request.query_params.get("ward") or "").strip()
        if ward:
            admissions = admissions.filter(bed__ward_id=ward)
        search = (request.query_params.get("search") or "").strip()
        if search:
            matches = (models.Q(patient__first_name__icontains=search)
                       | models.Q(patient__last_name__icontains=search)
                       | models.Q(patient__patient_number__icontains=search)
                       | models.Q(bed__ward__name__icontains=search))
            # "ADM-000123", "adm 123" or plain "123" — the reference is derived
            # from the primary key (`AdmissionSerializer.reference`), so it is
            # matched by reading the number back out rather than by storing a
            # column to search. Anything with no digits in it simply misses.
            digits = "".join(character for character in search if character.isdigit())
            if digits:
                try:
                    matches |= models.Q(pk=int(digits))
                except (TypeError, ValueError):
                    pass
            admissions = admissions.filter(matches)
        page = self.paginate_queryset(admissions)
        serializer = AdmissionSerializer(page if page is not None else admissions,
                                         many=True, context=self.get_serializer_context())
        return (self.get_paginated_response(serializer.data)
                if page is not None else Response(serializer.data))

    @action(detail=False, methods=["post"], url_path="discharge")
    def discharge(self, request):
        """
        Steps 3–7: file the discharge.

        Answers 409 `already_discharged` with the existing record rather than
        writing a second one or raising an `IntegrityError` — so the screen can
        show the discharge that is already there and offer its letter (rule 9).
        """
        admission_id = request.data.get("admission")
        admission = get_object_or_404(Admission, pk=admission_id) if admission_id else None
        if admission is None:
            raise ValidationError({"admission": "Which admission is being discharged?"})

        try:
            record = discharge_patient(
                admission=admission, actor=request.user,
                diagnosis=request.data.get("diagnosis", ""),
                summary=request.data.get("summary", ""),
                instructions=request.data.get("instructions", ""),
                follow_up=request.data.get("follow_up") or None,
                condition=request.data.get("condition", ""),
            )
        except AlreadyDischarged as exc:
            existing = exc.summary
            return Response(
                {"code": "already_discharged",
                 "detail": exc.messages[0] if exc.messages else str(exc),
                 "discharge": (self.get_serializer(existing).data if existing else None)},
                status=status.HTTP_409_CONFLICT)

        return Response(self.get_serializer(record).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="letter")
    def letter(self, request, pk=None):
        """
        Step 8: everything the printed discharge letter puts on paper, in one
        payload.

        Assembled here rather than in the browser so the letter cannot disagree
        with the record, and so the sheet needs no second call for the patient's
        demographics. Only fields the data model actually holds — nothing
        clinical is invented to fill the page out.
        """
        record = self.get_object()
        admission, patient = record.admission, record.admission.patient
        return Response({
            "reference": record.reference,
            "patient": {
                "name": patient.display_name,
                "patient_number": patient.patient_number,
                "uuid": str(patient.uuid),
                "sex": patient.get_sex_display(),
                "age": patient.age_display,
                "phone": patient.phone_number or "",
                "address": patient.street_address or "",
            },
            "admission": {
                "reference": f"ADM-{admission.pk:06d}",
                "ward": getattr(getattr(admission.bed, "ward", None), "name", ""),
                "bed": getattr(admission.bed, "number", ""),
                "admitted_at": admission.admitted_at,
                "discharged_at": admission.discharged_at,
                "status": admission.status,
                "admission_diagnosis": admission.diagnosis or "",
                "attending_doctor": _name_of(admission.attending_doctor),
                "admitted_by": _name_of(admission.admitted_by),
            },
            "discharge": {
                "diagnosis": record.diagnosis,
                "summary": record.summary,
                "instructions": record.instructions,
                "follow_up": record.follow_up,
                "condition": record.condition,
                "completed_by": _name_of(record.completed_by),
                "completed_by_number": getattr(record.completed_by, "staff_number", "") or "",
                "completed_at": record.created_at,
            },
        })


def _name_of(user):
    if not user:
        return ""
    return user.get_full_name() or user.username
