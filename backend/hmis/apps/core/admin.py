from django.contrib import admin, messages

from .models import AuditLog, HospitalSettings, Notification, NotificationSetting


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """
    The record of who did what. Read-only everywhere, including here — an
    audit trail somebody can edit is not an audit trail.
    """
    list_display = ["created_at", "actor", "action", "content_type", "object_id", "ip_address"]
    list_filter = ["action", "content_type", "created_at"]
    search_fields = ["action", "actor__username", "actor__first_name", "actor__last_name"]
    list_select_related = ["actor", "content_type"]
    date_hierarchy = "created_at"
    list_per_page = 100

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["created_at", "recipient", "title", "category", "is_read", "action_url"]
    list_filter = ["category", "is_read", "created_at"]
    search_fields = ["title", "message", "recipient__username"]
    list_select_related = ["recipient"]
    autocomplete_fields = ["recipient"]
    date_hierarchy = "created_at"
    list_per_page = 50
    actions = ["mark_read"]

    @admin.action(description="Mark selected as read")
    def mark_read(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_read=True)} marked read.")


@admin.register(HospitalSettings)
class HospitalSettingsAdmin(admin.ModelAdmin):
    """
    The hospital's identity and the numbers the dashboards alert on — one
    row, the same one the HMIS administration screen edits.

    Add is refused once the row exists: two answers to "what is this hospital
    called" is one too many, and the letterhead reads whichever came first.
    """
    list_display = ["full_name", "name", "phone", "expiry_warning_days",
                    "vitals_wait_alert_minutes", "unpaid_charge_alert_hours", "updated_at"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = [
        ("Identity", {
            "fields": ["name", "full_name", "address", "phone", "email"],
            "description": "Printed on every document — the patient card, invoices, "
                           "receipts and laboratory reports — and shown in the header.",
        }),
        ("Alert thresholds", {
            "fields": ["expiry_warning_days", "vitals_wait_alert_minutes",
                       "unpaid_charge_alert_hours"],
            "description": "Each of these drives a real alert. Expiry warning also "
                           "drives the nightly stock task.",
        }),
        ("Record", {"fields": ["created_at", "updated_at"], "classes": ["collapse"]}),
    ]

    def has_add_permission(self, request):
        return not HospitalSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Deleting it would take the letterhead and every threshold with it;
        # `load()` would rebuild it with defaults and the edits would be gone.
        return False


@admin.register(NotificationSetting)
class NotificationSettingAdmin(admin.ModelAdmin):
    """
    Which categories of notification the hospital sends. Read by
    `core.services.notify()` on every notification raised, so switching one
    off here actually stops them.

    Clinical cannot be switched off — a result reaching the clinician who
    ordered it is not a preference — and `notify()` ignores the flag if it
    is, so the form refuses rather than lying.
    """
    list_display = ["category_label", "is_enabled", "description"]
    list_editable = ["is_enabled"]
    list_filter = ["is_enabled"]
    search_fields = ["category", "description"]
    ordering = ["category"]
    actions = ["enable", "disable"]

    @admin.display(description="Category", ordering="category")
    def category_label(self, obj):
        return obj.get_category_display()

    def has_add_permission(self, request):
        # The categories are the ones the application raises; a row for a
        # category nothing sends would be a switch wired to nothing.
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        from .services import ALWAYS_ON

        if obj.category in ALWAYS_ON and not obj.is_enabled:
            obj.is_enabled = True
            self.message_user(
                request,
                f"{obj.get_category_display()} notifications cannot be switched off — "
                f"left on.", messages.WARNING)
        super().save_model(request, obj, form, change)

    @admin.action(description="Enable selected")
    def enable(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_enabled=True)} category(ies) enabled.")

    @admin.action(description="Disable selected (clinical stays on)")
    def disable(self, request, queryset):
        from .services import ALWAYS_ON

        changed = queryset.exclude(category__in=ALWAYS_ON).update(is_enabled=False)
        self.message_user(request, f"{changed} category(ies) disabled.", messages.WARNING)
