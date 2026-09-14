from decimal import Decimal

from django.db import models
from rest_framework import serializers
from .models import (PatientLedger, Charge, Payment, Adjustment, BillingItem, PaymentDeferral,
                     Refund, RefundAllocation)
from .withdrawn import is_withdrawn


def _cents(value):
    """A money figure as the database returned it, to the kobo — SQLite sums in floats."""
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


class BillingItemSerializer(serializers.ModelSerializer):
    class Meta: model = BillingItem; fields = "__all__"
class PatientLedgerSerializer(serializers.ModelSerializer):
    outstanding_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    # The routing identity, so a row that drives a link to the chart does not
    # have to send the reader to the integer pk. Read-only, and never a
    # permission: `/patients/<uuid>/` runs the same access filter.
    patient_uuid = serializers.UUIDField(source="patient.uuid", read_only=True)
    # The debtors list is worked by phone, so it needs the number to call and
    # the file number to quote — not just a name.
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_file_number = serializers.CharField(source="patient.patient_number", read_only=True)
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
    # What the patient actually owes on this bill today — zero once it is
    # cancelled, whatever the face value still says. `balance` is the charge's
    # own arithmetic and stays as it was, so an audit can read both.
    outstanding = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    amount_refunded = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    refundable_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_cancelled = serializers.BooleanField(read_only=True)
    cancelled_by_name = serializers.SerializerMethodField()
    # Who the bill is for, as a person is identified and as the chart is
    # addressed — the Service Cancellations desk lists every patient's bills.
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_uuid = serializers.UUIDField(source="patient.uuid", read_only=True)
    # The service's own department took it back, but the bill still stands
    # (`billing.withdrawn`). What the Service Cancellations desk leads with.
    service_withdrawn = serializers.SerializerMethodField()
    # Money on this bill that no payment allocation accounts for. Cancel &
    # refund refuses it rather than guessing a payment, so the screen says so
    # before anybody presses the button.
    untraceable_amount = serializers.SerializerMethodField()
    # amount_paid, amount_discounted and amount_waived only ever move through
    # a payment or a discount/waiver — never a direct write.
    class Meta:
        model = Charge; fields = "__all__"
        read_only_fields = ["created_by", "status", "amount_paid", "amount_discounted",
                            "amount_waived", "amount_returned", "cancelled_at", "cancelled_by",
                            "cancellation_reason"]
    def get_department_name(self, obj): return obj.department.name if obj.department_id else None

    def get_cancelled_by_name(self, obj):
        user = obj.cancelled_by
        return (user.get_full_name() or user.username) if user else None

    def get_service_withdrawn(self, obj):
        # The viewset annotates it in the list query; a charge that did not
        # come through that queryset asks once.
        annotated = getattr(obj, "service_withdrawn", None)
        return bool(annotated) if annotated is not None else is_withdrawn(obj)

    def get_untraceable_amount(self, obj):
        allocated = getattr(obj, "allocated_total", None)
        if allocated is None:
            allocated = obj.allocations.aggregate(v=models.Sum("amount"))["v"]
        traceable = _cents(allocated) - _cents(obj.amount_refunded)
        return f"{max(_cents(obj.amount_paid) - traceable, Decimal('0')):.2f}"

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
    # A walk-in POS customer's payment has no patient: say nothing rather than
    # printing "None" where a name goes.
    patient_name = serializers.SerializerMethodField()
    patient_number = serializers.SerializerMethodField()
    received_by_name = serializers.SerializerMethodField()

    def get_patient_name(self, obj):
        return str(obj.patient) if obj.patient_id else None

    def get_patient_number(self, obj):
        return obj.patient.patient_number if obj.patient_id else None
    # What has gone back and what still could. The refund panel needs both
    # before it will let anybody type an amount, and a payment row that says
    # only what arrived cannot show that half of it was returned.
    amount_refunded = serializers.SerializerMethodField()
    refundable_balance = serializers.SerializerMethodField()
    is_fully_refunded = serializers.SerializerMethodField()
    class Meta: model = Payment; fields = "__all__"; read_only_fields = ["received_by", "channel"]
    def get_received_by_name(self, obj): return obj.received_by.get_full_name() or obj.received_by.username

    def get_amount_refunded(self, obj): return f"{obj.amount_refunded:.2f}"

    def get_refundable_balance(self, obj): return f"{obj.refundable_balance:.2f}"

    def get_is_fully_refunded(self, obj): return obj.is_fully_refunded
class AdjustmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    patient_uuid = serializers.UUIDField(source="patient.uuid", read_only=True)
    charge_description = serializers.SerializerMethodField()
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    class Meta: model = Adjustment; fields = "__all__"; read_only_fields = ["approved_by"]
    def get_charge_description(self, obj): return obj.charge.description if obj.charge_id else None

    def validate(self, attrs):
        """
        Two rules for the generic `POST /api/adjustments/` path.

        **A refund is never an adjustment here.** Money handed back enters the
        system one way only: the Refund workflow (`POST /api/payments/<id>/
        refund/`, and the charge-scoped cancel & refund), which writes a
        `Refund` record through `billing.services.refund_payment` — the record
        Total Facility Revenue, net revenue retained and the refunds report
        subtract. An `Adjustment(kind="refund")` written here had no `Refund`
        behind it, so it moved the patient's ledger while every revenue figure
        said nothing had gone back. The check reads the *resulting* kind, so a
        PATCH cannot turn a discount into a refund, nor edit the ledger row the
        workflow wrote beside its `Refund`. The workflow never comes through
        this serializer, so it is unaffected.

        **A discount or a waiver forgives a *bill*, so it has to name one.**
        Without this, the endpoint could credit a patient's ledger without
        touching any charge: the balance fell, `amount_discounted` stayed at
        zero, and no department column could ever show the money. The
        dedicated actions — `/charges/<id>/discount/`, `/discount-amount/` and
        `/waive/` — always named a charge; this closes the generic path behind
        them.

        Both are serializer rules, never model constraints: `Adjustment.charge`
        stays nullable and `kind="refund"` stays a valid row, so history and the
        workflow's own rows stay readable.
        """
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        charge = attrs.get("charge", getattr(self.instance, "charge", None))
        if kind == "refund":
            raise serializers.ValidationError({
                "kind": "Refunds cannot be recorded as adjustments. Process the refund "
                        "against the payment on the Refunds desk, which records it as a Refund.",
                "code": "refund_workflow_required",
            })
        if kind == "return":
            # Goods brought back are recorded by a pharmacy POS return, which
            # also quarantines the stock and refunds the payment.
            raise serializers.ValidationError({
                "kind": "Returned goods cannot be recorded as adjustments. Take the return "
                        "against the sale in Pharmacy → POS sales.",
                "code": "return_workflow_required",
            })
        if kind in ("discount", "waiver") and charge is None:
            raise serializers.ValidationError({
                "charge": f"A {kind} must name the charge it applies to.",
                "code": "charge_required",
            })
        return attrs
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


class RefundAllocationSerializer(serializers.ModelSerializer):
    """Which bill this slice of the refund came back off."""
    charge_description = serializers.CharField(source="charge.description", read_only=True)
    # The department that had taken the money — the same attribution the
    # report uses, read off the charge rather than off whoever refunded it.
    department_name = serializers.SerializerMethodField()

    class Meta:
        model = RefundAllocation
        fields = ["id", "charge", "charge_description", "department_name", "amount", "created_at"]

    def get_department_name(self, obj):
        return obj.charge.department.name if obj.charge.department_id else None


class RefundSerializer(serializers.ModelSerializer):
    """
    A refund, read back. Every field is read-only: a refund is written by
    `billing.services.refund_payment` and never by a PATCH, the same rule the
    rest of the money follows.
    """
    # A walk-in POS customer's refund has no patient.
    patient_name = serializers.SerializerMethodField()
    patient_uuid = serializers.SerializerMethodField()
    patient_number = serializers.SerializerMethodField()

    def get_patient_name(self, obj):
        return str(obj.patient) if obj.patient_id else None

    def get_patient_uuid(self, obj):
        return str(obj.patient.uuid) if obj.patient_id else None

    def get_patient_number(self, obj):
        return obj.patient.patient_number if obj.patient_id else None

    payment_amount = serializers.DecimalField(source="payment.amount", max_digits=12,
                                              decimal_places=2, read_only=True)
    payment_method = serializers.CharField(source="payment.method", read_only=True)
    payment_date = serializers.DateTimeField(source="payment.created_at", read_only=True)
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    processed_by_name = serializers.SerializerMethodField()
    authorized_by_name = serializers.SerializerMethodField()
    allocations = RefundAllocationSerializer(many=True, read_only=True)
    # Was this refund the money half of a service cancellation?
    from_cancellation = serializers.SerializerMethodField()

    class Meta:
        model = Refund
        fields = ["id", "payment", "payment_amount", "payment_method", "payment_date",
                  "patient", "patient_name", "patient_uuid", "patient_number",
                  "amount", "reason", "method", "method_label", "reference",
                  "processed_by", "processed_by_name", "authorized_by", "authorized_by_name",
                  "adjustment", "allocations", "from_cancellation", "created_at"]
        read_only_fields = fields

    def get_from_cancellation(self, obj):
        """
        Read off the bills it came back off rather than stored: a refund taken
        from a charge at or after that charge was cancelled can only have come
        from Cancel & refund, because every other refund path passes a cancelled
        charge by — `refund_charge` refuses one, and a payment-scoped refund
        finds nothing left on it. Uses the register's prefetched allocations.
        """
        return any(
            a.charge.status == "cancelled" and a.charge.cancelled_at is not None
            and a.charge.cancelled_at <= obj.created_at
            for a in obj.allocations.all()
        )

    def _name(self, user):
        return (user.get_full_name() or user.username) if user else None

    def get_processed_by_name(self, obj): return self._name(obj.processed_by)

    def get_authorized_by_name(self, obj): return self._name(obj.authorized_by)


class RefundRequestSerializer(serializers.Serializer):
    """
    What a refund request has to carry. Amount and reason are both mandatory
    — a refund with no reason against it is indistinguishable from a mistake
    six months later — and the confirmation the UI asks for is its own
    deliberate step rather than a field here.
    """
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)
    method = serializers.ChoiceField(choices=Payment.METHOD, required=False)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
