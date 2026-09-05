"""
What the stock API exposes, and — more importantly — what it refuses.

`BatchSerializer` used to be `fields = "__all__"` with only `received_date`
read-only, which meant `PATCH /api/batches/<id>/ {"quantity": 9999}` was a
valid request: stock could be conjured with no movement behind it, and the
comment claiming otherwise was only a comment. There is now no writable
quantity anywhere in this file. Quantities are read from `StockRecord` and
moved only by the services, and the model refuses anything else.
"""
from rest_framework import serializers

from .models import (
    Batch, Item, ItemCategory, StockCount, StockCountLine, StockLocation, StockMovement,
    StockRecord, StockTransfer, StockTransferLine, UnitOfMeasure,
)


class ItemCategorySerializer(serializers.ModelSerializer):
    """How the catalogue is grouped. Configuration — one row per group."""
    item_count = serializers.ReadOnlyField()

    class Meta:
        model = ItemCategory
        fields = ["id", "name", "description", "is_active", "display_order", "item_count"]


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    """Tablets, bottles, vials. `label` is what a label actually prints."""
    item_count = serializers.ReadOnlyField()
    label = serializers.ReadOnlyField()

    class Meta:
        model = UnitOfMeasure
        fields = ["id", "name", "abbreviation", "label", "description",
                  "is_active", "display_order", "item_count"]


class StockLocationSerializer(serializers.ModelSerializer):
    total_units = serializers.ReadOnlyField()

    class Meta:
        model = StockLocation
        fields = ["id", "code", "name", "kind", "description", "is_default_receiving",
                  "is_dispensing_point", "is_active", "display_order", "total_units"]
        # The two workflow flags are configuration, not something a stock
        # screen toggles in passing: moving the dispensing point changes
        # where every prescription draws from.
        read_only_fields = ["is_default_receiving", "is_dispensing_point"]


