from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.html import format_html, format_html_join

from . import eye_exam
from . import services as note_services
from .eye_exam import clean_eye_examination
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
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number"]
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


class AmendmentInline(admin.TabularInline):
    """
    The note's history, on the note. Read-only and add-less on purpose: an
    amendment row is *written by* the act of correcting the note above it
    (`clinical/services.archive`), never typed in beside it — a hand-made row
    would be a claim that a correction happened when none did.
    """
    model = ConsultationNoteAmendment
    extra = 0
    can_delete = False
    verbose_name_plural = "Amendment history (most recent first)"
    fields = ["created_at", "amended_by", "reason", "detail", "what_changed"]
    readonly_fields = fields
    ordering = ["-created_at"]

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="What changed")
    def what_changed(self, obj):
        """
        What this one correction changed, from `clinical/services.changes_for`
        — the same answer the chart's amendment history shows.

        Deliberately *not* the whole snapshot. Printing every stored field
        buried a one-word correction under sixty unchanged eye findings, which
        is the opposite of what a history column is for.
        """
        changes = note_services.changes_for(obj)
        if not changes:
            return "— no field changed"
        return format_html("<dl>{}</dl>", format_html_join(
            "", "<dt><strong>{}</strong></dt><dd>{} &rarr; {}</dd>",
            ((c["label"], c["previous"] or "(blank)", c["current"] or "(blank)")
             for c in changes)))


class ConsultationNoteForm(forms.ModelForm):
    """
    The note's fields, plus — when an existing note is being corrected — why.

    The reason is **not** a field on `ConsultationNote`; it belongs to the
    amendment that `save_model` writes. It is collected here so that Django
    admin is held to exactly the rule the API is held to (rule 45): a
    correction to a saved note says why, or it is refused. A *new* note has
    nothing to explain, so the fields are hidden on the add form.
    """
    amendment_reason = forms.ChoiceField(
        required=False, choices=[("", "---------"), *ConsultationNoteAmendment.REASONS],
        label="Reason for amendment",
        help_text="Required when correcting a saved note. Kept in the amendment history.")
    amendment_detail = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        label="Amendment details",
        help_text="What you are correcting, in your own words. Optional.")

    class Meta:
        model = ConsultationNote
        fields = ["patient", "visit_time", "reason_for_visit", "chief_complaint",
                  "note_text", "diagnosis", "plan", "eye_examination"]

    def clean(self):
        cleaned = super().clean()
        # `self.instance.pk` is the difference between writing a record and
        # rewriting one. Only the second needs a reason.
        if self.instance.pk and not note_services.is_valid_reason(cleaned.get("amendment_reason")):
            self.add_error("amendment_reason",
                           "Say why this note is being amended. The previous version is kept "
                           "in the amendment history either way.")
        return cleaned

    def clean_eye_examination(self):
        """The same definition the API validates against — `clinical/eye_exam.py`
        — so a finding typed here cannot be one the chart would refuse."""
        try:
            return clean_eye_examination(self.cleaned_data.get("eye_examination"))
        except DjangoValidationError as exc:
            raise forms.ValidationError(
                exc.messages if not hasattr(exc, "error_dict")
                else [f"{key}: {' '.join(msgs)}" for key, msgs in exc.message_dict.items()])


