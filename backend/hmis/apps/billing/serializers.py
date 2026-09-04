from rest_framework import serializers
from .models import PatientLedger, Charge, Payment, Adjustment, BillingItem, PaymentDeferral
class BillingItemSerializer(serializers.ModelSerializer):
    class Meta: model = BillingItem; fields = "__all__"
class PatientLedgerSerializer(serializers.ModelSerializer):
    outstanding_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    # The debtors list is worked by phone, so it needs the number to call and
    # the file number to quote — not just a name.
    patient_file_number = serializers.CharField(source="patient.file_number", read_only=True)
    patient_phone = serializers.CharField(source="patient.phone_number", read_only=True)
    class Meta: model = PatientLedger; fields = "__all__"
class ChargeSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    department_name = serializers.SerializerMethodField()
    # Original − discount − waiver = payable; payable − paid = balance. Sent
    # as separate figures rather than one number, because a bill that only
    # shows what is left cannot be audited.
    payable = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=1, read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # What the money actually says, worked out from the figures rather than
    # read off a stored flag — including "deferred", which is an authorised
    # pay-later and never a kind of paid.
    settlement_status = serializers.CharField(read_only=True)
    deferral = serializers.SerializerMethodField()
    # amount_paid, amount_discounted and amount_waived only ever move through
    # a payment or a discount/waiver — never a direct write.
    class Meta:
        model = Charge; fields = "__all__"
        read_only_fields = ["created_by", "status", "amount_paid", "amount_discounted",
                            "amount_waived"]
    def get_department_name(self, obj): return obj.department.name if obj.department_id else None

    def get_deferral(self, obj):
        deferral = obj.active_deferral
        if deferral is None:
            return None
        approver = deferral.approved_by
        return {
            "id": deferral.pk,
            "amount_deferred": f"{deferral.amount_deferred:.2f}",
            "reason": deferral.reason,
            "approved_by": (approver.get_full_name() or approver.username) if approver else None,
            "approved_at": deferral.created_at,
        }
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


class PaymentDeferralSerializer(serializers.ModelSerializer):
    """The pay-later register: who let a patient proceed owing, and for how much."""
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    charge_description = serializers.CharField(source="charge.description", read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = PaymentDeferral
        fields = ["id", "patient", "patient_name", "charge", "charge_description",
                  "amount_deferred", "outstanding", "reason", "approved_by",
                  "approved_by_name", "released_at", "created_at"]
        read_only_fields = ["approved_by", "released_at"]

    def get_approved_by_name(self, obj):
        user = obj.approved_by
        return (user.get_full_name() or user.username) if user else None

    def get_outstanding(self, obj):
        return f"{obj.charge.balance:.2f}"
