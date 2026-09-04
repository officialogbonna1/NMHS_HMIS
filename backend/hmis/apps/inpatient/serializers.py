from rest_framework import serializers
from .models import Ward, Bed, Admission, BedTransfer, DischargeSummary
class WardSerializer(serializers.ModelSerializer):
    bed_count = serializers.SerializerMethodField()
    occupied_count = serializers.SerializerMethodField()
    class Meta: model=Ward; fields="__all__"
    def get_bed_count(self, obj): return obj.beds.filter(is_active=True).count()
    def get_occupied_count(self, obj):
        return obj.beds.filter(admissions__status="admitted").distinct().count()
class BedSerializer(serializers.ModelSerializer):
    occupied = serializers.SerializerMethodField()
    ward_name = serializers.CharField(source="ward.name", read_only=True)
    occupant = serializers.SerializerMethodField()
    class Meta: model=Bed; fields="__all__"
    def get_occupied(self,obj): return obj.admissions.filter(status="admitted").exists()
    def get_occupant(self, obj):
        admission = obj.admissions.filter(status="admitted").select_related("patient").first()
        return str(admission.patient) if admission else None
class AdmissionSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    patient_file_number = serializers.CharField(source="patient.file_number", read_only=True)
    bed_number = serializers.CharField(source="bed.number", read_only=True)
    ward_name = serializers.CharField(source="bed.ward.name", read_only=True)
    attending_doctor_name = serializers.SerializerMethodField()
    class Meta: model=Admission; fields="__all__"; read_only_fields=["admitted_by","status","admitted_at","discharged_at"]
    def get_attending_doctor_name(self, obj):
        user = obj.attending_doctor
        return (user.get_full_name() or user.username) if user else None
class BedTransferSerializer(serializers.ModelSerializer):
    from_bed_number = serializers.CharField(source="from_bed.number", read_only=True)
    to_bed_number = serializers.CharField(source="to_bed.number", read_only=True)
    # `from_bed` is where the patient actually is — the view reads it off the
    # admission under lock. Asking the caller for it made every transfer a
    # 400, and trusting their answer would let a move be recorded out of a
    # bed the patient was never in.
    class Meta:
        model = BedTransfer
        fields = "__all__"
        read_only_fields = ["transferred_by", "from_bed"]
class DischargeSummarySerializer(serializers.ModelSerializer):
    class Meta: model=DischargeSummary; fields="__all__"; read_only_fields=["completed_by"]
