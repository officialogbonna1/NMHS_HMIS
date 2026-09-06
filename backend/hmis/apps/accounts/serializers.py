from rest_framework import serializers
from .models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "staff_number", "username", "first_name", "last_name", "email", "role", "department", "must_change_password", "sensitive_record_access", "last_login"]


class UserDirectorySerializer(serializers.ModelSerializer):
    """Minimal, safe-for-any-authenticated-staff lookup (e.g. picking a
    doctor when booking an appointment) — no email/department/activity."""
    class Meta:
        model = User
        fields = ["id", "staff_number", "first_name", "last_name", "role"]


class UserAdminSerializer(serializers.ModelSerializer):
    """Used by UserViewSet. Admin-only: creates/edits accounts, never exposes
    or accepts a password here — that goes through the set_password action."""
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ["id", "staff_number", "username", "first_name", "last_name", "email", "role", "department",
                  "is_active", "must_change_password", "sensitive_record_access", "last_login", "password"]
        # `staff_number` is issued by the model on the first save that makes the
        # account staff (see `apps/core/identifiers.py`) — an identifier a form
        # can retype is not one, so admin reads it and never writes it.
        read_only_fields = ["last_login", "staff_number"]

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "Required when creating a user."})
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        validated_data.pop("password", None)
        return super().update(instance, validated_data)
