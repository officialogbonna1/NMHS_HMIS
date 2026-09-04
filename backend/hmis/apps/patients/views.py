
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from . import models, serializers
from apps.accounts.permissions import (
    PATIENT_LOOKUP_ROLES, ClinicalRecordAccess, IsReception, RoleRequired,
)
from .access import doctor_patient_q, patient_queryset_for
from .overview import build_overview


class PatientViewSet(viewsets.ModelViewSet):
    """
    Alphabetical patient listing with name search, mirroring the
    reference manual's Patients tile behavior.
    """
    queryset = models.Patient.objects.all()
    serializer_class = serializers.PatientSerializer
    permission_classes = [RoleRequired]
    filter_backends = [SearchFilter]
    search_fields = ["first_name", "last_name", "file_number"]

    def get_queryset(self):
        return patient_queryset_for(self.request.user)

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [IsReception()]
        if self.action == "overview":
            # The overview is the full chart in one payload — notes, vitals,
            # prescriptions. It is for the clinician treating the patient, not
            # for every role that may look up a name.
            return [ClinicalRecordAccess()]
        # Looking a patient up is not reading their chart: the tiles, the
        # notes and the overview are gated separately and far more tightly.
        return [RoleRequired(PATIENT_LOOKUP_ROLES)]

    def get_serializer_class(self):
        user = self.request.user
        if getattr(user, "role", None) == "reception" and not user.sensitive_record_access:
            return serializers.PatientDemographicsSerializer
        return serializers.PatientSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["get"])
    def overview(self, request, pk=None):
        """
        Everything attached to this patient in one response — the doctor's
        chart. get_object() runs the same assignment filter as the rest of
        the viewset, so an unassigned patient 404s here too.
        """
        return Response(build_overview(patient=self.get_object(), user=request.user))


def _make_health_record_viewset(model, serializer_cls):
    class _ViewSet(viewsets.ModelViewSet):
        queryset = model.objects.all()
        serializer_class = serializer_cls
        permission_classes = [ClinicalRecordAccess]
        filter_backends = [DjangoFilterBackend]
        filterset_fields = ["patient"]

        def get_queryset(self):
            user = self.request.user
            if user.role in {"admin", "hospital_admin"}:
                return model.objects.all()
            return model.objects.filter(doctor_patient_q(user, "patient")).distinct()

    return _ViewSet


AllergyViewSet = _make_health_record_viewset(
    models.Allergy,
    serializers.AllergySerializer
)

MedicationViewSet = _make_health_record_viewset(
    models.Medication,
    serializers.MedicationSerializer
)

MedicalConditionViewSet = _make_health_record_viewset(
    models.MedicalCondition,
    serializers.MedicalConditionSerializer
)

MedicalDeviceViewSet = _make_health_record_viewset(
    models.MedicalDevice,
    serializers.MedicalDeviceSerializer
)

SurgicalHistoryViewSet = _make_health_record_viewset(
    models.SurgicalHistory,
    serializers.SurgicalHistorySerializer
)

FamilyMedicalHistoryViewSet = _make_health_record_viewset(
    models.FamilyMedicalHistory,
    serializers.FamilyMedicalHistorySerializer
)

SocialHistoryViewSet = _make_health_record_viewset(
    models.SocialHistory,
    serializers.SocialHistorySerializer
)

VaccinationViewSet = _make_health_record_viewset(
    models.Vaccination,
    serializers.VaccinationSerializer
)

MedicalTestViewSet = _make_health_record_viewset(
    models.MedicalTest,
    serializers.MedicalTestSerializer
)
