from rest_framework import serializers
from .models import Prescription


class PrescriptionSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    # On the row because the pharmacy's own paperwork needs it — a dispensing
    # label identifies the patient by file number, and refetching the patient
    # for every line of a script is a request per drug.
    patient_file_number = serializers.CharField(source="patient.file_number", read_only=True)
    patient_age = serializers.CharField(source="patient.age_display", read_only=True)
    patient_sex = serializers.CharField(source="patient.get_sex_display", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    item_unit = serializers.CharField(source="item.unit_label", read_only=True)
    doctor_name = serializers.SerializerMethodField()
    dispensed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Prescription
        fields = "__all__"
        # Everything after the doctor's request is set by the dispense
        # service, never by a client write.
        read_only_fields = [
            "doctor", "status", "dispensed_by", "dispensed_at",
            "dispensed_value", "cancelled_reason",
        ]

    def get_doctor_name(self, obj):
        return _display(obj.doctor)

    def get_dispensed_by_name(self, obj):
        return _display(obj.dispensed_by) if obj.dispensed_by_id else None


def _display(user):
    return user.get_full_name() or user.username
