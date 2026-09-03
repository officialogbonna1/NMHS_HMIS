from rest_framework import serializers
from .models import PatientLedger, Charge, Payment, Adjustment, BillingItem
class BillingItemSerializer(serializers.ModelSerializer):
    class Meta: model = BillingItem; fields = "__all__"
class PatientLedgerSerializer(serializers.ModelSerializer):
    outstanding_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    class Meta: model = PatientLedger; fields = "__all__"
class ChargeSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    department_name = serializers.SerializerMethodField()
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=1, read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # amount_paid and amount_discounted only ever move through a payment or a
    # discount/waiver — never a direct write.
    class Meta:
        model = Charge; fields = "__all__"
        read_only_fields = ["created_by", "status", "amount_paid", "amount_discounted"]
    def get_department_name(self, obj): return obj.department.name if obj.department_id else None
class PaymentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    received_by_name = serializers.SerializerMethodField()
    class Meta: model = Payment; fields = "__all__"; read_only_fields = ["received_by", "channel"]
    def get_received_by_name(self, obj): return obj.received_by.get_full_name() or obj.received_by.username
class AdjustmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    charge_description = serializers.SerializerMethodField()
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    class Meta: model = Adjustment; fields = "__all__"; read_only_fields = ["approved_by"]
    def get_charge_description(self, obj): return obj.charge.description if obj.charge_id else None
    def get_approved_by_name(self, obj): return obj.approved_by.get_full_name() or obj.approved_by.username
