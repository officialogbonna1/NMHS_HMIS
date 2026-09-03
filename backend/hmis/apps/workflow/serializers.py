from rest_framework import serializers
from .models import Visit, PatientRoute
class VisitSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    class Meta: model = Visit; fields = "__all__"; read_only_fields = ["opened_by"]
class PatientRouteSerializer(serializers.ModelSerializer):
    patient_id = serializers.IntegerField(source="visit.patient_id", read_only=True)
    patient_name = serializers.CharField(source="visit.patient.__str__", read_only=True)
    patient_file_number = serializers.CharField(source="visit.patient.file_number", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    assigned_to_role = serializers.CharField(source="assigned_to.role", read_only=True)
    routed_by_name = serializers.SerializerMethodField()
    result_by_name = serializers.SerializerMethodField()
    purpose_label = serializers.CharField(source="get_purpose_display", read_only=True)
    result_file_url = serializers.SerializerMethodField()
    result_file_name = serializers.SerializerMethodField()

    # Status moves through the start/complete/cancel actions only. Reception
    # raises the route and can call it off; the clinician the patient was sent
    # to is the one who says the work is under way or done.
    # The finding is written through the record-result / complete actions,
    # which stamp who wrote it — never by PATCHing the row.
    class Meta:
        model = PatientRoute
        fields = "__all__"
        read_only_fields = ["routed_by", "status", "result", "result_by", "result_at"]

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.get_full_name() or obj.assigned_to.username if obj.assigned_to else None

    def validate_assigned_to(self, user):
        # Routing to a disabled account silently parks the patient in a queue
        # nobody is watching.
        if user and not user.is_active:
            raise serializers.ValidationError("That staff account is disabled.")
        return user

    def get_routed_by_name(self, obj):
        user = obj.routed_by
        return (user.get_full_name() or user.username) if user else None

    def get_result_by_name(self, obj):
        user = obj.result_by
        return (user.get_full_name() or user.username) if user else None

    def _filed(self, obj):
        return obj.filed_tests.first()

    def get_result_file_url(self, obj):
        test = self._filed(obj)
        try:
            return test.file.url if test and test.file else None
        except ValueError:
            return None

    def get_result_file_name(self, obj):
        test = self._filed(obj)
        return test.file.name.rsplit("/", 1)[-1] if test and test.file else None
