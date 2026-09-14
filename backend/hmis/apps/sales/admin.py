from django.contrib import admin

from .models import PosRegister, Sale, SaleItem, SaleLine, SaleReturn, SaleReturnLine

# Everything here is written by `sales/services.py` in one transaction with its
# stock movements and its payment. Editing a total, a batch or a quantity here
# would move one of those without the others, so these admins read and never
# write — the same rule the billing and inventory admins follow.


class _ReadOnly:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SaleLineInline(_ReadOnly, admin.TabularInline):
    model = SaleLine
    extra = 0
    fields = ["item", "quantity", "unit_price", "gross", "discount", "net"]
    readonly_fields = fields


class SaleItemInline(_ReadOnly, admin.TabularInline):
    model = SaleItem
    extra = 0
    fields = ["line", "batch", "quantity", "unit_price"]
    readonly_fields = fields


@admin.register(PosRegister)
class PosRegisterAdmin(_ReadOnly, admin.ModelAdmin):
    list_display = ["reference", "opened_by", "status", "opening_float", "created_at",
                    "closed_at", "expected_cash", "counted_cash", "variance"]
    list_filter = ["status", "location", "created_at"]
    search_fields = ["reference", "opened_by__username", "opened_by__first_name",
                     "opened_by__last_name"]
    list_select_related = ["opened_by", "location"]
    date_hierarchy = "created_at"


@admin.register(Sale)
class SaleAdmin(_ReadOnly, admin.ModelAdmin):
    list_display = ["reference", "status", "customer_type", "patient", "customer_name", "sold_by",
                    "subtotal", "discount_amount", "total_amount", "payment_method", "completed_at"]
    list_filter = ["status", "customer_type", "payment_method", "created_at"]
    search_fields = ["reference", "customer_name", "customer_phone", "patient__first_name",
                     "patient__last_name", "patient__patient_number"]
    list_select_related = ["patient", "sold_by"]
    date_hierarchy = "created_at"
    inlines = [SaleLineInline, SaleItemInline]


@admin.register(SaleLine)
class SaleLineAdmin(_ReadOnly, admin.ModelAdmin):
    list_display = ["sale", "item", "quantity", "unit_price", "gross", "discount", "net"]
    search_fields = ["sale__reference", "item__name", "item__sku"]
    list_select_related = ["sale", "item"]


@admin.register(SaleItem)
class SaleItemAdmin(_ReadOnly, admin.ModelAdmin):
    """The batch each sold unit came off — what a recall searches."""
    list_display = ["sale", "batch", "quantity", "unit_price"]
    search_fields = ["sale__reference", "batch__batch_no", "batch__item__name"]
    list_select_related = ["sale", "batch__item"]


class SaleReturnLineInline(_ReadOnly, admin.TabularInline):
    model = SaleReturnLine
    extra = 0
    fields = ["sale_item", "quantity", "amount"]
    readonly_fields = fields


@admin.register(SaleReturn)
class SaleReturnAdmin(_ReadOnly, admin.ModelAdmin):
    list_display = ["reference", "sale", "amount", "refund_method", "processed_by", "created_at"]
    search_fields = ["reference", "sale__reference", "reason"]
    list_select_related = ["sale", "processed_by"]
    date_hierarchy = "created_at"
    inlines = [SaleReturnLineInline]


@admin.register(SaleReturnLine)
class SaleReturnLineAdmin(_ReadOnly, admin.ModelAdmin):
    list_display = ["sale_return", "sale_item", "quantity", "amount"]
    list_select_related = ["sale_return", "sale_item__batch"]
