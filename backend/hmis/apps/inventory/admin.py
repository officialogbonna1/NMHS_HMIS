from django.contrib import admin, messages

from apps.core.config import ProtectedConfigAdmin

from .models import (
    Batch, Item, ItemCategory, StockCount, StockCountLine, StockLocation, StockMovement,
    StockRecord, StockTransfer, StockTransferLine, UnitOfMeasure,
)

# Two rules the admin classes here follow, the same two the billing and
# laboratory admins do:
#
# 1. Anything a service owns is read-only. Stock quantities, movements,
#    transfers and counts are kept in step by inventory/pharmacy services —
#    editing one through the admin would move a number without the
#    `StockMovement` that explains it, and `StockRecord.save()` refuses it
#    outright. So those admins are for reading, and say why.
# 2. Configuration is editable — that is how a category, a unit or a third
#    location gets added without a deployment. It is the same row the HMIS
#    administration screens edit; there is no second copy.
# 3. A configuration row that history points at is deactivated, not deleted
#    (`ProtectedConfigAdmin`), so Django admin gives the same answer the API
#    does instead of a database error or a silent cascade.


@admin.register(ItemCategory)
class ItemCategoryAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """How the catalogue is grouped. Retired once products are filed under it."""
    list_display = ["name", "item_count", "is_active", "display_order", "description"]
    list_editable = ["is_active", "display_order"]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]
    ordering = ["display_order", "name"]
    actions = ["activate", "deactivate"]
    protected_relations = ("items",)

    @admin.display(description="Products")
    def item_count(self, obj):
        return obj.item_count

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} activated.")

    @admin.action(description="Deactivate selected (keeps history)")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} deactivated.")


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """Tablets, bottles, vials — what a product is counted and labelled in."""
    list_display = ["name", "abbreviation", "item_count", "is_active", "display_order"]
    list_editable = ["abbreviation", "is_active", "display_order"]
    list_filter = ["is_active"]
    search_fields = ["name", "abbreviation", "description"]
    ordering = ["display_order", "name"]
    actions = ["activate", "deactivate"]
    protected_relations = ("items",)

    @admin.display(description="Products")
    def item_count(self, obj):
        return obj.item_count

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} activated.")

    @admin.action(description="Deactivate selected (keeps history)")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} deactivated.")


class StockRecordInline(admin.TabularInline):
    """Where this lot is standing, and how much of it. Read-only: stock moves
    by receipt, transfer, count, write-off or dispense."""
    model = StockRecord
    extra = 0
    fields = ["location", "quantity"]
    readonly_fields = ["location", "quantity"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StockLocation)
class StockLocationAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """
    The places stock stands. Main Store and Pharmacy are seeded; adding a
    third (a theatre store, a ward cupboard) is a row here.

    The two flags are the workflow: deliveries land in the location marked
    *default receiving*, and only the location marked *dispensing point* can
    be dispensed to patients from.
    """
    list_display = ["name", "code", "kind", "is_default_receiving",
                    "is_dispensing_point", "is_active", "total_units"]
    list_editable = ["is_active"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name", "code"]
    ordering = ["display_order", "name"]
    # A location that has held stock is part of the ledger.
    protected_relations = ("stock", "movements", "transfers_out", "transfers_in", "counts")
    fieldsets = [
        (None, {"fields": ["code", "name", "kind", "description"]}),
        ("What this location is for", {
            "fields": ["is_default_receiving", "is_dispensing_point"],
            "description": "Deliveries land in the receiving location; patients are "
                           "dispensed to only from the dispensing point. Moving either "
                           "flag changes where every delivery and every prescription goes.",
        }),
        ("Listing", {"fields": ["is_active", "display_order"]}),
    ]

    @admin.display(description="Units on hand")
    def total_units(self, obj):
        return obj.total_units


@admin.register(Item)
class ItemAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """
    The product catalogue — the same rows the HMIS administration screens
    edit. Quantities are not here: they are per location, on Stock records,
    and move only through the services.
    """
    list_display = ["name", "category", "unit", "on_hand", "reorder_threshold",
                    "needs_reorder", "is_active"]
    list_editable = ["is_active", "reorder_threshold"]
    list_filter = ["category", "unit", "is_active"]
    search_fields = ["name", "category__name"]
    ordering = ["name"]
    list_select_related = ["category", "unit"]
    autocomplete_fields = ["category", "unit"]
    list_per_page = 50
    actions = ["activate", "deactivate"]
    # A product with stock or prescriptions behind it is deactivated, never
    # deleted, or every movement and bill naming it loses its subject.
    protected_relations = ("batches", "prescription_set")
    fieldsets = [
        (None, {"fields": ["name", "category", "unit", "is_active"]}),
        ("Reordering", {
            "fields": ["reorder_threshold"],
            "description": "The hospital-wide total below which this product is "
                           "flagged as low on the dashboards.",
        }),
    ]

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} product(s) activated.")

    @admin.action(description="Deactivate selected (keeps history)")
    def deactivate(self, request, queryset):
        self.message_user(request,
                          f"{queryset.update(is_active=False)} product(s) deactivated.",
                          messages.WARNING)

    @admin.display(description="On hand (all locations)")
    def on_hand(self, obj):
        return obj.total_quantity

    @admin.display(description="Reorder", boolean=True)
    def needs_reorder(self, obj):
        return obj.total_quantity <= obj.reorder_threshold


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    """
    A lot: what it is, when it expires, what it cost. **Not** how much there
    is — that is per location, on the Stock records below, and it moves only
    through the inventory services.
    """
    list_display = ["item", "batch_no", "on_hand", "expiry_date", "cost_price",
                    "sale_price", "supplier"]
    list_filter = ["expiry_date", "item__category", "supplier"]
    search_fields = ["item__name", "batch_no", "supplier"]
    list_select_related = ["item"]
    autocomplete_fields = ["item"]
    date_hierarchy = "expiry_date"
    list_per_page = 50
    inlines = [StockRecordInline]
    readonly_fields = ["received_date"]
    fieldsets = [
        (None, {"fields": ["item", "batch_no", "supplier", "received_date"]}),
        ("Dates", {"fields": ["expiry_date"]}),
        ("Money", {"fields": ["cost_price", "sale_price"]}),
    ]

    @admin.display(description="On hand (all locations)")
    def on_hand(self, obj):
        return obj.total_quantity


