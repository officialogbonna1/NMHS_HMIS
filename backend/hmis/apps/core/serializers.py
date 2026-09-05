from rest_framework import serializers
from .models import AuditLog, HospitalSettings, Notification, NotificationSetting


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


class HospitalSettingsSerializer(serializers.ModelSerializer):
    """
    The hospital's identity and the thresholds the dashboards alert on.

    Every field here changes real behaviour — the printed documents' letterhead
    and the three alert windows — so there is nothing on this form that only
    looks like it does something.
    """
    class Meta:
        model = HospitalSettings
        fields = ["id", "name", "full_name", "address", "phone", "email",
                  "expiry_warning_days", "vitals_wait_alert_minutes",
                  "unpaid_charge_alert_hours", "updated_at"]
        read_only_fields = ["id", "updated_at"]

    def validate_expiry_warning_days(self, value):
        if value < 1:
            raise serializers.ValidationError("Warn at least one day ahead.")
        return value

    def validate_vitals_wait_alert_minutes(self, value):
        if value < 1:
            raise serializers.ValidationError("A waiting time of zero would alert on everybody.")
        return value

    def validate_unpaid_charge_alert_hours(self, value):
        if value < 1:
            raise serializers.ValidationError("Give the desk at least an hour to collect.")
        return value


class NotificationSettingSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    # Clinical notifications are not a preference — a result reaching the
    # doctor who ordered it has to happen — so the row says so rather than
    # offering a switch that the server would ignore.
    can_disable = serializers.SerializerMethodField()

    class Meta:
        model = NotificationSetting
        fields = ["id", "category", "category_label", "is_enabled", "description", "can_disable"]
        read_only_fields = ["category"]

    def get_can_disable(self, obj):
        from .services import ALWAYS_ON
        return obj.category not in ALWAYS_ON
