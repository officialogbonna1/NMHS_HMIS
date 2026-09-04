from rest_framework import serializers
from .models import (
    Patient, Allergy, Medication, MedicalCondition, MedicalDevice,
    SurgicalHistory, FamilyMedicalHistory, SocialHistory, Vaccination, MedicalTest,
)


class PatientSerializer(serializers.ModelSerializer):
    # "Male", not "M" — this is printed on the patient's card.
    sex_display = serializers.CharField(source="get_sex_display", read_only=True)
    # "3 days" / "7 months" / "42 yrs" — worked out from the birthdate where
    # there is one, so nothing has to recompute it per screen.
    age_display = serializers.CharField(read_only=True)

    class Meta:
        model = Patient
        fields = "__all__"
        read_only_fields = ["created_by"]


class PatientDemographicsSerializer(serializers.ModelSerializer):
    """Reception-safe representation: no clinical or sensitive registration notes."""
    sex_display = serializers.CharField(source="get_sex_display", read_only=True)
    age_display = serializers.CharField(read_only=True)

    class Meta:
        model = Patient
        # street_address is here because reception typed it in at
        # registration and it goes back onto the printed card; the
        # clinical note deliberately stays out.
        fields = ["id", "file_number", "first_name", "middle_name", "last_name", "sex",
                  "sex_display", "birthdate", "age_value", "age_unit", "age_display", "phone_number", "email",
                  "street_address", "city", "state", "country",
                  # Reception takes these at registration and is who gets
                  # asked for them in an emergency.
                  "emergency_contact_name", "emergency_contact_relationship",
                  "emergency_contact_phone", "emergency_contact_alt_phone",
                  "emergency_contact_address", "emergency_contact_notes",
                  "created_at"]


class AllergySerializer(serializers.ModelSerializer):
    class Meta:
        model = Allergy
        fields = "__all__"


class MedicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medication
        fields = "__all__"


class MedicalConditionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicalCondition
        fields = "__all__"


class MedicalDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicalDevice
        fields = "__all__"


class SurgicalHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = SurgicalHistory
        fields = "__all__"


class FamilyMedicalHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyMedicalHistory
        fields = "__all__"


class SocialHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = SocialHistory
        fields = "__all__"


class VaccinationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vaccination
        fields = "__all__"


class MedicalTestSerializer(serializers.ModelSerializer):
    """
    A result is either a document or a typed finding — often both, never
    neither. The file is optional so a result read over the phone can still
    be recorded; `validate` is what stops an empty shell being saved.
    """
    file_url = serializers.SerializerMethodField()
    file_name = serializers.SerializerMethodField()
    test_type_label = serializers.CharField(source="get_test_type_display", read_only=True)

    class Meta:
        model = MedicalTest
        fields = "__all__"

    def get_file_url(self, obj):
        try:
            return obj.file.url if obj.file else None
        except ValueError:
            return None

    def get_file_name(self, obj):
        return obj.file.name.rsplit("/", 1)[-1] if obj.file else None

    def validate(self, attrs):
        # On a PATCH the untouched half is already on the instance.
        file = attrs.get("file", getattr(self.instance, "file", None))
        impressions = attrs.get("impressions", getattr(self.instance, "impressions", "") or "")
        if not file and not impressions.strip():
            raise serializers.ValidationError(
                {"impressions": "Attach the result document, or type the findings here."}
            )
        return attrs