class StockRecordSerializer(serializers.ModelSerializer):
    """Product + batch + location + quantity. Read-only, always."""
    item = serializers.IntegerField(source="batch.item_id", read_only=True)
    item_name = serializers.CharField(source="batch.item.name", read_only=True)
    item_unit = serializers.CharField(source="batch.item.unit_label", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    expiry_date = serializers.DateField(source="batch.expiry_date", read_only=True)
    is_expired = serializers.ReadOnlyField()
    location_code = serializers.CharField(source="location.code", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    sale_price = serializers.DecimalField(source="batch.sale_price", max_digits=10,
                                          decimal_places=2, read_only=True)
    cost_price = serializers.DecimalField(source="batch.cost_price", max_digits=10,
                                          decimal_places=2, read_only=True)

    class Meta:
        model = StockRecord
        fields = ["id", "batch", "batch_no", "item", "item_name", "item_unit",
                  "location", "location_code", "location_name", "quantity",
                  "expiry_date", "is_expired", "sale_price", "cost_price"]
        read_only_fields = fields


class ItemSerializer(serializers.ModelSerializer):
    total_quantity = serializers.ReadOnlyField()
    is_low_stock = serializers.ReadOnlyField()
    # The relation for editing, the name for reading. Both, because the
    # administration form posts an id and every list wants a word.
    category_name = serializers.CharField(source="category.name", read_only=True, default="")
    unit_name = serializers.CharField(source="unit.name", read_only=True, default="")
    unit_label = serializers.ReadOnlyField()
    batch_count = serializers.SerializerMethodField()
    # Where this item's stock is standing, so a stock screen can show
    # "Main Store 400 · Pharmacy 100" without a request per location.
    by_location = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = ["id", "name", "category", "category_name", "reorder_threshold",
                  "unit", "unit_name", "unit_label", "is_active", "batch_count",
                  "total_quantity", "is_low_stock", "by_location"]

    def get_batch_count(self, obj):
        # What makes a product undeletable — shown so the administration
        # screen can say why before the API refuses.
        return obj.batches.count()

    def get_by_location(self, obj):
        totals = {}
        for record in StockRecord.objects.filter(batch__item=obj).select_related("location"):
            entry = totals.setdefault(record.location.code, {
                "location": record.location_id,
                "code": record.location.code,
                "name": record.location.name,
                "quantity": 0,
            })
            entry["quantity"] += record.quantity
        return list(totals.values())


class ItemForPrescribingSerializer(serializers.ModelSerializer):
    """
    Used by the doctor's drug picker. Deliberately hides raw quantities —
    doctors see availability, not exact counts (design rule 7).

    "Available" means **available at the pharmacy**, not somewhere in the
    building: a drug sitting in the Main Store cannot be handed to a patient
    until it is transferred, so showing it as available would have the doctor
    prescribe something the counter has to refuse.
    """
    available = serializers.SerializerMethodField()
    # Strings, not ids: this is a picker, and the doctor reads
    # "Analgesics · per tablet". Rule 7 still holds — no quantities.
    category = serializers.CharField(source="category_name", read_only=True)
    unit = serializers.CharField(source="unit_label", read_only=True)

    class Meta:
        model = Item
        fields = ["id", "name", "category", "unit", "available"]

    def get_available(self, obj):
        from apps.pharmacy.services import available_quantity
        return available_quantity(obj) > 0


class BatchSerializer(serializers.ModelSerializer):
    """
    A lot: identity, expiry, price. Quantities are reported per location and
    are never writable here — see the module docstring.

    `opening_quantity` is write-only and is the delivery being received: the
    viewset hands it to `receive_stock()`, which puts the units in a location
    and writes the movement. It is not a field on the model, so it cannot be
    PATCHed later to invent stock.
    """
    is_expired = serializers.ReadOnlyField()
    item_name = serializers.CharField(source="item.name", read_only=True)
    item_unit = serializers.CharField(source="item.unit_label", read_only=True)
    total_quantity = serializers.ReadOnlyField()
    stock = StockRecordSerializer(many=True, read_only=True)

    opening_quantity = serializers.IntegerField(write_only=True, required=False, min_value=1)
    # The name the receiving form has always posted. Kept as an alias because
    # dropping it would have an existing caller create a batch holding
    # nothing and say 201 — a silent zero is the worst way to fail at stock
    # control. Both names mean the same thing; `opening_quantity` is the one
    # to write new callers against.
    quantity = serializers.IntegerField(write_only=True, required=False, min_value=1)
    location = serializers.PrimaryKeyRelatedField(
        queryset=StockLocation.objects.filter(is_active=True),
        write_only=True, required=False,
        help_text="Where the delivery lands. Defaults to the receiving location (Main Store).",
    )

    class Meta:
        model = Batch
        fields = ["id", "item", "item_name", "item_unit", "batch_no", "cost_price",
                  "sale_price", "expiry_date", "supplier", "received_date",
                  "is_expired", "total_quantity", "stock",
                  "opening_quantity", "quantity", "location"]
        read_only_fields = ["received_date"]


class StockMovementSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="batch.item.name", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    location_code = serializers.CharField(source="location.code", read_only=True)
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)
    transfer_reference = serializers.CharField(source="transfer.reference", read_only=True)
    performed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = "__all__"
        # The whole row is history. It is written by the services beside the
        # quantity it explains, never posted.
        read_only_fields = [f.name for f in StockMovement._meta.fields]

    def get_performed_by_name(self, obj):
        return obj.performed_by.get_full_name() or obj.performed_by.username


class StockTransferLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="batch.item.name", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    expiry_date = serializers.DateField(source="batch.expiry_date", read_only=True)

    class Meta:
        model = StockTransferLine
        fields = ["id", "batch", "batch_no", "item_name", "expiry_date", "quantity"]
        read_only_fields = fields


class StockTransferSerializer(serializers.ModelSerializer):
    lines = StockTransferLineSerializer(many=True, read_only=True)
    source_name = serializers.CharField(source="source.name", read_only=True)
    destination_name = serializers.CharField(source="destination.name", read_only=True)
    transferred_by_name = serializers.SerializerMethodField()
    total_units = serializers.ReadOnlyField()

    class Meta:
        model = StockTransfer
        fields = ["id", "reference", "source", "source_name", "destination",
                  "destination_name", "note", "transferred_by", "transferred_by_name",
                  "total_units", "lines", "created_at"]
        # A transfer is a completed fact. It is created through the service,
        # which applies it; there is nothing about it to edit afterwards.
        read_only_fields = ["reference", "transferred_by", "created_at"]

    def get_transferred_by_name(self, obj):
        user = obj.transferred_by
        return (user.get_full_name() or user.username) if user else None


class StockCountLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="batch.item.name", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    expiry_date = serializers.DateField(source="batch.expiry_date", read_only=True)
    difference = serializers.ReadOnlyField()

    class Meta:
        model = StockCountLine
        fields = ["id", "batch", "batch_no", "item_name", "expiry_date",
                  "system_quantity", "counted_quantity", "difference"]
        read_only_fields = fields


class StockCountSerializer(serializers.ModelSerializer):
    lines = StockCountLineSerializer(many=True, read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    counted_by_name = serializers.SerializerMethodField()
    discrepancy_count = serializers.ReadOnlyField()

    class Meta:
        model = StockCount
        fields = ["id", "reference", "location", "location_name", "note",
                  "counted_by", "counted_by_name", "discrepancy_count", "lines",
                  "created_at"]
        read_only_fields = ["reference", "counted_by", "created_at"]

    def get_counted_by_name(self, obj):
        user = obj.counted_by
        return (user.get_full_name() or user.username) if user else None
