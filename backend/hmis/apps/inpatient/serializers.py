from rest_framework import serializers
from apps.accounts.permissions import OWN_PATIENT_WARD_ROLES
from apps.patients.access import patient_queryset_for
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
        if not admission:
            return None
        # A role that works the ward for its own patients sees that a bed is
        # taken, never whose it is unless the patient is theirs.
        user = getattr(self.context.get("request"), "user", None)
        if getattr(user, "role", None) in OWN_PATIENT_WARD_ROLES:
            if "_own_patient_ids" not in self.context:
                self.context["_own_patient_ids"] = set(
                    patient_queryset_for(user).values_list("pk", flat=True))
            if admission.patient_id not in self.context["_own_patient_ids"]:
                return None
        return str(admission.patient)
class AdmissionSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_file_number = serializers.CharField(source="patient.patient_number", read_only=True)
    bed_number = serializers.CharField(source="bed.number", read_only=True)
    ward_name = serializers.CharField(source="bed.ward.name", read_only=True)
    attending_doctor_name = serializers.SerializerMethodField()
    # What an admission slip is made of. The ward prints one at the bedside,
    # so it must not need a second call to the patient record to say who is
    # in the bed.
    patient_sex = serializers.CharField(source="patient.get_sex_display", read_only=True)
    patient_age = serializers.CharField(source="patient.age_display", read_only=True)
    admitted_by_name = serializers.SerializerMethodField()
    class Meta: model=Admission; fields="__all__"; read_only_fields=["admitted_by","status","admitted_at","discharged_at"]
    def get_attending_doctor_name(self, obj):
        user = obj.attending_doctor
        return (user.get_full_name() or user.username) if user else None
    def get_admitted_by_name(self, obj):
        user = obj.admitted_by
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
