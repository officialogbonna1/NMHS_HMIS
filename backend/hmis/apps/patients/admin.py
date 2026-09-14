from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path

from apps.accounts.permissions import is_super_admin
from apps.core.config import describe

from .models import (
    Patient, Allergy, Medication, MedicalCondition, MedicalDevice,
    SurgicalHistory, FamilyMedicalHistory, SocialHistory, Vaccination, MedicalTest,
)
from .services import money_on_record, purge_history, purge_patient, purge_preview

# Patients are people the hospital treats; Users are staff who log in. They
# are separate models on purpose — a patient has no account and never signs
# in — which is why the admin's Users list only ever showed staff.


class PurgePatientForm(forms.Form):
    """Asks twice: which patient you mean, and why."""
    confirm = forms.CharField(label="Hospital number",
                              help_text="Type this patient's hospital number to confirm.")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "cols": 60}),
                             help_text="Kept in the audit log with what was removed.")

    def __init__(self, *args, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.patient = patient

    def clean_confirm(self):
        value = self.cleaned_data["confirm"]
        if value.upper() != (self.patient.patient_number or "").upper():
            raise forms.ValidationError("That is not this patient's hospital number.")
        return value


class _RecordInline(admin.TabularInline):
    """The health-record tiles, edited on the patient they belong to."""
    extra = 0
    show_change_link = True


class AllergyInline(_RecordInline):
    model = Allergy
    fields = ["name", "reactions", "is_dangerous", "notes"]


class MedicationInline(_RecordInline):
    model = Medication
    fields = ["name", "strength", "dose_frequency", "as_needed"]


class ConditionInline(_RecordInline):
    model = MedicalCondition
    fields = ["name", "date_diagnosed", "notes"]


class VaccinationInline(_RecordInline):
    model = Vaccination
    fields = ["name", "date_administered", "next_due_date"]


class MedicalTestInline(_RecordInline):
    model = MedicalTest
    fields = ["title", "test_type", "test_date", "file", "impressions"]


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ["patient_number", "last_name", "first_name", "sex", "age_display", "phone_number", "created_at"]
    list_display_links = ["patient_number", "last_name", "first_name"]
    list_filter = ["sex", "city", "state", "country", "created_at"]
    search_fields = ["patient_number", "first_name", "middle_name", "last_name", "phone_number",
                     "email", "emergency_contact_name", "emergency_contact_phone"]
    ordering = ["last_name", "first_name"]
    date_hierarchy = "created_at"
    list_per_page = 50
    # Derived from the PK on first save; editing it by hand breaks the one
    # identifier the whole hospital refers to a patient by.
    readonly_fields = ["patient_number", "uuid", "created_at", "updated_at"]
    autocomplete_fields = ["created_by"]
    inlines = [AllergyInline, ConditionInline, MedicationInline, VaccinationInline, MedicalTestInline]

    fieldsets = (
        ("Identity", {"fields": ("patient_number", "uuid", ("first_name", "middle_name", "last_name"), "sex"),
                      "description": "The number is what people read; the UUID is what the API "
                                     "addresses the patient by. Neither is typed in."}),
        ("Age", {"fields": ("birthdate", ("age_value", "age_unit")),
                 "description": "The value and unit are the fallback when a birthdate is not "
                                "known — days and weeks matter for newborns."}),
        ("Contact", {"fields": ("phone_number", "email", "street_address", ("city", "state"), "country")}),
        ("Emergency contact", {
            "fields": (("emergency_contact_name", "emergency_contact_relationship"),
                       ("emergency_contact_phone", "emergency_contact_alt_phone"),
                       "emergency_contact_address", "emergency_contact_notes"),
        }),
        ("Notes", {"fields": ("short_note",)}),
        ("Record", {"fields": ("created_by", "created_at", "updated_at")}),
    )

    @admin.display(description="Age", ordering="birthdate")
    def age_display(self, obj):
        return obj.age_display or "—"

    # Deleting a patient here is purging them (`patients/services.py`): the
    # Super Admin alone, with the hospital number typed and a reason, taking
    # the history the API refuses to. Django's own delete page cannot do it —
    # Vitals and every money admin answer `has_delete_permission = False`,
    # which it reads as a refusal for the cascade — and would write no audit
    # row if it could, so Delete leads to the purge page and there is no bulk
    # delete.

    def has_delete_permission(self, request, obj=None):
        return is_super_admin(request.user) and super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_urls(self):
        # Before the defaults: their `<path:object_id>/` catch-all would
        # otherwise swallow `…/purge/`.
        purge = path("<path:object_id>/purge/", self.admin_site.admin_view(self.purge_view),
                     name="patients_patient_purge")
        return [purge, *super().get_urls()]

    def delete_view(self, request, object_id, extra_context=None):
        return redirect("admin:patients_patient_purge", object_id)

    def purge_view(self, request, object_id):
        patient = self.get_object(request, unquote(object_id))
        if patient is None:
            return self._get_obj_does_not_exist_redirect(request, self.opts, object_id)
        if not self.has_delete_permission(request, patient):
            raise PermissionDenied

        form = PurgePatientForm(request.POST or None, patient=patient)
        if request.method == "POST" and form.is_valid():
            label = f"{patient.patient_number} {patient}"
            with transaction.atomic():
                self.log_deletion(request, patient, label)
                removed = purge_patient(patient=patient, actor=request.user,
                                        reason=form.cleaned_data["reason"],
                                        confirmation=form.cleaned_data["confirm"],
                                        request=request)
            with_history = f", with {describe(removed)}" if removed else ""
            self.message_user(request, f"{label} was permanently deleted{with_history}.",
                              messages.SUCCESS)
            return redirect("admin:patients_patient_changelist")

        history = purge_history(patient)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "title": f"Permanently delete {patient}?",
            "patient": patient,
            "form": form,
            "history": history,
            "history_text": describe(history),
            "money": money_on_record(patient),
            "deleted": purge_preview(patient),
        }
        request.current_app = self.admin_site.name
        return TemplateResponse(request, "admin/patients/patient/purge.html", context)


