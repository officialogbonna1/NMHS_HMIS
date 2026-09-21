from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.accounts.permissions import EYE_EXAMINATION_ROLES, has_any_role

from . import services as note_services
from .eye_exam import clean_eye_examination
from .models import Vitals, ConsultationNote, ConsultationNoteAmendment, NursingNote


def may_amend(user, note):
    """
    Who may correct a saved note: **its own author, or an admin.**

    One definition, read by the serializer (to offer the button) and by
    `ConsultationNoteViewSet.perform_update` (to actually allow it), so the
    screen and the API can never disagree about it. Another doctor may read
    any colleague's note and may never change one.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(user, "is_admin", False)) or note.doctor_id == user.pk


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
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)

    class Meta:
        model = NursingNote
        fields = "__all__"
        read_only_fields = ["nurse", "is_locked", "locked_at"]

    def get_nurse_name(self, obj):
        return obj.nurse.get_full_name() or obj.nurse.username


class ConsultationNoteAmendmentSerializer(serializers.ModelSerializer):
    """
    One correction, and what it changed.

    `changes` is **derived, never stored**: this row holds what the note said
    before, and what it said *after* is the next amendment's snapshot — or the
    note itself, for the most recent one. Storing both halves would be the same
    fact written twice and free to disagree.
    """
    amended_by_name = serializers.SerializerMethodField()
    amended_by_staff_number = serializers.CharField(source="amended_by.staff_number",
                                                    read_only=True)
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)
    changes = serializers.SerializerMethodField()

    class Meta:
        model = ConsultationNoteAmendment
        fields = "__all__"
        read_only_fields = ["amended_by", "note"]

    def get_amended_by_name(self, obj):
        user = obj.amended_by
        return (user.get_full_name() or user.username) if user else None

    def get_changes(self, obj):
        # Derived in `clinical/services.py`, which Django admin's history
        # inline reads too — one answer to "what did this correction change".
        return note_services.changes_for(obj)


class ConsultationNoteSerializer(serializers.ModelSerializer):
    """
    The note as it stands now, plus who documented it and whether it has been
    corrected since.

    **`doctor` is the author and is never reassigned** — it is `read_only` and
    set once in `perform_create`, so the record always answers "who originally
    documented this examination". Who last *amended* it is a different
    question and is answered from the amendment trail rather than from a
    second column on the note, so the two can never disagree.
    """
    amendments = ConsultationNoteAmendmentSerializer(many=True, read_only=True)
    doctor_name = serializers.SerializerMethodField()
    doctor_staff_number = serializers.CharField(source="doctor.staff_number", read_only=True)
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)

    is_amended = serializers.SerializerMethodField()
    last_amended_at = serializers.SerializerMethodField()
    last_amended_by_name = serializers.SerializerMethodField()
    amendment_count = serializers.SerializerMethodField()
    # Whether *this* caller may amend it. The frontend reads it to decide
    # whether to offer the button; it is a courtesy and never the control —
    # `perform_update` refuses regardless (rule 28).
    can_amend = serializers.SerializerMethodField()

    class Meta:
        model = ConsultationNote
        fields = "__all__"
        read_only_fields = ["doctor", "is_locked", "locked_at"]

    def get_doctor_name(self, obj):
        user = obj.doctor
        return (user.get_full_name() or user.username) if user else None

    def _latest(self, obj):
        # `amendments` is ordered `-created_at`, and is prefetched by the
        # viewset, so this costs nothing per row.
        return next(iter(obj.amendments.all()), None)

    def get_is_amended(self, obj):
        return self._latest(obj) is not None

    def get_amendment_count(self, obj):
        return len(obj.amendments.all())

    def get_last_amended_at(self, obj):
        latest = self._latest(obj)
        return latest.created_at if latest else None

    def get_last_amended_by_name(self, obj):
        latest = self._latest(obj)
        if not latest or not latest.amended_by:
            return None
        return latest.amended_by.get_full_name() or latest.amended_by.username

    def get_can_amend(self, obj):
        user = getattr(self.context.get("request"), "user", None)
        return bool(user) and may_amend(user, obj)

    def validate_eye_examination(self, value):
        """Only the fields `clinical/eye_exam.py` defines, blanks dropped, from the eye doctor."""
        try:
            cleaned = clean_eye_examination(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "error_dict") else exc.messages)
        user = getattr(self.context.get("request"), "user", None)
        if cleaned is not None and not has_any_role(user, EYE_EXAMINATION_ROLES):
            raise serializers.ValidationError("Only the eye doctor records an eye examination.")
        return cleaned

    def update(self, instance, validated_data):
        # The view hands the lock's `admin_override` over through
        # `serializer.save(admin_override=…)`. ModelSerializer.update treats
        # every keyword as a field — it set the flag as an attribute and then
        # called `instance.save()` without it, so the lock refused every admin
        # amendment. The flag goes to `save()`, where LockedRecordMixin reads it.
        admin_override = validated_data.pop("admin_override", False)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save(admin_override=admin_override)
        return instance