@admin.register(ConsultationNote)
class ConsultationNoteAdmin(admin.ModelAdmin):
    """
    The back office's door onto the clinical note — **the same note**, the same
    amendment trail and the same audit rows as the chart (rule 31: one set of
    models, two administration interfaces).

    Deliberately *not* a `LockedRecordAdmin`. `Vitals`, `NursingNote` and the
    amendment trail still are, and still refuse every write. A consultation
    note is different because it has an amendment path (rule 45): correcting
    one here archives what it said, records who and why, and only then saves —
    so history is added to, never written over. Deleting is still refused
    outright: a medical record is corrected, not removed.
    """
    form = ConsultationNoteForm
    inlines = [AmendmentInline]
    list_display = ["patient_number", "patient", "visit_time", "doctor", "reason_for_visit",
                    "diagnosis", "has_eye_examination", "amended", "last_amended_at",
                    "last_amended_by"]
    list_display_links = ["patient_number", "patient"]
    list_filter = ["visit_time", "created_at", "doctor", "amendments__reason"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number",
                     "doctor__first_name", "doctor__last_name", "doctor__staff_number",
                     "reason_for_visit", "chief_complaint", "diagnosis", "plan"]
    list_select_related = ["patient", "doctor"]
    autocomplete_fields = ["patient"]
    date_hierarchy = "visit_time"
    list_per_page = 50
    ordering = ["-visit_time"]

    # The author, the lock and the timestamps are the record's own; none of
    # them is a thing an administrator types.
    readonly_fields = ["doctor", "documented_by", "created_at", "updated_at",
                       "is_locked", "locked_at", "amended", "eye_examination_findings"]

    fieldsets = (
        ("Patient and encounter", {
            "fields": ("patient", "visit_time"),
            "description": "The note is filed against this patient and shows on their chart "
                           "immediately, exactly as one written in the HMIS does.",
        }),
        ("Clinical record", {
            "fields": ("reason_for_visit", "chief_complaint", "note_text", "diagnosis", "plan"),
        }),
        ("Eye examination", {
            "classes": ("collapse",),
            "fields": ("eye_examination", "eye_examination_findings"),
            "description": "Structured findings, validated against clinical/eye_exam.py — "
                           "the same definition the chart's form is drawn from. Leave empty "
                           "on a note that is not an eye consultation.",
        }),
        ("Amendment", {
            "fields": ("amendment_reason", "amendment_detail"),
            "description": "Correcting a saved note archives what it said first. "
                           "Ignored when adding a new note.",
        }),
        ("Record provenance", {
            "fields": ("documented_by", "doctor", "created_at", "updated_at",
                       "amended", "is_locked", "locked_at"),
            "description": "Who documented this, and whether it has been corrected since. "
                           "The author is stamped from the signed-in account and never moves.",
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("amendments__amended_by")

    # --- who may use this door -------------------------------------------------
    #
    # The HMIS role decides, not a Django group: `User.is_admin` is superuser
    # or one of ADMIN_ROLES, the same test every administrative screen uses.
    # Django's own `is_staff` still gates the admin site itself, so this is a
    # narrowing of that, never a way around it.

    def _may_administer(self, request):
        return bool(getattr(request.user, "is_admin", False))

    def has_add_permission(self, request):
        return self._may_administer(request)

    def has_change_permission(self, request, obj=None):
        return self._may_administer(request)

    def has_view_permission(self, request, obj=None):
        # No Django-permission fallback. `view_consultationnote` granted to a
        # group would be a second RBAC deciding who reads clinical records,
        # beside the HMIS role that decides it everywhere else — and reading is
        # the access that matters most here: the note is the patient's history.
        return self._may_administer(request)

    def has_delete_permission(self, request, obj=None):
        # A clinical note is corrected by amendment and never deleted — the
        # same answer the HMIS gives.
        return False

    # --- writing ---------------------------------------------------------------

    def save_model(self, request, obj, form, change):
        """
        Creating goes straight through. **Correcting archives first.**

        Both call `clinical/services.py`, which is also what the chart's
        `ConsultationNoteViewSet` calls, so the two doors cannot drift apart
        about what an amendment is.
        """
        if not change:
            # The author is the signed-in administrator, never a clinician
            # picked from a list: `doctor` is who documented this, and letting
            # it be chosen would let one account file a note under another's
            # name. It is absent from the form for that reason.
            obj.doctor = request.user
            super().save_model(request, obj, form, change)
            note_services.audit_created(note=obj, actor=request.user, request=request,
                                        source="django-admin")
            return

        reason = form.cleaned_data.get("amendment_reason", "")
        detail = form.cleaned_data.get("amendment_detail", "")
        with transaction.atomic():
            # Read the note as the database still holds it: `obj` has already
            # been written over by the form, so the values the snapshot needs
            # are gone from it.
            before = note_services.note_before(obj)
            note_services.archive(note=before, actor=request.user, reason=reason, detail=detail)
            # The lock stands; the override is what the archive above earns.
            obj.save(admin_override=True)
        note_services.audit_amended(note=obj, actor=request.user, reason=reason, detail=detail,
                                    request=request, source="django-admin")

    # --- display ---------------------------------------------------------------

    @admin.display(description="Patient no.", ordering="patient__patient_number")
    def patient_number(self, obj):
        return obj.patient.patient_number

    @admin.display(description="Documented by")
    def documented_by(self, obj):
        if not obj.doctor:
            return "— stamped from your account when you save"
        name = obj.doctor.get_full_name() or obj.doctor.username
        return f"{name} ({obj.doctor.staff_number or 'no staff number'})"

    @admin.display(description="Eye exam", boolean=True)
    def has_eye_examination(self, obj):
        return bool(obj.eye_examination)

    def _latest(self, obj):
        return next(iter(obj.amendments.all()), None)

    @admin.display(description="Amended", boolean=True)
    def amended(self, obj):
        return self._latest(obj) is not None

    @admin.display(description="Last amended")
    def last_amended_at(self, obj):
        latest = self._latest(obj)
        return latest.created_at if latest else None

    @admin.display(description="Last amended by")
    def last_amended_by(self, obj):
        latest = self._latest(obj)
        if not latest or not latest.amended_by:
            return "—"
        return latest.amended_by.get_full_name() or latest.amended_by.username

    @admin.display(description="Eye examination")
    def eye_examination_findings(self, obj):
        """
        The stored examination read back through the catalogue's own labels
        (`clinical/eye_exam.describe`), rather than raw JSON — beside the
        field, which stays editable so a finding can be corrected.
        """
        findings = eye_exam.describe(obj.eye_examination)
        if not findings:
            return "—"
        return format_html("<dl>{}</dl>", format_html_join(
            "", "<dt><strong>{}</strong></dt><dd>{}</dd>", findings))


@admin.register(ConsultationNoteAmendment)
class ConsultationNoteAmendmentAdmin(LockedRecordAdmin):
    """
    The archive of what a note said before it was amended.

    Read behind the same gate as the note itself: these rows hold the note's
    own clinical text, so reading the archive *is* reading the record, and
    letting a Django group past it would be the hole `ConsultationNoteAdmin`
    closes seen from behind.
    """
    list_display = ["note", "created_at", "amended_by", "reason"]
    list_filter = ["created_at", "reason"]
    list_select_related = ["note", "note__patient", "amended_by"]
    search_fields = ["note__patient__patient_number", "detail"]

    def has_view_permission(self, request, obj=None):
        return bool(getattr(request.user, "is_admin", False))
