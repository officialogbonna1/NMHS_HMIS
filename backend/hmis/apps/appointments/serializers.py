from rest_framework import serializers

from .models import Appointment


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_uuid = serializers.UUIDField(source="patient.uuid", read_only=True)
    doctor_name = serializers.SerializerMethodField()
    # `provider` is what the rest of the hospital calls this person now that a
    # sonographer or an eye doctor can hold an appointment. It is a read-only
    # alias for `doctor` — the same column, never a second field — the way
    # `file_number` aliases `patient_number` (rule 32). Writers still send
    # `doctor`, so every existing caller is untouched.
    provider = serializers.PrimaryKeyRelatedField(source="doctor", read_only=True)
    provider_name = serializers.SerializerMethodField()
    department_name = serializers.CharField(source="department.name", read_only=True)
    # Where the money stands, read off the charge this booking raised. It is
    # `Charge.settlement_status` rendered by `billing/status.py` — the one
    # reading every unit shows (rule 54) — so the queue cannot invent a
    # vocabulary of its own, and a fee is never mistaken for a payment.
    billing = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = "__all__"
        # Status only ever moves through the accept/cancel/start/end
        # actions (apps.appointments.views), never a raw field write —
        # keeps every transition going through the state-machine checks.
        # start_time/end_time are stamped by those same transitions, so
        # reception cannot schedule (or back-date) a consultation.
        #
        # The booking columns are read-only for the same reason money is:
        # `department`, `service_name`, `service_fee` and `charge` are all
        # *derived* from the service that was booked, on the server
        # (`appointments/services.queue_appointment`). A client that could
        # write `service_fee` would be a client that sets its own prices.
        # The booking columns are read-only for the same reason money is:
        # `service`, `department`, `service_name`, `service_fee` and `charge`
        # are all decided from the service that was booked, on the server
        # (`appointments/services.queue_appointment`), at the moment it was
        # booked. A client that could write `service_fee` would be a client
        # that sets its own prices; a client that could re-point `service`
        # afterwards would leave the fee and the charge describing something
        # else. The booking is one decision, taken once.
        read_only_fields = ["status", "start_time", "end_time", "service", "services",
                            "department", "service_name", "service_fee", "charge"]

    def get_unique_together_validators(self):
        """
        No field-level validator for the open-appointment constraint.

        DRF builds one automatically from `Appointment`'s `UniqueConstraint`,
        and it runs during `is_valid()` — *before* the view's
        `open_appointment_for` guard, replacing "Jane Doe is already in Dr
        Ade's queue (queued). Cancel that entry first…" with "The fields
        patient, doctor must make a unique set." and dropping the
        `already_queued` code the screen branches on.

        The constraint is not there to validate a form. It is the last line of
        defence against two genuinely concurrent requests (rule 30's reasoning
        applied to the queue): the guard answers the ordinary repeat with a
        sentence somebody can act on, and the database answers the race with an
        `IntegrityError` that `apps/core/exceptions.py` renders as a 409.
        """
        return []

    def get_doctor_name(self, obj):
        return obj.doctor.get_full_name() or obj.doctor.username if obj.doctor_id else None

    def get_provider_name(self, obj):
        return self.get_doctor_name(obj)

    def get_billing(self, obj):
        """
        The fee and where the money stands, in the one vocabulary every unit
        already shows.

        One service reads through `service_billing`, which answers "NOT
        BILLED" for a booking that raised no charge and carries the fee
        anyway, so a screen never has to decide for itself whether a fee means
        a debt. Several read through `summarise`, which is the same module's
        answer for a basket: the total, and the **worst** of their statuses,
        because a booking that read "PAID" while one scan on it was not is how
        an unpaid service gets through (rule 54).
        """
        from apps.billing import status as billing_status

        charges = obj.charges
        if len(charges) > 1:
            return billing_status.summarise(charges)
        return billing_status.service_billing(
            charges[0] if charges else None, fallback_amount=obj.service_fee)