@admin.register(StockRecord)
class StockRecordAdmin(admin.ModelAdmin):
    """
    Product + batch + location. Read-only: the model itself refuses a
    quantity change that did not come from a service, so a form here could
    only ever fail — use a receipt, transfer, count or write-off.
    """
    list_display = ["batch", "location", "quantity", "expiry"]
    list_filter = ["location", "batch__item__category"]
    search_fields = ["batch__item__name", "batch__batch_no"]
    list_select_related = ["batch__item", "location"]
    list_per_page = 100

    @admin.display(description="Expires", ordering="batch__expiry_date")
    def expiry(self, obj):
        return obj.batch.expiry_date

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class StockTransferLineInline(admin.TabularInline):
    model = StockTransferLine
    extra = 0
    fields = ["batch", "quantity"]
    readonly_fields = ["batch", "quantity"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    """A completed internal movement. Applied atomically when it was created;
    there is nothing to edit afterwards."""
    list_display = ["reference", "source", "destination", "total_units",
                    "transferred_by", "created_at"]
    list_filter = ["source", "destination", "created_at"]
    search_fields = ["reference", "note", "lines__batch__batch_no", "lines__batch__item__name"]
    list_select_related = ["source", "destination", "transferred_by"]
    date_hierarchy = "created_at"
    inlines = [StockTransferLineInline]

    @admin.display(description="Units")
    def total_units(self, obj):
        return obj.total_units

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class StockCountLineInline(admin.TabularInline):
    model = StockCountLine
    extra = 0
    fields = ["batch", "system_quantity", "counted_quantity", "difference"]
    readonly_fields = ["batch", "system_quantity", "counted_quantity", "difference"]
    can_delete = False

    @admin.display(description="Difference")
    def difference(self, obj):
        return obj.difference

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StockCount)
class StockCountAdmin(admin.ModelAdmin):
    """A physical inventory of one location, with what the system believed at
    the time. Frozen: the adjustments it posted are in the movement log."""
    list_display = ["reference", "location", "counted_by", "discrepancies", "created_at"]
    list_filter = ["location", "created_at"]
    search_fields = ["reference", "note"]
    list_select_related = ["location", "counted_by"]
    date_hierarchy = "created_at"
    inlines = [StockCountLineInline]

    @admin.display(description="Lines differing")
    def discrepancies(self, obj):
        return obj.discrepancy_count

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StockCountLine)
class StockCountLineAdmin(admin.ModelAdmin):
    """The count sheet itself: product, batch, location, system, counted,
    difference — the one place all six read across in a single table."""
    list_display = ["count", "batch", "location", "system_quantity",
                    "counted_quantity", "difference"]
    list_filter = ["count__location"]
    search_fields = ["batch__item__name", "batch__batch_no", "count__reference"]
    list_select_related = ["count__location", "batch__item"]
    list_per_page = 100

    @admin.display(description="Location")
    def location(self, obj):
        return obj.count.location

    @admin.display(description="Difference")
    def difference(self, obj):
        return obj.difference

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    """Why every quantity is what it is. Read-only, like the audit log."""
    list_display = ["created_at", "batch", "location", "change", "reason",
                    "performed_by", "reference"]
    list_filter = ["reason", "location", "created_at"]
    search_fields = ["batch__item__name", "batch__batch_no", "reference"]
    list_select_related = ["batch__item", "location", "performed_by"]
    date_hierarchy = "created_at"
    list_per_page = 100

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockTransferLine)
class StockTransferLineAdmin(admin.ModelAdmin):
    """Registered so a line is reachable on its own — "where did batch PCM001
    go?" is a search here. Read-only, like the transfer it belongs to."""
    list_display = ["transfer", "batch", "quantity"]
    search_fields = ["batch__item__name", "batch__batch_no", "transfer__reference"]
    list_select_related = ["transfer", "batch__item"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
