from rest_framework import serializers
from .models import Appointment


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    doctor_name = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = "__all__"
        # Status only ever moves through the accept/cancel/start/end
        # actions (apps.appointments.views), never a raw field write —
        # keeps every transition going through the state-machine checks.
        # start_time/end_time are stamped by those same transitions, so
        # reception cannot schedule (or back-date) a consultation.
        read_only_fields = ["status", "start_time", "end_time"]

    def get_doctor_name(self, obj):
        return obj.doctor.get_full_name() or obj.doctor.username if obj.doctor_id else None
