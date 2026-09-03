from django.contrib import admin

from .models import Visit, PatientRoute


class PatientRouteInline(admin.TabularInline):
    model = PatientRoute
    extra = 0
    fields = ["purpose", "department", "assigned_to", "priority", "status", "notes"]
    show_change_link = True


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ["patient", "visit_type", "status", "attending_doctor", "reason", "created_at"]
    list_filter = ["status", "visit_type", "created_at"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__file_number", "reason"]
    list_select_related = ["patient", "attending_doctor"]
    autocomplete_fields = ["patient", "attending_doctor", "opened_by"]
    date_hierarchy = "created_at"
    list_per_page = 50
    inlines = [PatientRouteInline]


@admin.register(PatientRoute)
class PatientRouteAdmin(admin.ModelAdmin):
    list_display = ["patient_name", "purpose", "department", "assigned_to", "priority", "status", "created_at"]
    list_filter = ["status", "purpose", "priority", "department", "created_at"]
    search_fields = ["visit__patient__first_name", "visit__patient__last_name",
                     "visit__patient__file_number"]
    list_select_related = ["visit__patient", "department", "assigned_to"]
    autocomplete_fields = ["visit", "assigned_to", "routed_by"]
    date_hierarchy = "created_at"
    list_per_page = 50

    @admin.display(description="Patient", ordering="visit__patient__last_name")
    def patient_name(self, obj):
        return obj.visit.patient
