from rest_framework import serializers

from .models import Department, Service


class DepartmentSerializer(serializers.ModelSerializer):
    # Counts, so the administration screen can say what a department holds
    # before somebody tries to delete it.
    service_count = serializers.SerializerMethodField()
    staff_count = serializers.SerializerMethodField()
    manager_name = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = "__all__"

    def get_service_count(self, obj):
        return obj.services.count()

    def get_staff_count(self, obj):
        return obj.staff.count()

    def get_manager_name(self, obj):
        user = obj.manager
        return (user.get_full_name() or user.username) if user else None


class ServiceSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = Service
        fields = "__all__"
