from django.contrib import admin

from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    """
    A queue, not a diary: start/end are stamped by the provider's own
    transitions, so they record what happened rather than what was booked.

    The booked service, its fee and the charge it raised are **read-only**
    here for the reason every service-owned figure is (see the admin rules in
    CLAUDE.md): they are a snapshot taken when the appointment was queued, and
    the charge beside them is kept in step by `billing/services.py`. Editing
    the fee here would move one number without the other.
    """
    list_display = ["created_at", "patient", "department", "service_name", "doctor",
                    "status", "service_fee", "charge", "start_time", "end_time"]
    list_filter = ["status", "created_at", "department", "doctor"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number",
                     "reason", "service_name"]
    list_select_related = ["patient", "doctor", "department", "charge"]
    autocomplete_fields = ["patient", "doctor"]
    readonly_fields = ["service", "service_name", "service_fee", "charge", "department"]
    date_hierarchy = "created_at"
    list_per_page = 50
