"""
Departments and the services they offer — hospital configuration.

Read by every signed-in user (the whole application quotes them), written by
an admin, and never deleted once the record points at them. The HMIS
administration screens and Django admin edit these same rows.
"""
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets

from apps.accounts.permissions import IsAdmin
from apps.core.config import ProtectedConfigMixin
from apps.core.services import audit_event

from .models import Department, Service
from .serializers import DepartmentSerializer, ServiceSerializer


class AdminWriteViewSet(ProtectedConfigMixin, viewsets.ModelViewSet):
    """Anyone may read the configuration; only an admin may change it."""
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsAdmin()]

    def perform_create(self, serializer):
        instance = serializer.save()
        audit_event(actor=self.request.user, action="config.created", instance=instance,
                    details={"model": instance._meta.label}, request=self.request)

    def perform_update(self, serializer):
        instance = serializer.save()
        audit_event(actor=self.request.user, action="config.updated", instance=instance,
                    details={"model": instance._meta.label}, request=self.request)


class DepartmentViewSet(AdminWriteViewSet):
    queryset = Department.objects.prefetch_related("staff").all()
    serializer_class = DepartmentSerializer
    filterset_fields = ["is_active"]
    search_fields = ["name", "code"]
    # A department that has routed a patient, priced a service, holds a ward
    # or has taken money is part of the record. Deactivate it; the history
    # keeps reading.
    #
    # `charge_set` was the omission that mattered: `Charge.department` used to
    # be SET_NULL and was not listed here, so deleting a department silently
    # wiped the department off every charge it had ever earned. It is PROTECT
    # now as well, so the database refuses too — but the 409 with the counts
    # is what an administrator should actually see.
    protected_relations = ("routes", "services", "lab_tests", "ward_set", "charge_set")


class ServiceViewSet(AdminWriteViewSet):
    queryset = Service.objects.select_related("department").all()
    serializer_class = ServiceSerializer
    filterset_fields = ["department", "is_active"]
    search_fields = ["name", "code"]
