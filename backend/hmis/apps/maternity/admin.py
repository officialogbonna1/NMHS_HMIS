from django.contrib import admin

from apps.core.config import ProtectedConfigAdmin

from .models import (Delivery, LabourEpisode, LabourObservation, MaternityEncounter,
                     MaternityOption, MaternityVisitType, Newborn, PostpartumVisit,
                     Pregnancy)


@admin.register(MaternityVisitType)
class MaternityVisitTypeAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """
    The clinics maternity runs — the same rows the HMIS Administration screen
    edits. One with attendances behind it is retired, never deleted: the
    history points at it.
    """
    list_display = ["name", "code", "is_booking", "display_order", "is_active", "encounter_count"]
    list_editable = ["display_order", "is_active"]
    list_filter = ["is_active", "is_booking"]
    search_fields = ["name", "code"]
    prepopulated_fields = {"code": ("name",)}
    ordering = ["display_order", "name"]
    actions = ["activate", "deactivate"]
    protected_relations = ("encounters",)

    @admin.display(description="Attendances")
    def encounter_count(self, obj):
        return obj.encounters.count()

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} visit type(s) activated.")

    @admin.action(description="Deactivate selected (history is kept)")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} visit type(s) retired.")


class MaternityEncounterInline(admin.TabularInline):
    """
    The visits of a pregnancy, read-only.

    An attendance is a record of a day: correcting it by typing over it is
    what stops ANC 2 being true, so this shows the course of the pregnancy and
    offers no way to rewrite it. New visits are opened through the maternity
    workspace, which stamps who recorded them.
    """
    model = MaternityEncounter
    extra = 0
    can_delete = False
    fields = ["seen_on", "visit_type", "provider", "status", "summary"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Pregnancy)
class PregnancyAdmin(admin.ModelAdmin):
    """
    One woman's episodes. Each is its own row and none of them is ever
    rewritten by the next — pregnancy #2 does not touch pregnancy #1.
    """
    list_display = ["patient", "number", "status", "outcome", "lmp", "edd", "ended_on"]
    list_filter = ["status", "outcome"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number"]
    list_select_related = ["patient"]
    autocomplete_fields = ["patient"]
    date_hierarchy = "created_at"
    readonly_fields = ["number", "opened_by"]
    inlines = [MaternityEncounterInline]


@admin.register(MaternityEncounter)
class MaternityEncounterAdmin(admin.ModelAdmin):
    """
    Every maternity attendance, newest first. Read-only for the reason the
    inline is: a visit is what happened that day.
    """
    list_display = ["seen_on", "pregnancy", "visit_type", "provider", "status"]
    list_filter = ["visit_type", "status", "seen_on"]
    search_fields = ["pregnancy__patient__first_name", "pregnancy__patient__last_name",
                     "pregnancy__patient__patient_number"]
    list_select_related = ["pregnancy__patient", "visit_type", "provider"]
    date_hierarchy = "seen_on"

    def has_change_permission(self, request, obj=None):
        # Kept in step by `maternity/services.py`, which stamps who recorded
        # each one — the rule every service-owned record in this HMIS follows.
        return False


@admin.register(MaternityOption)
class MaternityOptionAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """
    The hospital's maternity vocabulary. One list per `kind`, all in one
    table — five near-identical models would be five of this class.

    A word a record has used is retired, never deleted: the delivery note that
    says "Assisted vaginal delivery" has to keep saying it.
    """
    list_display = ["name", "kind", "code", "display_order", "is_active"]
    list_editable = ["display_order", "is_active"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name", "code"]
    ordering = ["kind", "display_order", "name"]
    actions = ["activate", "deactivate"]
    protected_relations = ("deliveries_of_type", "deliveries_with_outcome",
                           "deliveries_with_complication", "newborns_with_status",
                           "deliveries_with_maternal_condition",
                           "postpartum_with_condition", "postpartum_with_family_planning")

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} option(s) activated.")

    @admin.action(description="Deactivate selected (records keep the word)")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} option(s) retired.")


class LabourObservationInline(admin.TabularInline):
    """The partogram, read-only: a reading is what was measured at that time."""
    model = LabourObservation
    extra = 0
    can_delete = False
    fields = ["observed_at", "cervical_dilation_cm", "contractions_per_10min",
              "fetal_heart_rate", "membranes", "maternal_condition", "recorded_by"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(LabourEpisode)
class LabourEpisodeAdmin(admin.ModelAdmin):
    """
    One labour per row, with its partogram beneath it. The ward and the bed
    are read through the admission (`inpatient.Admission`) rather than stored
    here, so a bed transfer moves this record with it.
    """
    list_display = ["started_at", "patient", "pregnancy", "onset", "stage", "status", "where"]
    list_filter = ["status", "stage", "onset", "started_at"]
    search_fields = ["pregnancy__patient__first_name", "pregnancy__patient__last_name",
                     "pregnancy__patient__patient_number"]
    list_select_related = ["pregnancy__patient", "admission__bed__ward"]
    date_hierarchy = "started_at"
    readonly_fields = ["opened_by"]
    inlines = [LabourObservationInline]

    @admin.display(description="Patient")
    def patient(self, obj):
        return obj.pregnancy.patient

    @admin.display(description="Ward / bed")
    def where(self, obj):
        return f"{obj.ward.name} · {obj.bed.number}" if obj.bed else "—"


class NewbornInline(admin.TabularInline):
    """
    The babies of this delivery. **Twins are two rows here** — never two
    deliveries and never two pregnancies.
    """
    model = Newborn
    extra = 0
    fields = ["birth_order", "name", "sex", "birth_weight_grams",
              "apgar_1_min", "apgar_5_min", "status", "admitted_to_nursery"]


class PostpartumInline(admin.TabularInline):
    model = PostpartumVisit
    extra = 0
    fields = ["seen_at", "mother_condition", "bleeding", "breastfeeding_established",
              "family_planning", "follow_up_on"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    """One delivery per labour, with its babies and postpartum checks."""
    list_display = ["delivered_at", "patient", "delivery_type", "outcome", "baby_count"]
    list_filter = ["delivery_type", "outcome", "delivered_at"]
    search_fields = ["labour__pregnancy__patient__first_name",
                     "labour__pregnancy__patient__last_name",
                     "labour__pregnancy__patient__patient_number"]
    list_select_related = ["labour__pregnancy__patient", "delivery_type", "outcome"]
    date_hierarchy = "delivered_at"
    filter_horizontal = ["complications"]
    readonly_fields = ["labour", "recorded_by"]
    inlines = [NewbornInline, PostpartumInline]

    @admin.display(description="Babies")
    def baby_count(self, obj):
        return obj.newborns.count()


@admin.register(Newborn)
class NewbornAdmin(admin.ModelAdmin):
    """The birth register."""
    list_display = ["delivery", "birth_order", "name", "sex", "birth_weight_grams",
                    "apgar_5_min", "status"]
    list_filter = ["sex", "status", "admitted_to_nursery"]
    search_fields = ["name", "delivery__labour__pregnancy__patient__patient_number"]
    list_select_related = ["delivery__labour__pregnancy__patient", "status"]
    autocomplete_fields = ["patient"]


@admin.register(PostpartumVisit)
class PostpartumVisitAdmin(admin.ModelAdmin):
    list_display = ["seen_at", "delivery", "mother_condition", "breastfeeding_established",
                    "follow_up_on"]
    list_filter = ["mother_condition", "family_planning", "seen_at"]
    list_select_related = ["delivery__labour__pregnancy__patient", "mother_condition"]
    date_hierarchy = "seen_at"
