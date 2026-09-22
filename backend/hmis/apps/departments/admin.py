from django.contrib import admin

from apps.core.config import ProtectedConfigAdmin

from .models import Department, Service


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 0
    fields = ["name", "code", "price", "is_active"]


@admin.register(Department)
class DepartmentAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """
    The same rows the HMIS Departments screen edits.

    **Active and available for appointments are two different switches.**
    `is_active` says the department is running — its staff, services, stock,
    charges and workflows are all untouched by the other one — and
    `is_appointment_available` says Reception may book a patient into it.
    Pharmacy is active every day and is not an appointment destination; turning
    that off takes nothing away from the POS or the dispensing queue.
    """
    list_display = ["name", "code", "manager", "staff_count", "service_count",
                    "is_active", "is_appointment_available"]
    list_editable = ["is_active", "is_appointment_available"]
    list_filter = ["is_active", "is_appointment_available"]
    search_fields = ["name", "code"]
    actions = ["activate", "deactivate", "open_for_appointments", "close_for_appointments"]
    # A department that has routed a patient, prices a service or has taken
    # money is history. Django admin is held to the same rule the API is.
    protected_relations = ("routes", "services", "lab_tests", "charge_set")

    @admin.display(description="Services")
    def service_count(self, obj):
        return obj.services.count()

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} activated.")

    @admin.action(description="Deactivate selected (keeps history)")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} deactivated.")

    @admin.action(description="Offer as an appointment destination")
    def open_for_appointments(self, request, queryset):
        count = queryset.update(is_appointment_available=True)
        self.message_user(request, f"{count} department(s) can now be booked into.")

    @admin.action(description="Stop offering as an appointment destination")
    def close_for_appointments(self, request, queryset):
        # Appointments already booked keep their department and their place in
        # the queue: this governs what is offered, never what exists.
        count = queryset.update(is_appointment_available=False)
        self.message_user(
            request,
            f"{count} department(s) withdrawn from appointment booking. "
            "Appointments already made are unaffected.")
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
