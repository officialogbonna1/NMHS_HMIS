from rest_framework import viewsets
from django_filters.rest_framework import DjangoFilterBackend
from apps.billing.services import add_charge
from apps.core.services import audit_event, notify
from .models import InvestigationCatalog, InvestigationOrder, InvestigationResult
from .serializers import InvestigationCatalogSerializer, InvestigationOrderSerializer, InvestigationResultSerializer
from apps.accounts.permissions import IsDoctor, RoleRequired
class InvestigationCatalogViewSet(viewsets.ModelViewSet):
    queryset=InvestigationCatalog.objects.select_related("department"); serializer_class=InvestigationCatalogSerializer
    def get_permissions(self): return [RoleRequired(["doctor", "laboratory", "radiology", "optometrist", "ophthalmologist"])]
class InvestigationOrderViewSet(viewsets.ModelViewSet):
    queryset=InvestigationOrder.objects.select_related("patient","investigation","requested_by"); serializer_class=InvestigationOrderSerializer; filter_backends=[DjangoFilterBackend]; filterset_fields=["patient","visit","status","investigation"]
    def get_permissions(self):
        if self.action == "create": return [IsDoctor()]
        return [RoleRequired(["doctor", "laboratory", "radiology", "optometrist", "ophthalmologist"])]
    def get_queryset(self):
        user=self.request.user
        if user.role in {"admin", "hospital_admin", "laboratory", "radiology", "optometrist", "ophthalmologist"}: return InvestigationOrder.objects.select_related("patient","investigation","requested_by")
        return InvestigationOrder.objects.filter(requested_by=user).select_related("patient","investigation")
    def perform_create(self, serializer):
        order=serializer.save(requested_by=self.request.user); catalog=order.investigation
        if catalog.price: add_charge(patient=order.patient, description=catalog.name, amount=catalog.price, created_by=self.request.user, department=catalog.department, source_type="investigation", source_id=order.id)
        audit_event(actor=self.request.user, action="investigation.requested", instance=order, request=self.request)
class InvestigationResultViewSet(viewsets.ModelViewSet):
    queryset=InvestigationResult.objects.select_related("order__patient","order__requested_by"); serializer_class=InvestigationResultSerializer; http_method_names=["get","post","head","options"]
    def get_permissions(self):
        if self.action == "create": return [RoleRequired(["laboratory", "radiology", "optometrist", "ophthalmologist"])]
        return [RoleRequired(["doctor", "laboratory", "radiology", "optometrist", "ophthalmologist"])]
    def get_queryset(self):
        user=self.request.user
        if user.role in {"admin", "hospital_admin", "laboratory", "radiology", "optometrist", "ophthalmologist"}: return InvestigationResult.objects.select_related("order__patient","order__requested_by")
        return InvestigationResult.objects.filter(order__requested_by=user).select_related("order__patient")
    def perform_create(self, serializer):
        result=serializer.save(released_by=self.request.user); order=result.order; order.status="completed"; order.performed_by=self.request.user; order.save(update_fields=["status","performed_by"])
        notify(recipient=order.requested_by,title=f"Result released: {order.investigation.name}",message=f"Result for {order.patient}",category="diagnostics"); audit_event(actor=self.request.user,action="investigation.result_released",instance=result,request=self.request)
