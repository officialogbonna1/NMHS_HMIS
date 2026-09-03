from django.contrib import admin

from .models import Item, Batch, StockMovement


class BatchInline(admin.TabularInline):
    model = Batch
    extra = 0
    fields = ["batch_no", "quantity", "cost_price", "sale_price", "expiry_date", "supplier"]
    show_change_link = True


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "unit", "on_hand", "reorder_threshold", "needs_reorder"]
    list_filter = ["category"]
    search_fields = ["name", "category"]
    ordering = ["name"]
    list_per_page = 50
    inlines = [BatchInline]

    @admin.display(description="On hand")
    def on_hand(self, obj):
        return sum(b.quantity for b in obj.batches.all())

    @admin.display(description="Reorder", boolean=True)
    def needs_reorder(self, obj):
        return self.on_hand(obj) <= obj.reorder_threshold


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    """
    Quantities are moved by inventory/pharmacy services so every change
    leaves a StockMovement. Editing `quantity` here writes no audit row —
    use the Inventory screens unless you are correcting bad data knowingly.
    """
    list_display = ["item", "batch_no", "quantity", "expiry_date", "sale_price", "supplier"]
    list_filter = ["expiry_date", "item__category"]
    search_fields = ["item__name", "batch_no", "supplier"]
    list_select_related = ["item"]
    autocomplete_fields = ["item"]
    date_hierarchy = "expiry_date"
    list_per_page = 50


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    """Why every quantity is what it is. Read-only, like the audit log."""
    list_display = ["created_at", "batch", "change", "reason", "performed_by", "reference"]
    list_filter = ["reason", "created_at"]
    search_fields = ["batch__item__name", "batch__batch_no", "reference"]
    list_select_related = ["batch__item", "performed_by"]
    date_hierarchy = "created_at"
    list_per_page = 100

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
