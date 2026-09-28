from rest_framework import serializers
from .models import Prescription


class PrescriptionSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    # On the row because the pharmacy's own paperwork needs it — a dispensing
    # label identifies the patient by file number, and refetching the patient
    # for every line of a script is a request per drug.
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_file_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_age = serializers.CharField(source="patient.age_display", read_only=True)
    patient_sex = serializers.CharField(source="patient.get_sex_display", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    item_unit = serializers.CharField(source="item.unit_label", read_only=True)
    # What the drug is, beside what it is called — the pharmacist reading a
    # queue of unfamiliar brand names, and the doctor reading their own script
    # back. It is the product's own category (rule: one relation, no second
    # copy of the text) and it decides nothing: dispensing still resolves
    # stock, expiry, FEFO and location exactly as before.
    item_category = serializers.CharField(source="item.category_name", read_only=True,
                                          default="")
    item_strength = serializers.CharField(source="item.strength", read_only=True)
    item_form = serializers.CharField(source="item.dosage_form", read_only=True)
    route_label = serializers.CharField(source="get_route_display", read_only=True)
    doctor_name = serializers.SerializerMethodField()
    dispensed_by_name = serializers.SerializerMethodField()
    # **This line's own bill**, in the one shape every department reads
    # (`billing.status.service_billing`) — never the patient's balance.
    billing = serializers.SerializerMethodField()
    # Whether the money lets it leave the shelf now: the same
    # `dispensing_clearance` the dispense service refuses with, so the button
    # and the server cannot disagree. A courtesy; the service is the control.
    dispensable = serializers.SerializerMethodField()
    # One per submission from the prescribing screen, so a retry returns the
    # script it already wrote (`Prescription.client_token`).
    client_token = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Prescription
        fields = "__all__"
        # Everything after the doctor's request is set by the services, never
        # by a client write — including the bill and its price.
        read_only_fields = [
            "doctor", "status", "dispensed_by", "dispensed_at",
            "dispensed_value", "cancelled_reason", "charge", "quoted_amount",
        ]

    def get_unique_together_validators(self):
        # DRF turns `one_line_per_drug_per_submission` into a validator that
        # makes `client_token` *required* — refusing every caller that sends
        # none, which is most of them. The constraint is the database's to
        # enforce and the view answers a retry readably; the same reason
        # `AppointmentSerializer` returns [] here (rule 55).
        return []

    def get_billing(self, obj):
        from apps.billing.status import service_billing
        return service_billing(obj.charge, fallback_amount=obj.quoted_amount or 0) \
            if obj.charge_id else {**service_billing(None, fallback_amount=obj.quoted_amount or 0),
                                   "priced": obj.quoted_amount is not None}

    def get_dispensable(self, obj):
        if obj.status != "pending":
            return False
        from .services import dispensing_clearance
        return dispensing_clearance(obj)[0]

    def get_doctor_name(self, obj):
        return _display(obj.doctor)

    def get_dispensed_by_name(self, obj):
        return _display(obj.dispensed_by) if obj.dispensed_by_id else None


def _display(user):
    return user.get_full_name() or user.username
