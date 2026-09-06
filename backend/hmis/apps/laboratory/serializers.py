from decimal import Decimal

from rest_framework import serializers

from .models import (
    LabOrder, LabOrderTest, LabPanel, LabParameter, LabResultAmendment,
    LabResultValue, LabTest, TYPE_OPTIONS,
)


def _name(user):
    return (user.get_full_name() or user.username) if user else None


class LabParameterSerializer(serializers.ModelSerializer):
    # What the entry form should offer, whether the options are fixed by the
    # result type or configured on the row.
    choices = serializers.SerializerMethodField()

    class Meta:
        model = LabParameter
        fields = [
            "id", "test", "code", "name", "group", "result_type", "unit",
            "reference_range", "ref_low", "ref_high", "normal_value", "options",
            "choices", "display_order", "is_required", "is_active",
        ]

    def get_choices(self, obj):
        return TYPE_OPTIONS.get(obj.result_type) or list(obj.options or [])

    def validate(self, attrs):
        low = attrs.get("ref_low", getattr(self.instance, "ref_low", None))
        high = attrs.get("ref_high", getattr(self.instance, "ref_high", None))
        if low is not None and high is not None and low > high:
            raise serializers.ValidationError(
                {"ref_low": "The low end of the range is above the high end."})
        return attrs


class LabTestSerializer(serializers.ModelSerializer):
    parameters = serializers.SerializerMethodField()
    parameter_count = serializers.SerializerMethodField()
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    charge_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = LabTest
        fields = [
            "id", "code", "name", "category", "category_label", "description",
            "specimen_type", "container", "turnaround_hours", "price", "charge_amount",
            "billing_item", "department", "is_active", "display_order",
            "parameters", "parameter_count",
        ]

    def get_parameters(self, obj):
        # Deactivated parameters stay out of the entry form but keep their
        # results readable — the value rows carry their own unit and range.
        rows = [p for p in obj.parameters.all() if p.is_active]
        return LabParameterSerializer(rows, many=True).data

    def get_parameter_count(self, obj):
        return sum(1 for p in obj.parameters.all() if p.is_active)


class LabTestSummarySerializer(serializers.ModelSerializer):
    """The catalogue as a picker sees it — no parameter payload."""
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    charge_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = LabTest
        fields = ["id", "code", "name", "category", "category_label", "specimen_type",
                  "container", "turnaround_hours", "price", "charge_amount", "is_active"]


class LabPanelSerializer(serializers.ModelSerializer):
    test_details = LabTestSummarySerializer(source="tests", many=True, read_only=True)

    class Meta:
        model = LabPanel
        fields = ["id", "code", "name", "description", "tests", "test_details", "is_active"]


