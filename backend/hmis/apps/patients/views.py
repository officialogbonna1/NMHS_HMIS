
import uuid as uuid_module

from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from . import models, serializers
from apps.accounts.permissions import (
    PATIENT_LOOKUP_ROLES, ClinicalRecordAccess, IsReception, IsSuperAdmin, RoleRequired,
)
from apps.core.config import describe, references_to
from apps.core import notifications_email as email_events
from apps.core.services import audit_event
from .access import doctor_patient_q, patient_queryset_for
from .overview import build_overview

#: What makes a patient part of the hospital's permanent record.
#:
#: Every one of these is a `PROTECT` foreign key, so the database would refuse
#: the delete anyway — this list is what turns that refusal into an answer a
#: person can act on, the same way `ProtectedConfigMixin` does for
#: configuration (rule 31). Financial and audit history is never cascaded away
#: to make a delete succeed.
#:
#: What is *not* here cascades with the patient, and is meant to: the ledger
#: row, vitals, consultation and nursing notes, the nine health-record tiles,
#: appointments and prescriptions. None of those outlives the person they
#: describe. `sales.Sale.patient` is SET_NULL and the sale survives.
PROTECTED_HISTORY = (
    "charges",              # every bill ever raised
    "payments",             # every payment ever taken
    "adjustments",          # discounts, waivers, refunds
    "refunds",              # money handed back
    "deferrals",            # pay-later authorisations
    "visits",               # and, through them, every route and hand-off
    "lab_orders",           # laboratory orders and their results
    "admissions",           # ward stays
    "investigation_orders",
)


class PatientViewSet(viewsets.ModelViewSet):
    """
    Alphabetical patient listing with name search, mirroring the
    reference manual's Patients tile behavior.
    """
    queryset = models.Patient.objects.all()
    serializer_class = serializers.PatientSerializer
    permission_classes = [RoleRequired]
    filter_backends = [SearchFilter]
    # A patient is looked up by whichever of the three the desk has to hand:
    # the number on the card (`NMHS-P000001`), the name, or the phone number
    # somebody just read out.
    search_fields = ["first_name", "last_name", "patient_number", "phone_number"]

    def get_queryset(self):
        return patient_queryset_for(self.request.user)

    def get_object(self):
        """
        A patient is addressed by their UUID — the technical identity, which
        tells a reader nothing and cannot be walked by incrementing it — or by
        the legacy integer pk, which every nested `?patient=` filter and every
        existing client still holds.

        Either way the row comes out of `get_queryset()`, so it is still the
        role's own assignment filter that decides whether this caller may see
        this patient. Neither identifier is permission to read a chart.
        """
        queryset = self.filter_queryset(self.get_queryset())
        value = str(self.kwargs[self.lookup_url_kwarg or self.lookup_field])
        try:
            lookup = {"uuid": uuid_module.UUID(value)}
        except ValueError:
            # Not a UUID, so it can only be the numeric pk. Anything else is a
            # 404 rather than the 500 a non-integer pk lookup would raise.
            if not value.isdigit():
                raise Http404
            lookup = {"pk": int(value)}
        obj = get_object_or_404(queryset, **lookup)
        self.check_object_permissions(self.request, obj)
        return obj

    def get_permissions(self):
        if self.action == "destroy":
            # Permanently deleting a patient is the one irreversible action in
            # the application, and it was reachable by reception and by any
            # administrator until now. It is the Super Admin's alone —
            # `IsSuperAdmin`, not `IsAdmin`, so an ordinary `hospital_admin`
            # is refused like everybody else.
            return [IsSuperAdmin()]
        if self.action in {"create", "update", "partial_update"}:
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
        patient = serializer.save(created_by=self.request.user)
        # The administrator is told, by email, once the registration has
        # actually landed. `dispatch_admin_email` queues on commit and cannot
        # raise (`core/email.py`), so Resend being unreachable leaves the
        # patient registered and writes a log line — the registration is the
        # record, the email is a courtesy. Keyed on the hospital number, so a
        # re-submitted form cannot produce a second one.
        email_events.patient_registered(patient, registered_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """
        `DELETE /api/patients/<uuid>/` — permanently remove a patient, Super
        Admin only, and only where nothing in the hospital's record points at
        them.

        Three gates, in order:

        1. **Who.** `IsSuperAdmin` above. An ordinary administrator, reception,
           the cash desk and every clinical role are refused here regardless of
           what the React app chooses to show.
        2. **Deliberateness.** The request body must carry the patient's own
           hospital number in `confirm` — `NMHS-P000001`, typed out. A DELETE
           fired at the wrong URL cannot succeed by accident, and the number
           has to be read off the record in front of you.
        3. **History.** Anything in `PROTECTED_HISTORY` and the answer is 409
           with the counts, naming what stands in the way. Money, visits,
           laboratory orders and ward stays are the hospital's record, not the
           patient's property, and they are never deleted to let a delete
           through — the database's own `PROTECT` says the same thing, one
           layer down.

        What *is* removed, when all three pass, is everything that cascades:
        the ledger row, vitals, notes, the nine health-record tiles,
        appointments and prescriptions. A patient with any of those but none of
        the protected history is a registration mistake, which is exactly the
        case this endpoint is for.

        The audit row is written **before** the delete and keeps the identity
        in its `details`, because `AuditLog.object_id` points at a row that is
        about to stop existing. Deleting a patient is itself part of the
        record.
        """
        patient = self.get_object()
        confirmation = str(request.data.get("confirm")
                           or request.data.get("patient_number") or "").strip()
        if confirmation.upper() != (patient.patient_number or "").upper():
            return Response(
                {"detail": "Type the patient's hospital number to confirm this deletion.",
                 "code": "confirmation_required",
                 "expected_field": "confirm"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        blocking = references_to(patient, PROTECTED_HISTORY)
        if blocking:
            return Response(
                {"detail": f"This patient has {describe(blocking)} on record, which the "
                           f"hospital keeps. Nothing was deleted.",
                 "code": "history_exists",
                 "references": blocking},
                status=status.HTTP_409_CONFLICT,
            )

        audit_event(
            actor=request.user, action="patients.deleted", request=request,
            details={"patient_number": patient.patient_number, "uuid": str(patient.uuid),
                     "name": patient.display_name, "sex": patient.sex,
                     "registered_at": patient.created_at.isoformat()},
        )
        patient.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

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