class _TileAdmin(admin.ModelAdmin):
    """Each tile is also listed on its own, for finding every patient with
    one — everybody on a given drug, everybody due a vaccination."""
    list_select_related = ["patient"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number"]
    autocomplete_fields = ["patient"]
    list_per_page = 50


@admin.register(Allergy)
class AllergyAdmin(_TileAdmin):
    list_display = ["patient", "name", "is_dangerous"]
    list_filter = ["is_dangerous"]
    search_fields = _TileAdmin.search_fields + ["name"]


@admin.register(Medication)
class MedicationAdmin(_TileAdmin):
    list_display = ["patient", "name", "strength", "dose_frequency", "as_needed"]
    list_filter = ["as_needed", "consumption_type"]
    search_fields = _TileAdmin.search_fields + ["name"]


@admin.register(MedicalCondition)
class MedicalConditionAdmin(_TileAdmin):
    list_display = ["patient", "name", "date_diagnosed"]
    search_fields = _TileAdmin.search_fields + ["name"]


@admin.register(MedicalDevice)
class MedicalDeviceAdmin(_TileAdmin):
    list_display = ["patient", "name", "make", "model", "next_update"]
    search_fields = _TileAdmin.search_fields + ["name", "device_id"]


@admin.register(SurgicalHistory)
class SurgicalHistoryAdmin(_TileAdmin):
    list_display = ["patient", "name", "surgery_date"]
    search_fields = _TileAdmin.search_fields + ["name"]


@admin.register(FamilyMedicalHistory)
class FamilyMedicalHistoryAdmin(_TileAdmin):
    list_display = ["patient", "relationship", "is_deceased"]
    list_filter = ["relationship", "is_deceased"]


@admin.register(SocialHistory)
class SocialHistoryAdmin(_TileAdmin):
    list_display = ["patient", "category", "is_active", "frequency"]
    list_filter = ["category", "is_active"]


@admin.register(Vaccination)
class VaccinationAdmin(_TileAdmin):
    list_display = ["patient", "name", "date_administered", "next_due_date"]
    list_filter = ["name"]
    search_fields = _TileAdmin.search_fields + ["name"]


@admin.register(MedicalTest)
class MedicalTestAdmin(_TileAdmin):
    list_display = ["patient", "title", "test_type", "test_date", "has_file"]
    list_filter = ["test_type", "test_date"]
    search_fields = _TileAdmin.search_fields + ["title"]

    @admin.display(description="Document", boolean=True)
    def has_file(self, obj):
        return bool(obj.file)
