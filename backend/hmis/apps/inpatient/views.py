"""
The ward: the bed board, who is in which bed, and the moves between.

Every endpoint here used to be `IsAuthenticated`, which meant a cashier or a
laboratory scientist could list every inpatient, admit somebody, move them
between beds and discharge them. The ward is worked by WARD_ROLES; wards and
beds themselves are back-office configuration, so they are readable by the
ward and writable only by an admin.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from apps.accounts.permissions import OWN_PATIENT_WARD_ROLES, ReadOnlyForRoles, RoleRequired, WARD_ROLES
from apps.core.config import ProtectedConfigMixin
from apps.core.services import audit_event
from apps.patients.access import holds_patient, patient_queryset_for
from .models import Ward, Bed, Admission, BedTransfer, DischargeSummary
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
    queryset = Admission.objects.select_related("patient", "bed", "bed__ward")
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
    queryset = BedTransfer.objects.select_related("admission", "from_bed", "to_bed")
    serializer_class = BedTransferSerializer

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
    queryset = DischargeSummary.objects.select_related("admission")
    serializer_class = DischargeSummarySerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self): return [RoleRequired(BED_BOARD_ROLES)]

    def get_queryset(self):
        return _scoped(self.request.user, super().get_queryset(), "admission__patient")

    @transaction.atomic
    def perform_create(self, serializer):
        _require_own_patient(self.request.user, serializer.validated_data["admission"].patient)
        summary=serializer.save(completed_by=self.request.user); admission=summary.admission
        if admission.status != "admitted": raise ValidationError({"admission":"Admission is not active."})
        admission.status="discharged"; admission.discharged_at=timezone.now(); admission.save(update_fields=["status","discharged_at"]); audit_event(actor=self.request.user,action="admission.discharged",instance=summary,request=self.request)
