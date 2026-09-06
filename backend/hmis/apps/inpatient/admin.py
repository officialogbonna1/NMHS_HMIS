from django.contrib import admin

from apps.core.config import ProtectedConfigAdmin

from .models import Ward, Bed, Admission, BedTransfer, DischargeSummary


class BedInline(admin.TabularInline):
    model = Bed
    extra = 0
    fields = ["number", "is_active"]


@admin.register(Ward)
class WardAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """Wards — configuration, edited here or on the HMIS admin screens."""
    list_display = ["name", "department", "bed_count", "is_active"]
    list_editable = ["is_active"]
    list_filter = ["is_active", "department"]
    search_fields = ["name"]
    autocomplete_fields = ["department"]
    actions = ["activate", "deactivate"]
    protected_relations = ("beds",)

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} ward(s) activated.")

    @admin.action(description="Deactivate selected")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} ward(s) deactivated.")
    inlines = [BedInline]

    @admin.display(description="Beds")
    def bed_count(self, obj):
        return obj.beds.count()


@admin.register(Bed)
class BedAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """A bed somebody has occupied is part of the stay record: deactivate it."""
    list_display = ["ward", "number", "is_active", "occupied"]
    list_editable = ["is_active"]
    list_filter = ["ward", "is_active"]
    search_fields = ["number", "ward__name"]
    autocomplete_fields = ["ward"]
    actions = ["activate", "deactivate"]
    protected_relations = ("admissions", "transfers_out", "transfers_in")

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} bed(s) activated.")

    @admin.action(description="Deactivate selected")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} bed(s) deactivated.")
    list_select_related = ["ward"]

    @admin.display(description="Occupied", boolean=True)
    def occupied(self, obj):
        return obj.admissions.filter(status="admitted").exists()


class BedTransferInline(admin.TabularInline):
    model = BedTransfer
    extra = 0
    fields = ["from_bed", "to_bed", "transferred_by", "reason"]


@admin.register(Admission)
class AdmissionAdmin(admin.ModelAdmin):
    list_display = ["patient", "bed", "status", "attending_doctor", "admitted_at", "discharged_at"]
    list_filter = ["status", "admitted_at", "bed__ward"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number"]
    list_select_related = ["patient", "bed__ward", "attending_doctor"]
    autocomplete_fields = ["patient", "attending_doctor", "admitted_by"]
    date_hierarchy = "admitted_at"
    list_per_page = 50
    inlines = [BedTransferInline]


@admin.register(DischargeSummary)
class DischargeSummaryAdmin(admin.ModelAdmin):
    list_display = ["admission", "follow_up", "completed_by", "created_at"]
    search_fields = ["admission__patient__first_name", "admission__patient__last_name", "diagnosis"]
    list_select_related = ["admission__patient", "completed_by"]
    date_hierarchy = "created_at"


@admin.register(BedTransfer)
class BedTransferAdmin(admin.ModelAdmin):
    list_display = ["created_at", "admission", "from_bed", "to_bed", "transferred_by"]
    list_select_related = ["admission__patient", "from_bed", "to_bed", "transferred_by"]
    date_hierarchy = "created_at"
