from rest_framework import serializers
from .models import AuditLog, Notification


class NotificationSerializer(serializers.ModelSerializer):
    # Who it was for. Carried on every row so the admin's whole-system view
    # can say whose inbox it landed in — on your own list it is simply you.
    recipient_name = serializers.SerializerMethodField()
    recipient_role = serializers.CharField(source="recipient.role", read_only=True)

    class Meta:
        model = Notification
        fields = "__all__"
        read_only_fields = ["recipient", "created_at", "updated_at"]

    def get_recipient_name(self, obj):
        user = obj.recipient
        return (user.get_full_name() or user.username) if user else None


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.get_full_name", read_only=True)
    class Meta:
        model = AuditLog
        fields = "__all__"
