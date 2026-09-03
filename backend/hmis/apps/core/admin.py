from django.contrib import admin

from .models import AuditLog, Notification


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
