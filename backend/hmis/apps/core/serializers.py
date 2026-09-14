from rest_framework import serializers
from .models import AuditLog, HospitalSettings, Notification, NotificationSetting


class NotificationSerializer(serializers.ModelSerializer):
    # Who it was for. Carried on every row so the admin's whole-system view
    # can say whose inbox it landed in — on your own list it is simply you.
    recipient_name = serializers.SerializerMethodField()
    recipient_role = serializers.CharField(source="recipient.role", read_only=True)
    is_archived = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = "__all__"
        # `archived_at` moves only through the archive / unarchive actions, so
        # the server stamps when — a PATCH cannot back-date or clear it.
        read_only_fields = ["recipient", "archived_at", "created_at", "updated_at"]

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
    The hospital's identity, the thresholds the dashboards alert on, and what
    the pharmacy till may discount.

    Every field here changes real behaviour — the printed documents'
    letterhead, the three alert windows, and the ceilings
    `sales/discount_policy.py` enforces on every sale — so there is nothing on
    this form that only looks like it does something.
    """
    # Read by everybody (the POS dialog offers exactly what is allowed here),
    # written by an admin — the viewset's own rule, unchanged. They are
    # derived rather than stored so the two spellings of a preset list,
    # "5,10" and "5, 10", can never disagree with each other.
    pos_discount_preset_values = serializers.SerializerMethodField()
    pos_discount_reason_options = serializers.SerializerMethodField()

    class Meta:
        model = HospitalSettings
        fields = ["id", "name", "full_name", "address", "phone", "email",
                  "expiry_warning_days", "vitals_wait_alert_minutes",
                  "unpaid_charge_alert_hours",
                  # The pharmacy till's discount policy (rule 42).
                  "pos_discounts_enabled", "pos_discount_types",
                  "pos_discount_limit_percent", "pos_max_discount_percent",
                  "pos_discount_limit_amount", "pos_max_discount_amount",
                  "pos_discount_presets", "pos_discount_preset_values",
                  "pos_discount_reasons", "pos_discount_reason_options",
                  "updated_at"]
        read_only_fields = ["id", "updated_at"]

    def get_pos_discount_preset_values(self, obj):
        return [str(value) for value in obj.pos_discount_presets_list]

    def get_pos_discount_reason_options(self, obj):
        return obj.pos_discount_reasons_list

    def validate(self, attrs):
        """
        A limit above its own maximum is a policy that cannot mean anything:
        the cashier's ceiling would sit above the one nobody may pass. Caught
        here so the administration screen says so, rather than the till
        refusing a discount the settings appear to allow.
        """
        current = self.instance
        def value(name):
            return attrs.get(name, getattr(current, name, None))

        limit_percent, max_percent = value("pos_discount_limit_percent"), value("pos_max_discount_percent")
        if limit_percent is not None and max_percent is not None and limit_percent > max_percent:
            raise serializers.ValidationError({
                "pos_discount_limit_percent":
                    f"A cashier's limit ({limit_percent}%) cannot be above the maximum "
                    f"anybody may give ({max_percent}%)."})
        limit_amount, max_amount = value("pos_discount_limit_amount"), value("pos_max_discount_amount")
        if limit_amount and max_amount and limit_amount > max_amount:
            raise serializers.ValidationError({
                "pos_discount_limit_amount":
                    f"A cashier's limit ({limit_amount}) cannot be above the maximum "
                    f"anybody may give ({max_amount})."})
        return attrs

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
