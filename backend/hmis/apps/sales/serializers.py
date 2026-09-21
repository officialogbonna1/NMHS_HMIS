"""
What the POS API reads back, and the shapes it accepts.

Every read serializer is read-only: a sale, a register and a return are written
by `sales/services.py` and nothing else.
"""
from decimal import Decimal

from rest_framework import serializers

from apps.billing.models import Payment

from .models import PosRegister, Sale, SaleItem, SaleLine, SaleReturn, SaleReturnLine


def _name(user):
    return (user.get_full_name() or user.username) if user else None


class PosRegisterSerializer(serializers.ModelSerializer):
    opened_by_name = serializers.SerializerMethodField()
    closed_by_name = serializers.SerializerMethodField()
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = PosRegister
        fields = ["id", "reference", "status", "location", "location_name", "opened_by",
                  "opened_by_name", "opening_float", "created_at", "closed_by", "closed_by_name",
                  "closed_at", "expected_cash", "counted_cash", "variance", "closing_note",
                  "closing_summary"]
        read_only_fields = fields

    def get_opened_by_name(self, obj):
        return _name(obj.opened_by)

    def get_closed_by_name(self, obj):
        return _name(obj.closed_by)


class SaleItemSerializer(serializers.ModelSerializer):
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    expiry_date = serializers.DateField(source="batch.expiry_date", read_only=True)
    returned_quantity = serializers.IntegerField(read_only=True)
    returnable_quantity = serializers.IntegerField(read_only=True)

    class Meta:
        model = SaleItem
        fields = ["id", "batch", "batch_no", "expiry_date", "quantity", "unit_price",
                  "returned_quantity", "returnable_quantity"]
        read_only_fields = fields


class SaleLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    sku = serializers.SerializerMethodField()
    unit_label = serializers.CharField(source="item.unit_label", read_only=True)
    pieces = SaleItemSerializer(many=True, read_only=True)

    class Meta:
        model = SaleLine
        fields = ["id", "item", "item_name", "sku", "unit_label", "quantity", "unit_price",
                  "gross", "line_discount_type", "line_discount_value", "line_discount",
                  "discount", "net", "pieces"]
        read_only_fields = fields

    def get_sku(self, obj):
        return obj.item.sku or ""


class SaleReturnLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="sale_item.batch.item.name", read_only=True)
    batch_no = serializers.CharField(source="sale_item.batch.batch_no", read_only=True)

    class Meta:
        model = SaleReturnLine
        fields = ["id", "sale_item", "item_name", "batch_no", "quantity", "amount"]
        read_only_fields = fields


class SaleReturnSerializer(serializers.ModelSerializer):
    sale_reference = serializers.CharField(source="sale.reference", read_only=True)
    customer_label = serializers.CharField(source="sale.customer_label", read_only=True)
    customer_type = serializers.CharField(source="sale.customer_type", read_only=True)
    register_reference = serializers.CharField(source="register.reference", read_only=True)
    refund_method_label = serializers.CharField(source="get_refund_method_display", read_only=True)
    processed_by_name = serializers.SerializerMethodField()
    lines = SaleReturnLineSerializer(many=True, read_only=True)

    class Meta:
        model = SaleReturn
        fields = ["id", "reference", "sale", "sale_reference", "customer_label", "customer_type",
                  "register", "register_reference",
                  "reason", "amount", "refund_method", "refund_method_label", "refund",
                  "processed_by", "processed_by_name", "lines", "created_at"]
        read_only_fields = fields

    def get_processed_by_name(self, obj):
        return _name(obj.processed_by)


class SaleSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    customer_type_label = serializers.CharField(source="get_customer_type_display", read_only=True)
    customer_label = serializers.CharField(read_only=True)
    payment_method_label = serializers.CharField(source="get_payment_method_display", read_only=True)
    patient_name = serializers.SerializerMethodField()
    patient_number = serializers.SerializerMethodField()
    patient_uuid = serializers.SerializerMethodField()
    sold_by_name = serializers.SerializerMethodField()
    discount_by_name = serializers.SerializerMethodField()
    discount_approved_by_name = serializers.SerializerMethodField()
    register_reference = serializers.SerializerMethodField()
    amount_returned = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    sale_discount_amount = serializers.SerializerMethodField()
    lines = SaleLineSerializer(many=True, read_only=True)
    returns = SaleReturnSerializer(many=True, read_only=True)

    class Meta:
        model = Sale
        fields = ["id", "reference", "status", "status_label", "register", "register_reference",
                  "customer_type", "customer_type_label", "customer_label", "patient",
                  "patient_name", "patient_number", "patient_uuid", "customer_name",
                  "customer_phone", "sold_by", "sold_by_name", "subtotal", "discount_type",
                  "discount_value", "discount_amount", "discount_reason", "discount_by",
                  "discount_by_name", "discount_approved_by", "discount_approved_by_name",
                  "discount_at", "sale_discount_amount", "total_amount", "payment_method",
                  "payment_method_label", "amount_tendered", "change_due", "payment", "charge",
                  "amount_returned", "created_at", "completed_at", "cancelled_at", "lines",
                  "returns"]
        read_only_fields = fields

    def get_sale_discount_amount(self, obj):
        """The sale-wide part of `discount_amount`; the rest sits on the lines."""
        on_lines = sum((line.line_discount for line in obj.lines.all()), Decimal("0"))
        return f"{obj.discount_amount - on_lines:.2f}"

    def get_discount_approved_by_name(self, obj):
        """Named only where the policy actually required an authorisation."""
        user = obj.discount_approved_by
        return (user.get_full_name() or user.username) if user else None

    def get_patient_name(self, obj):
        return obj.patient.display_name if obj.patient_id else None

    def get_patient_number(self, obj):
        return obj.patient.patient_number if obj.patient_id else None

    def get_patient_uuid(self, obj):
        return str(obj.patient.uuid) if obj.patient_id else None

    def get_sold_by_name(self, obj):
        return _name(obj.sold_by)

    def get_discount_by_name(self, obj):
        return _name(obj.discount_by)

    def get_register_reference(self, obj):
        return obj.register.reference if obj.register_id else None


# ------------------------------------------------------------------ inputs


class LineDiscountInput(serializers.Serializer):
    type = serializers.ChoiceField(choices=Sale.DISCOUNT)
    value = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0"))


class CartLineInput(serializers.Serializer):
    item = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)
    # A discount on this line alone. Its reason is the sale's one reason.
    discount = LineDiscountInput(required=False, allow_null=True)


class AuthorizationInput(serializers.Serializer):
    """
    A supervisor's own credentials, for a discount above the cashier's limit.

    Write-only and never stored: `discount_policy.approver_for` authenticates
    them and keeps the resulting *user*, so the password exists only for the
    length of the request. A user id would be no authorisation at all — the
    till would be asserting who approved, which is the thing being checked.
    """
    username = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, style={"input_type": "password"},
                                     trim_whitespace=False)


class DiscountInput(serializers.Serializer):
    type = serializers.ChoiceField(choices=Sale.DISCOUNT)
    value = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0"))
    reason = serializers.CharField(required=False, allow_blank=True)
    authorization = AuthorizationInput(required=False, allow_null=True)


class SaleInput(serializers.Serializer):
    lines = CartLineInput(many=True)
    customer_type = serializers.ChoiceField(choices=Sale.CUSTOMER, default="walk_in")
    # The patient's integer pk, the way every FK write body carries it (rule 32).
    patient = serializers.IntegerField(required=False, allow_null=True)
    customer_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    customer_phone = serializers.CharField(required=False, allow_blank=True, max_length=40)
    discount = DiscountInput(required=False, allow_null=True)
    # The one reason every discount on this sale is given for, whether it is
    # sale-wide, on individual lines or both. `discount.reason` is accepted too.
    discount_reason = serializers.CharField(required=False, allow_blank=True)
    # One authorisation covers the whole receipt, the same way one reason does
    # — and it has to live here as well as on `discount`, because a cart whose
    # only over-limit discount is on a *line* has no sale-wide discount object
    # to carry it.
    authorization = AuthorizationInput(required=False, allow_null=True)
    payment_method = serializers.ChoiceField(choices=Payment.METHOD, default="cash")
    amount_tendered = serializers.DecimalField(max_digits=12, decimal_places=2, required=False,
                                               allow_null=True)
    client_token = serializers.UUIDField(required=False, allow_null=True)
    held_sale = serializers.IntegerField(required=False, allow_null=True)


class ReturnLineInput(serializers.Serializer):
    sale_item = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=0)


class ReturnInput(serializers.Serializer):
    lines = ReturnLineInput(many=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    refund_method = serializers.ChoiceField(choices=Payment.METHOD, required=False,
                                            allow_blank=True)
