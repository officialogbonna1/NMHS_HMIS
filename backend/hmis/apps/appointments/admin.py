from django.contrib import admin

from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    """
    A queue, not a diary: start/end are stamped by the doctor's own
    transitions, so they record what happened rather than what was booked.
    """
    list_display = ["created_at", "patient", "doctor", "reason", "status", "start_time", "end_time"]
    list_filter = ["status", "created_at", "doctor"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__file_number", "reason"]
    list_select_related = ["patient", "doctor"]
    autocomplete_fields = ["patient", "doctor"]
    date_hierarchy = "created_at"
    list_per_page = 50
