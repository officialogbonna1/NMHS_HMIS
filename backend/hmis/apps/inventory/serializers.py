from rest_framework import serializers
from .models import Item, Batch, StockMovement


class ItemSerializer(serializers.ModelSerializer):
    total_quantity = serializers.ReadOnlyField()
    is_low_stock = serializers.ReadOnlyField()

    class Meta:
        model = Item
        fields = ["id", "name", "category", "reorder_threshold", "unit", "total_quantity", "is_low_stock"]


class ItemForPrescribingSerializer(serializers.ModelSerializer):
    """
    Used by the doctor's drug picker. Deliberately hides raw quantities —
    doctors see availability, not exact counts (see design discussion).
    """
    available = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = ["id", "name", "category", "unit", "available"]

    def get_available(self, obj):
        return obj.total_quantity > 0


class BatchSerializer(serializers.ModelSerializer):
    is_expired = serializers.ReadOnlyField()
    item_name = serializers.CharField(source="item.name", read_only=True)
    item_unit = serializers.CharField(source="item.unit", read_only=True)

    class Meta:
        model = Batch
        fields = "__all__"
        # Quantity only moves through inventory.services (receive / count /
        # write-off / dispense) so every change leaves a StockMovement behind.
        read_only_fields = ["received_date"]


class StockMovementSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="batch.item.name", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    performed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = "__all__"
        read_only_fields = ["performed_by"]

    def get_performed_by_name(self, obj):
        return obj.performed_by.get_full_name() or obj.performed_by.username
