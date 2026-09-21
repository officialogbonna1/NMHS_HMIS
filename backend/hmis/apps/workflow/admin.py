from django.contrib import admin

from .models import Visit, PatientRoute, RouteService


class PatientRouteInline(admin.TabularInline):
    model = PatientRoute
    extra = 0
    fields = ["purpose", "department", "assigned_to", "priority", "status", "notes"]
    show_change_link = True


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ["patient", "visit_type", "status", "attending_doctor", "reason", "created_at"]
    list_filter = ["status", "visit_type", "created_at"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number", "reason"]
    list_select_related = ["patient", "attending_doctor"]
    autocomplete_fields = ["patient", "attending_doctor", "opened_by"]
    date_hierarchy = "created_at"
    list_per_page = 50
    inlines = [PatientRouteInline]


class RouteServiceInline(admin.TabularInline):
    """What the referral asked for. Read-only: a requested service carries the
    charge it raised, and the two are kept in step by `workflow/services.py`
    (rule 24) — typing over either here would move one without the other."""
    model = RouteService
    extra = 0
    fields = ["name", "unit_price", "item", "charge", "requested_by"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(RouteService)
class RouteServiceAdmin(admin.ModelAdmin):
    """The examinations referrals have asked for, and the bills they raised.

    Read-only for the reason `billing`'s own admins are: the charge beside it
    is maintained by `billing/services.py`, and editing a price here would
    leave the two disagreeing."""
    list_display = ["name", "route", "unit_price", "charge", "requested_by", "created_at"]
    list_filter = ["route__purpose", "created_at"]
    search_fields = ["name", "route__visit__patient__patient_number",
                     "route__visit__patient__last_name"]
    list_select_related = ["route__visit__patient", "charge", "requested_by"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PatientRoute)
class PatientRouteAdmin(admin.ModelAdmin):
    list_display = ["patient_name", "purpose", "department", "assigned_to", "priority", "status", "created_at"]
    list_filter = ["status", "purpose", "priority", "department", "created_at"]
    search_fields = ["visit__patient__first_name", "visit__patient__last_name",
                     "visit__patient__patient_number"]
    list_select_related = ["visit__patient", "department", "assigned_to"]
    autocomplete_fields = ["visit", "assigned_to", "routed_by"]
    date_hierarchy = "created_at"
    list_per_page = 50
    inlines = [RouteServiceInline]

    @admin.display(description="Patient", ordering="visit__patient__last_name")
    def patient_name(self, obj):
        return obj.visit.patient
