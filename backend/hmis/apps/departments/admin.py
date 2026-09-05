from django.contrib import admin

from apps.core.config import ProtectedConfigAdmin

from .models import Department, Service


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 0
    fields = ["name", "code", "price", "is_active"]


@admin.register(Department)
class DepartmentAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """The same rows the HMIS Departments screen edits."""
    list_display = ["name", "code", "manager", "staff_count", "service_count", "is_active"]
    list_editable = ["is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "code"]
    actions = ["activate", "deactivate"]
    # A department that has routed a patient or prices a service is history.
    protected_relations = ("routes", "services", "lab_tests")

    @admin.display(description="Services")
    def service_count(self, obj):
        return obj.services.count()

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} activated.")

    @admin.action(description="Deactivate selected (keeps history)")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} deactivated.")
    prepopulated_fields = {"code": ("name",)}
    filter_horizontal = ["staff"]
    autocomplete_fields = ["manager"]
    inlines = [ServiceInline]

    @admin.display(description="Staff")
    def staff_count(self, obj):
        return obj.staff.count()


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "department", "code", "price", "is_active"]
    list_editable = ["price", "is_active"]
    list_filter = ["department", "is_active"]
    search_fields = ["name", "code"]
    ordering = ["department__name", "name"]
    list_select_related = ["department"]
    autocomplete_fields = ["department"]
    actions = ["activate", "deactivate"]

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} activated.")

    @admin.action(description="Deactivate selected")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} deactivated.")
