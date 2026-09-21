from django.utils import timezone
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
        return admission.patient.display_name
class AdmissionSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
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
    # The admission as it is *referred to* — on a board, down a phone, on the
    # discharge letter. Derived from the primary key exactly as
    # `DischargeSummarySerializer.admission_reference` already does it, so the
    # two cannot disagree; there is no column and nothing to backfill, which is
    # the same reasoning rule 32 applies to the hospital number's format living
    # in one place.
    reference = serializers.SerializerMethodField()
    # How long they have been in. Counted to the discharge where there is one
    # and to now where there is not, so a closed admission stops ageing the day
    # the patient went home.
    length_of_stay_days = serializers.SerializerMethodField()
    length_of_stay = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # Whether this admission has been closed, and under what reference — so a
    # list can offer the letter instead of a Discharge button without asking a
    # second endpoint. Read from the existing `DischargeSummary` row; nothing
    # new is stored (rule 7 of the billing brief, applied here).
    discharge_reference = serializers.SerializerMethodField()
    discharge_id = serializers.SerializerMethodField()
    class Meta: model=Admission; fields="__all__"; read_only_fields=["admitted_by","status","admitted_at","discharged_at"]

    def get_reference(self, obj):
        return f"ADM-{obj.pk:06d}" if obj.pk else ""

    def _days(self, obj):
        if not obj.admitted_at:
            return None
        end = obj.discharged_at or timezone.now()
        return max((end - obj.admitted_at).days, 0)

    def get_length_of_stay_days(self, obj):
        return self._days(obj)

    def get_length_of_stay(self, obj):
        days = self._days(obj)
        if days is None:
            return ""
        if days == 0:
            return "Same day"
        return f"{days} day" + ("" if days == 1 else "s")

    def _summary(self, obj):
        return getattr(obj, "discharge_summary", None)

    def get_discharge_reference(self, obj):
        record = self._summary(obj)
        return getattr(record, "reference", "") if record else ""

    def get_discharge_id(self, obj):
        record = self._summary(obj)
        return record.pk if record else None
    def get_attending_doctor_name(self, obj):
        user = obj.attending_doctor
        return (user.get_full_name() or user.username) if user else None
    def get_admitted_by_name(self, obj):
        user = obj.admitted_by
        return (user.get_full_name() or user.username) if user else None
class BedTransferSerializer(serializers.ModelSerializer):
    from_bed_number = serializers.CharField(source="from_bed.number", read_only=True)
    to_bed_number = serializers.CharField(source="to_bed.number", read_only=True)
    # Which ward each bed is on, and who moved the patient — what a move
    # history has to say to be readable. A transfer between wards is the
    # interesting one, and "Bed 4 → Bed 11" does not say that it happened.
    from_ward_name = serializers.CharField(source="from_bed.ward.name", read_only=True)
    to_ward_name = serializers.CharField(source="to_bed.ward.name", read_only=True)
    transferred_by_name = serializers.SerializerMethodField()
    # `from_bed` is where the patient actually is — the view reads it off the
    # admission under lock. Asking the caller for it made every transfer a
    # 400, and trusting their answer would let a move be recorded out of a
    # bed the patient was never in.
    class Meta:
        model = BedTransfer
        fields = "__all__"
        read_only_fields = ["transferred_by", "from_bed"]

    def get_transferred_by_name(self, obj):
        user = obj.transferred_by
        return (user.get_full_name() or user.username) if user else None
class DischargeSummarySerializer(serializers.ModelSerializer):
    """
    A completed discharge, carrying the identity the discharged-patients list
    and the letter read — so neither needs a second call to say who left, from
    which bed and when.

    `reference` and `completed_by` are stamped by
    `inpatient/services.discharge_patient`, never sent by a caller: a discharge
    that could be filed under somebody else's name, or carry a reference the
    client chose, would not be a record of anything.
    """
    patient = serializers.IntegerField(source="admission.patient_id", read_only=True)
    patient_name = serializers.CharField(source="admission.patient.display_name", read_only=True)
    patient_number = serializers.CharField(source="admission.patient.patient_number",
                                           read_only=True)
    patient_uuid = serializers.CharField(source="admission.patient.uuid", read_only=True)
    admission_reference = serializers.SerializerMethodField()
    admitted_at = serializers.DateTimeField(source="admission.admitted_at", read_only=True)
    discharged_at = serializers.DateTimeField(source="admission.discharged_at", read_only=True)
    admission_status = serializers.CharField(source="admission.status", read_only=True)
    ward_name = serializers.CharField(source="admission.bed.ward.name", read_only=True)
    bed_number = serializers.CharField(source="admission.bed.number", read_only=True)
    completed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = DischargeSummary
        fields = "__all__"
        read_only_fields = ["completed_by", "reference"]

    def get_admission_reference(self, obj):
        return f"ADM-{obj.admission_id:06d}"

    def get_completed_by_name(self, obj):
        user = obj.completed_by
        return (user.get_full_name() or user.username) if user else None
