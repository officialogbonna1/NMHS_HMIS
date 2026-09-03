from rest_framework import viewsets, permissions
from apps.accounts.permissions import IsAdmin
from .models import Department, Service
from .serializers import DepartmentSerializer, ServiceSerializer
class AdminWriteViewSet(viewsets.ModelViewSet):
    def get_permissions(self):
        return [permissions.IsAuthenticated(), IsAdmin()] if self.action not in ("list", "retrieve") else [permissions.IsAuthenticated()]
class DepartmentViewSet(AdminWriteViewSet):
    queryset = Department.objects.prefetch_related("staff").all(); serializer_class = DepartmentSerializer
class ServiceViewSet(AdminWriteViewSet):
    queryset = Service.objects.select_related("department").all(); serializer_class = ServiceSerializer