class LabResultValueSerializer(serializers.ModelSerializer):
    parameter_name = serializers.CharField(source="parameter.name", read_only=True)
    parameter_code = serializers.CharField(source="parameter.code", read_only=True)
    parameter_group = serializers.CharField(source="parameter.group", read_only=True)
    result_type = serializers.CharField(source="parameter.result_type", read_only=True)
    display_order = serializers.IntegerField(source="parameter.display_order", read_only=True)
    unit = serializers.SerializerMethodField()
    reference_range = serializers.SerializerMethodField()
    flag_label = serializers.CharField(source="get_flag_display", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = LabResultValue
        fields = [
            "id", "parameter", "parameter_name", "parameter_code", "parameter_group",
            "result_type", "display_order", "value", "unit", "reference_range",
            "flag", "flag_label", "flag_is_manual", "comment",
            "recorded_by_name", "created_at", "updated_at",
        ]

    def get_unit(self, obj):
        # What it was measured in at the time; the catalogue may have moved on.
        return obj.unit_at_entry or obj.parameter.unit

    def get_reference_range(self, obj):
        return obj.reference_at_entry or obj.parameter.reference_range

    def get_recorded_by_name(self, obj):
        return _name(obj.recorded_by)


class LabResultAmendmentSerializer(serializers.ModelSerializer):
    parameter_name = serializers.CharField(source="parameter.name", read_only=True)
    amended_by_name = serializers.SerializerMethodField()

    class Meta:
        model = LabResultAmendment
        fields = ["id", "parameter", "parameter_name", "previous_value", "new_value",
                  "reason", "amended_by_name", "created_at"]

    def get_amended_by_name(self, obj):
        return _name(obj.amended_by)


class LabOrderTestSerializer(serializers.ModelSerializer):
    # Read off the order-time snapshot, not the live catalogue: this is how
    # the test read the day it was ordered, and that is what the report and
    # the bill have to keep saying.
    test_name = serializers.CharField(source="name", read_only=True)
    test_code = serializers.CharField(source="test.code", read_only=True)
    category = serializers.CharField(source="test_category", read_only=True)
    parameters = serializers.SerializerMethodField()
    values = LabResultValueSerializer(many=True, read_only=True)
    amendments = LabResultAmendmentSerializer(many=True, read_only=True)
    performed_by_name = serializers.SerializerMethodField()
    verified_by_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    billing = serializers.SerializerMethodField()

    class Meta:
        model = LabOrderTest
        fields = [
            "id", "order", "test", "test_name", "test_code", "category", "specimen_type",
            "container", "unit_price", "source", "source_label",
            "status", "status_label", "comments", "performed_by_name", "performed_at",
            "verified_by_name", "verified_at", "billing",
            "parameters", "values", "amendments", "created_at",
        ]
        # A test is submitted and verified through the actions, which stamp
        # who did it; the snapshot and the charge are never client-writable.
        read_only_fields = [
            "status", "comments", "performed_by_name", "performed_at", "verified_at",
            "test_name", "category", "specimen_type", "container", "unit_price", "source",
        ]

    def get_parameters(self, obj):
        # The snapshot, or the live catalogue as a fallback for rows written
        # before snapshots existed — `LabOrderTest.parameters` decides which.
        return obj.parameters

    def get_performed_by_name(self, obj):
        return _name(obj.performed_by)

    def get_verified_by_name(self, obj):
        return _name(obj.verified_by)

    def get_billing(self, obj):
        """
        What this one test costs and whether it has been settled — read from
        the charge the order raised, never computed from today's catalogue
        price. The lab reads this; it never writes it.
        """
        charge = obj.charge
        if charge is None:
            return {"billed": False, "status": "unbilled",
                    "amount": f"{obj.unit_price:.2f}", "paid": "0.00",
                    "outstanding": "0.00", "deferred": False}
        return {
            "billed": True,
            "charge": charge.pk,
            "status": charge.settlement_status,
            "amount": f"{charge.amount:.2f}",
            "discounted": f"{charge.amount_discounted:.2f}",
            "waived": f"{charge.amount_waived:.2f}",
            "payable": f"{charge.payable:.2f}",
            "paid": f"{charge.amount_paid:.2f}",
            "outstanding": f"{max(charge.balance, 0):.2f}",
            "deferred": charge.active_deferral is not None,
        }


class LabOrderSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_file_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_age = serializers.CharField(source="patient.age_display", read_only=True)
    patient_sex = serializers.CharField(source="patient.get_sex_display", read_only=True)
    items = LabOrderTestSerializer(many=True, read_only=True)
    requested_by_name = serializers.SerializerMethodField()
    entered_by_name = serializers.SerializerMethodField()
    verified_by_name = serializers.SerializerMethodField()
    collected_by_name = serializers.SerializerMethodField()
    report_to_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    billing = serializers.SerializerMethodField()

    class Meta:
        model = LabOrder
        fields = [
            "id", "order_number", "patient", "patient_name", "patient_number", "patient_file_number",
            "patient_age", "patient_sex", "visit", "route", "requested_by",
            "requested_by_name", "priority", "status", "status_label", "clinical_notes",
            "specimen_id", "specimen_collected_at", "collected_by", "collected_by_name",
            "lab_comments", "report_to", "report_to_name",
            "entered_by", "entered_by_name", "entered_at",
            "verified_by", "verified_by_name", "verified_at", "items", "billing",
            "created_at", "updated_at",
        ]
        # Status and every signature move through the viewset's actions, so a
        # result can never be marked verified by a PATCH.
        read_only_fields = [
            "order_number", "status", "requested_by", "entered_by", "entered_at",
            "verified_by", "verified_at", "collected_by", "report_to",
        ]

    def get_requested_by_name(self, obj):
        return _name(obj.requested_by)

    def get_entered_by_name(self, obj):
        return _name(obj.entered_by)

    def get_verified_by_name(self, obj):
        return _name(obj.verified_by)

    def get_collected_by_name(self, obj):
        return _name(obj.collected_by)

    def get_report_to_name(self, obj):
        """Who the result goes back to — the referrer unless redirected."""
        return _name(obj.report_to or obj.requested_by)

    def get_billing(self, obj):
        """
        The order's money, summed from its tests' own charges.

        Read-only: the laboratory never writes a figure here. Ordering a test
        raised the charge through `billing.services`, and the cash desk
        settles it there — this is the bench being told where that stands, so
        an unpaid test is visible rather than assumed.
        """
        charges = [item.charge for item in obj.items.all()
                   if item.charge_id and item.charge.status != "cancelled"]
        total = sum((c.amount for c in charges), Decimal("0"))
        paid = sum((c.amount_paid for c in charges), Decimal("0"))
        discounted = sum((c.amount_discounted for c in charges), Decimal("0"))
        waived = sum((c.amount_waived for c in charges), Decimal("0"))
        outstanding = sum((max(c.balance, Decimal("0")) for c in charges), Decimal("0"))
        deferred = [c for c in charges if c.active_deferral is not None]
        return {
            "billed": bool(charges),
            # Formatted here so the shape on the wire is the same whichever
            # renderer answers, and the frontend never has to guess.
            "total": f"{total:.2f}",
            "paid": f"{paid:.2f}",
            "discounted": f"{discounted:.2f}",
            "waived": f"{waived:.2f}",
            "outstanding": f"{outstanding:.2f}",
            "settled": bool(charges) and outstanding <= 0,
            "deferred": bool(deferred),
            "deferred_by": _name(deferred[0].active_deferral.approved_by) if deferred else None,
        }


class LabOrderSummarySerializer(serializers.ModelSerializer):
    """The worklist row — no parameters, no values."""
    patient_name = serializers.CharField(source="patient.__str__", read_only=True)
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_file_number = serializers.CharField(source="patient.patient_number", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    test_names = serializers.SerializerMethodField()
    requested_by_name = serializers.SerializerMethodField()
    # The counter's whole reason for reading this list: what is owed on it.
    billing = serializers.SerializerMethodField()

    class Meta:
        model = LabOrder
        fields = ["id", "order_number", "patient", "patient_name", "patient_number", "patient_file_number",
                  "status", "status_label", "priority", "test_names", "requested_by_name",
                  "specimen_id", "billing", "created_at", "verified_at"]

    def get_test_names(self, obj):
        # The names as ordered — a test renamed in the catalogue since must
        # not rename itself on an old worklist row.
        return [item.name for item in obj.items.all() if item.status != "cancelled"]

    def get_billing(self, obj):
        return LabOrderSerializer.get_billing(self, obj)

    def get_requested_by_name(self, obj):
        return _name(obj.requested_by)
