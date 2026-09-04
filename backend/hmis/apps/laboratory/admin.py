"""
Laboratory in the Django admin.

The catalogue is edited here as freely as on the Laboratory Catalogue page —
it is configuration, and an admin should be able to fix a unit at 2am.

Results are not. A `LabResultValue` is kept in step with its order's status
and its amendment trail by `laboratory/services.py`; editing one here would
move the number without the audit row, which is exactly the drift the
service exists to prevent. Same rule the billing and vitals admins follow.
"""
from django.contrib import admin

from .models import (
    LabOrder, LabOrderTest, LabPanel, LabParameter, LabResultAmendment,
    LabResultValue, LabTest,
)


class LabParameterInline(admin.TabularInline):
    model = LabParameter
    extra = 0
    fields = ["display_order", "code", "name", "group", "result_type", "unit",
              "reference_range", "ref_low", "ref_high", "options", "is_required", "is_active"]
    ordering = ["display_order", "id"]


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "category", "specimen_type", "price", "parameter_count",
                    "is_active"]
    list_filter = ["category", "is_active"]
    search_fields = ["name", "code", "specimen_type"]
    inlines = [LabParameterInline]

    @admin.display(description="Parameters")
    def parameter_count(self, obj):
        return obj.parameters.filter(is_active=True).count()


@admin.register(LabParameter)
class LabParameterAdmin(admin.ModelAdmin):
    list_display = ["name", "test", "code", "result_type", "unit", "reference_range",
                    "is_required", "is_active"]
    list_filter = ["result_type", "is_required", "is_active", "test__category"]
    search_fields = ["name", "code", "test__name"]


@admin.register(LabPanel)
class LabPanelAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "test_count", "is_active"]
    filter_horizontal = ["tests"]
    search_fields = ["name", "code"]

    @admin.display(description="Tests")
    def test_count(self, obj):
        return obj.tests.count()


class LabOrderTestInline(admin.TabularInline):
    model = LabOrderTest
    extra = 0
    fields = ["test", "status", "comments", "performed_by", "performed_at"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "patient", "status", "priority", "requested_by",
                    "entered_by", "verified_by", "created_at"]
    list_filter = ["status", "priority"]
    search_fields = ["order_number", "specimen_id", "patient__last_name",
                     "patient__first_name", "patient__file_number"]
    readonly_fields = ["order_number", "entered_by", "entered_at", "verified_by",
                       "verified_at", "collected_by", "specimen_collected_at"]
    inlines = [LabOrderTestInline]

    # Results and their signatures are kept in step by laboratory/services.py.
    # Editing one here moves a value without its amendment row.
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LabOrderTest)
class LabOrderTestAdmin(admin.ModelAdmin):
    list_display = ["order", "test", "status", "performed_by", "performed_at"]
    list_filter = ["status", "test__category"]
    search_fields = ["order__order_number", "test__name"]

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LabResultValue)
class LabResultValueAdmin(admin.ModelAdmin):
    list_display = ["order_test", "parameter", "value", "flag", "recorded_by", "created_at"]
    list_filter = ["flag"]
    search_fields = ["order_test__order__order_number", "parameter__name", "value"]

    # A result is corrected on the bench page, which writes the amendment row.
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LabResultAmendment)
class LabResultAmendmentAdmin(admin.ModelAdmin):
    list_display = ["order_test", "parameter", "previous_value", "new_value",
                    "amended_by", "created_at"]
    search_fields = ["order_test__order__order_number", "parameter__name"]

    # The audit trail itself: readable, never editable.
    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return False
