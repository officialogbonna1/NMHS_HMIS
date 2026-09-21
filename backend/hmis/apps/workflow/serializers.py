from rest_framework import serializers

from apps.accounts.permissions import ADMIN_ROLES
from apps.billing import status as billing_status

from . import access
from .models import Visit, PatientRoute, RouteService
class VisitSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    class Meta: model = Visit; fields = "__all__"; read_only_fields = ["opened_by"]
class RouteServiceSerializer(serializers.ModelSerializer):
    """
    One examination a referral asked for, with what it cost and whether it
    has been settled.

    `billing` is read from the charge the request raised — never computed from
    today's catalogue price — and is exactly the block the laboratory's own
    order serializer answers with, so a station shows payment the same way
    whichever unit it is. The unit reads it; it never writes it, and it is
    never a gate: a patient already on the couch is scanned and the desk
    chases the balance (rule 24).
    """
    billing = serializers.SerializerMethodField()

    class Meta:
        model = RouteService
        fields = ["id", "item", "name", "unit_price", "charge", "billing", "created_at"]
        read_only_fields = fields

    def get_billing(self, obj):
        return billing_status.service_billing(obj.charge, fallback_amount=obj.unit_price)


class PatientRouteSerializer(serializers.ModelSerializer):
    patient_id = serializers.IntegerField(source="visit.patient_id", read_only=True)
    # The routing identity, so a row that drives a link to the chart does not
    # have to send the reader to the integer pk. Read-only, and never a
    # permission: `/patients/<uuid>/` runs the same access filter.
    patient_uuid = serializers.UUIDField(source="visit.patient.uuid", read_only=True)
    patient_name = serializers.CharField(source="visit.patient.display_name", read_only=True)
    patient_number = serializers.CharField(source="visit.patient.patient_number", read_only=True)
    patient_file_number = serializers.CharField(source="visit.patient.patient_number", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    assigned_to_role = serializers.CharField(source="assigned_to.role", read_only=True)
    routed_by_name = serializers.SerializerMethodField()
    result_by_name = serializers.SerializerMethodField()
    purpose_label = serializers.CharField(source="get_purpose_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # The clinical context the receiving unit reads before it calls the
    # patient in — who they are, how to reach them, and what the visit is
    # about. Demographics only: every role a route can reach already reads
    # these from `/patients/` (PATIENT_LOOKUP_ROLES), so this adds no access,
    # it saves the station a second request for what it is already allowed.
    patient_sex = serializers.CharField(source="visit.patient.get_sex_display", read_only=True)
    patient_age = serializers.CharField(source="visit.patient.age_display", read_only=True)
    patient_phone = serializers.CharField(source="visit.patient.phone_number", read_only=True)
    visit_reason = serializers.CharField(source="visit.reason", read_only=True)
    visit_type = serializers.CharField(source="visit.get_visit_type_display", read_only=True)
    # What was actually asked for, where the referral names configured
    # services — the imaging examinations today. Read-only: they are put on a
    # referral through `request-services/`, which is what raises the charge.
    services = RouteServiceSerializer(many=True, read_only=True)
    # Where this referral's money stands, in one word and one figure, so the
    # queue row says it without anybody opening a second page.
    #
    # It sums **whatever this referral raised**, which is not always a
    # `RouteService`: the laboratory's charges hang off the `LabOrderTest`
    # rows of the order the route opened (rule 24), imaging's off the route's
    # own services (rule 51). `billing.status.route_billing` is what knows the
    # difference, so a station never has to.
    billing = serializers.SerializerMethodField()
    # Whether this row is the reader's to work, and who has it if not.
    #
    # A station sees its unit's whole board now (`work_routes_for`), so a row
    # can be somebody else's — and a screen that worked that out for itself
    # would be a second copy of the rule. These read `workflow/access.py`, the
    # same function `PatientRouteViewSet._own_route` gates start / complete /
    # record-result with, so a button is never offered that the API refuses.
    can_work = serializers.SerializerMethodField()
    can_accept = serializers.SerializerMethodField()
    claimed_by_other = serializers.SerializerMethodField()
    result_file_url = serializers.SerializerMethodField()
    result_file_name = serializers.SerializerMethodField()

    # Status moves through the start/complete/cancel actions only. Reception
    # raises the route and can call it off; the clinician the patient was sent
    # to is the one who says the work is under way or done.
    # The finding is written through the record-result / complete actions,
    # which stamp who wrote it — never by PATCHing the row.
    class Meta:
        model = PatientRoute
        fields = "__all__"
        read_only_fields = ["routed_by", "status", "result", "result_data", "result_by",
                            "result_at"]

    def get_billing(self, obj):
        return billing_status.route_billing(obj)

    def _reader(self):
        return getattr(self.context.get("request"), "user", None)

    def get_can_work(self, obj):
        from .views import PURPOSE_ROLE, ROLE_PURPOSES

        user = self._reader()
        if user is None or not user.is_authenticated:
            return False
        return access.may_work(user, obj, role_purposes=ROLE_PURPOSES, purpose_role=PURPOSE_ROLE)

    def get_can_accept(self, obj):
        """Unclaimed work this reader could take. Once somebody holds it, the
        answer is no for everyone else — `accept` returns 409."""
        user = self._reader()
        if user is None or not user.is_authenticated or obj.assigned_to_id:
            return False
        return obj.status == "queued" and self.get_can_work(obj)

    def get_claimed_by_other(self, obj):
        user = self._reader()
        if user is None or not user.is_authenticated:
            return False
        return access.claimed_by_somebody_else(user, obj)

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.get_full_name() or obj.assigned_to.username if obj.assigned_to else None

    def validate_assigned_to(self, user):
        # Routing to a disabled account silently parks the patient in a queue
        # nobody is watching.
        if user and not user.is_active:
            raise serializers.ValidationError("That staff account is disabled.")
        return user

    def validate(self, attrs):
        """
        A named person has to be somebody who does that work.

        `refer/` has always checked this; creating a route did not, so the
        front desk could name a cashier on an eye referral over the API and
        the patient would sit in a queue that person cannot even list. The
        rule is `PURPOSE_ROLE`'s — the same map the notification and the queue
        read — so the three cannot disagree about who the work is for.

        A purpose with no role of its own ("other") falls back to department
        membership at routing time and is left alone here.
        """
        # Imported here: workflow.views imports this module, so a module-level
        # import would close the circle.
        from .views import PURPOSE_ROLE

        assigned_to = attrs.get("assigned_to", getattr(self.instance, "assigned_to", None))
        purpose = attrs.get("purpose", getattr(self.instance, "purpose", None))
        roles = PURPOSE_ROLE.get(purpose)
        if assigned_to and roles and assigned_to.role not in {*roles, *ADMIN_ROLES}:
            raise serializers.ValidationError({"assigned_to": (
                f"{assigned_to.get_full_name() or assigned_to.username} cannot take "
                f"{dict(PatientRoute.PURPOSE).get(purpose, purpose)} work.")})
        return attrs

    def get_routed_by_name(self, obj):
        user = obj.routed_by
        return (user.get_full_name() or user.username) if user else None

    def get_result_by_name(self, obj):
        user = obj.result_by
        return (user.get_full_name() or user.username) if user else None

    def _filed(self, obj):
        return obj.filed_tests.first()

    def get_result_file_url(self, obj):
        test = self._filed(obj)
        try:
            return test.file.url if test and test.file else None
        except ValueError:
            return None

    def get_result_file_name(self, obj):
        test = self._filed(obj)
        return test.file.name.rsplit("/", 1)[-1] if test and test.file else None
