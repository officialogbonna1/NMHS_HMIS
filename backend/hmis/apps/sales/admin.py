from django.contrib import admin

from .models import Sale, SaleItem


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    fields = ["batch", "quantity", "unit_price", "discount_percent", "discount_reason", "approved_by"]


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    """
    Note: creating a SaleItem does not deduct stock — this app is not yet
    routed through pharmacy/services.py. Do not use it to move stock.
    """
    list_display = ["created_at", "patient", "sold_by", "total_amount"]
    list_filter = ["created_at"]
    search_fields = ["patient__first_name", "patient__last_name"]
    list_select_related = ["patient", "sold_by"]
    date_hierarchy = "created_at"
    inlines = [SaleItemInline]


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ["sale", "batch", "quantity", "unit_price", "discount_percent"]
    list_select_related = ["sale", "batch__item"]
