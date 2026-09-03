from django.contrib import admin

from .models import Vitals, ConsultationNote, NursingNote, ConsultationNoteAmendment


class LockedRecordAdmin(admin.ModelAdmin):
    """
    Vitals and notes lock on save (core.mixins.LockedRecordMixin) — the model
    itself refuses a second write without `admin_override`, which the admin's
    save path has no way to pass. So they are listed and read here, never
    edited: a correction is a new record, and an amendment goes through
    ConsultationNoteAmendment. Making them editable here would 403 on save
    and, worse, imply clinical history can be rewritten in place.
    """
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Vitals)
class VitalsAdmin(LockedRecordAdmin):
    list_display = ["patient", "visit_time", "temperature_c", "heart_rate", "blood_pressure",
                    "sao2", "recorded_by"]
    list_filter = ["visit_time", "recorded_by"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__file_number"]
    list_select_related = ["patient", "recorded_by"]
    date_hierarchy = "visit_time"

    @admin.display(description="BP")
    def blood_pressure(self, obj):
        return f"{obj.bp_systolic}/{obj.bp_diastolic}" if obj.bp_systolic and obj.bp_diastolic else "—"


@admin.register(NursingNote)
class NursingNoteAdmin(LockedRecordAdmin):
    list_display = ["patient", "created_at", "nurse", "complaint"]
    list_filter = ["created_at", "nurse"]
    search_fields = ["patient__first_name", "patient__last_name", "complaint", "observation"]
    list_select_related = ["patient", "nurse"]
    date_hierarchy = "created_at"


@admin.register(ConsultationNote)
class ConsultationNoteAdmin(LockedRecordAdmin):
    list_display = ["patient", "visit_time", "doctor", "reason_for_visit", "diagnosis"]
    list_filter = ["visit_time", "doctor"]
    search_fields = ["patient__first_name", "patient__last_name", "reason_for_visit", "diagnosis"]
    list_select_related = ["patient", "doctor"]
    date_hierarchy = "visit_time"


@admin.register(ConsultationNoteAmendment)
class ConsultationNoteAmendmentAdmin(LockedRecordAdmin):
    """The archive of what a note said before it was amended."""
    list_display = ["note", "created_at", "amended_by"]
    list_filter = ["created_at"]
    list_select_related = ["note", "amended_by"]
