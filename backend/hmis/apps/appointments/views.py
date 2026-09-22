from decimal import Decimal

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db import transaction

from apps.core.services import notify
from . import booking
from .models import Appointment
from .serializers import AppointmentSerializer
from .services import (
    open_appointment_for, accept_appointment, cancel_appointment,
    start_appointment, end_appointment, queue_appointment,
)
from apps.accounts.permissions import IsReception, RoleRequired, IsAdmin


def _services_from(request):
    """
    The service keys a booking named — one, several, or none.

    JSON sends a list (or a single key); a form-encoded post repeats the
    field, which DRF's QueryDict exposes through `getlist`. Read here so
    neither the serializer nor `booking.refusal_for` has to care which door
    the request came through.
    """
    data = request.data
    value = data.get("service")
    if hasattr(data, "getlist"):
        listed = data.getlist("service")
        if len(listed) > 1:
            return listed
    return value


class AppointmentViewSet(viewsets.ModelViewSet):
    """
    Reception queues a patient to a doctor; only the doctor accepts, starts,
    ends or cancels it. Reception never sets a time — the queue is ordered by
    when it was raised, and the start/end stamps come from the doctor's own
    transitions.
    """
    queryset = Appointment.objects.select_related(
        "patient", "doctor", "department", "service", "charge").all()
    serializer_class = AppointmentSerializer
    filter_backends = [DjangoFilterBackend]
    # The queue workspace's filters. `doctor` is the provider and `service` is
    # what was booked — both existing columns, so this is a filter list rather
    # than a reporting feature.
    filterset_fields = ["patient", "doctor", "status", "department", "service"]

    def get_queryset(self):
        """
        Who sees which rows. Unchanged in every respect but one: a provider
        sees the appointments queued **to them**, which used to be spelled as
        "a doctor sees theirs". An eye doctor or a sonographer holding an
        appointment has exactly the same claim on it, and the column is the
        same column, so the rule is now about the row rather than the role.
        Reception and the administrators still see the whole queue.
        """
        user = self.request.user
        base = super().get_queryset()
        if user.role in {"admin", "hospital_admin", "reception"}:
            return base
        return base.filter(doctor=user)

    def get_permissions(self):
        if self.action == "create":
            return [IsReception()]
        if self.action in {"update", "partial_update", "destroy"}:
            return [IsAdmin()]
        if self.action in {"accept", "cancel", "start", "end"}:
            # The provider works their own queue. `PROVIDER_ROLES` is every
            # role a bookable service can be worked by, read from the same map
            # the dropdown is built from — so a role becomes able to transition
            # an appointment by becoming bookable, not by being retyped here.
            # `_require_own_appointment` is still what stops one provider
            # touching another's row.
            return [RoleRequired(booking.PROVIDER_ROLES)]
        if self.action == "booking_options":
            return [RoleRequired(["reception"])]
        return [RoleRequired(["reception", *booking.PROVIDER_ROLES])]

    def create(self, request, *args, **kwargs):
        """
        Queue a patient. Unchanged for the caller that has always sent
        patient + doctor + reason; a caller that also names a bookable
        `service` gets the department, the order-time fee and the charge
        filled in for it, on the server.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        doctor = serializer.validated_data["doctor"]
        patient = serializer.validated_data["patient"]
        existing = open_appointment_for(doctor=doctor, patient=patient)
        if existing:
            doctor_name = doctor.get_full_name() or doctor.username
            return Response(
                {"detail": f"{patient.display_name} is already in Dr. {doctor_name}'s queue "
                           f"({existing.get_status_display().lower()}). Cancel that entry first "
                           f"if you need to re-queue them.",
                 "code": "already_queued"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # A named service has to be bookable, and the provider has to be able
        # to do it — checked here because the dropdowns that offer them are a
        # courtesy, and the API is what actually refuses.
        # `service` is one key or several. Reception ticks the scans a patient
        # is being sent for and submits once, exactly as the billing counter
        # bills several services in one decision (rule 52) — and, as there,
        # what is submitted is identities, never money.
        service_key = _services_from(request)
        _, refusal = booking.refusal_for(service_key, doctor)
        if refusal:
            return Response(refusal, status=status.HTTP_400_BAD_REQUEST)
        # The booking and the bill it raises land together or not at all.
        with transaction.atomic():
            appointment = serializer.save(status="queued")
            queue_appointment(appointment=appointment, service_key=service_key,
                              actor=request.user)
        notify(
            recipient=doctor,
            # The service where one was booked, so the provider reads what
            # they are being queued for rather than only who. A consultation
            # booked the old way still says exactly what it always said.
            title=f"New appointment: {appointment.patient.display_name}",
            message=appointment.service_name or appointment.reason,
            category="appointments",
            action_url="/appointments",
        )
        return Response(self.get_serializer(appointment).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="booking-options")
    def booking_options(self, request):
        """
        Everything the booking form needs, in one read: the units that take
        appointments, the services each offers at the catalogue's own price,
        and who may be named on each.

        Served from the server because every one of those answers is a rule,
        not a list — `appointments/booking.py` derives all three from
        configuration that already exists. The form holds no copy of any of
        them, so ticking a service in Django admin is the whole of adding one.
        """
        departments = []
        for entry in booking.departments():
            services = []
            for service in entry["services"]:
                services.append({
                    "key": service["key"],
                    "id": service["id"],
                    "name": service["name"],
                    "fee": service["price"],
                    "billable": Decimal(service["price"]) > 0,
                    "category": service["category"],
                    "category_label": service["category_label"],
                    "providers": [
                        {"id": person.pk,
                         "name": person.get_full_name() or person.username,
                         "role": person.role,
                         "role_label": person.get_role_display()}
                        for person in booking.eligible_providers(service)
                    ],
                })
            departments.append({**entry, "services": services})
        # The fast path beside them: a general consultation, which names no
        # service, raises no charge and is the booking reception has always
        # made. Carried here so the form does not have to know which
        # department that is, or spell a department code into JavaScript.
        general = booking.general_consultation()
        return Response({
            "departments": departments,
            "general": {
                **general,
                "providers": [
                    {"id": person.pk,
                     "name": person.get_full_name() or person.username,
                     "role": person.role,
                     "role_label": person.get_role_display()}
                    for person in general["providers"]
                ],
            },
        })

    def _require_own_appointment(self, appointment):
        user = self.request.user
        if not user.is_admin and appointment.doctor_id != user.id:
            raise PermissionDenied("You can only manage your own appointments.")

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._transition(accept_appointment)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._transition(cancel_appointment)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._transition(start_appointment)

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        return self._transition(end_appointment)

    def _transition(self, service_fn):
        appointment = self.get_object()
        self._require_own_appointment(appointment)
        try:
            service_fn(appointment=appointment, actor=self.request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(appointment).data)
