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
from rest_framework.exceptions import ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from apps.accounts.permissions import ReadOnlyForRoles, WardStaff, WARD_ROLES
from apps.core.services import audit_event
from .models import Ward, Bed, Admission, BedTransfer, DischargeSummary
from .serializers import WardSerializer, BedSerializer, AdmissionSerializer, BedTransferSerializer, DischargeSummarySerializer


class WardViewSet(viewsets.ModelViewSet):
    queryset = Ward.objects.all()
    serializer_class = WardSerializer

    def get_permissions(self): return [ReadOnlyForRoles(WARD_ROLES)]


class BedViewSet(viewsets.ModelViewSet):
    queryset = Bed.objects.select_related("ward")
    serializer_class = BedSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["ward", "is_active"]

    def get_permissions(self): return [ReadOnlyForRoles(WARD_ROLES)]


class AdmissionViewSet(viewsets.ModelViewSet):
    queryset = Admission.objects.select_related("patient", "bed", "bed__ward")
    serializer_class = AdmissionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["patient", "bed", "status"]

    def get_permissions(self): return [WardStaff()]

    @transaction.atomic
    def perform_create(self, serializer):
        bed=Bed.objects.select_for_update().get(pk=serializer.validated_data["bed"].pk)
        if not bed.is_active or bed.admissions.filter(status="admitted").exists(): raise ValidationError({"bed":"This bed is unavailable."})
        admission=serializer.save(admitted_by=self.request.user); audit_event(actor=self.request.user,action="admission.created",instance=admission,request=self.request)


class BedTransferViewSet(viewsets.ModelViewSet):
    queryset = BedTransfer.objects.select_related("admission", "from_bed", "to_bed")
    serializer_class = BedTransferSerializer

    def get_permissions(self): return [WardStaff()]

    @transaction.atomic
    def perform_create(self, serializer):
        admission=Admission.objects.select_for_update().get(pk=serializer.validated_data["admission"].pk); target=Bed.objects.select_for_update().get(pk=serializer.validated_data["to_bed"].pk)
        if admission.status != "admitted": raise ValidationError({"admission":"Only active admissions can be transferred."})
        if target.admissions.filter(status="admitted").exists(): raise ValidationError({"to_bed":"This bed is occupied."})
        transfer=serializer.save(from_bed=admission.bed,transferred_by=self.request.user); admission.bed=target; admission.save(update_fields=["bed"]); audit_event(actor=self.request.user,action="admission.bed_transferred",instance=transfer,request=self.request)


class DischargeSummaryViewSet(viewsets.ModelViewSet):
    queryset = DischargeSummary.objects.select_related("admission")
    serializer_class = DischargeSummarySerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self): return [WardStaff()]

    @transaction.atomic
    def perform_create(self, serializer):
        summary=serializer.save(completed_by=self.request.user); admission=summary.admission
        if admission.status != "admitted": raise ValidationError({"admission":"Admission is not active."})
        admission.status="discharged"; admission.discharged_at=timezone.now(); admission.save(update_fields=["status","discharged_at"]); audit_event(actor=self.request.user,action="admission.discharged",instance=summary,request=self.request)
