from rest_framework import serializers
from .models import Vitals, ConsultationNote, ConsultationNoteAmendment, NursingNote


class VitalsSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Vitals
        fields = "__all__"
        read_only_fields = ["recorded_by", "is_locked", "locked_at"]

    def get_recorded_by_name(self, obj):
        return obj.recorded_by.get_full_name() or obj.recorded_by.username


class NursingNoteSerializer(serializers.ModelSerializer):
    nurse_name = serializers.SerializerMethodField()
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)

    class Meta:
        model = NursingNote
        fields = "__all__"
        read_only_fields = ["nurse", "is_locked", "locked_at"]

    def get_nurse_name(self, obj):
        return obj.nurse.get_full_name() or obj.nurse.username


class ConsultationNoteAmendmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsultationNoteAmendment
        fields = "__all__"
        read_only_fields = ["amended_by"]


class ConsultationNoteSerializer(serializers.ModelSerializer):
    amendments = ConsultationNoteAmendmentSerializer(many=True, read_only=True)

    class Meta:
        model = ConsultationNote
        fields = "__all__"
        read_only_fields = ["doctor", "is_locked", "locked_at"]
