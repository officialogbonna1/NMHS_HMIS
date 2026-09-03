from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from apps.core.services import notify
from .models import Appointment
from .serializers import AppointmentSerializer
from .services import (
    open_appointment_for, accept_appointment, cancel_appointment,
    start_appointment, end_appointment,
)
from apps.accounts.permissions import IsReception, RoleRequired, IsAdmin


class AppointmentViewSet(viewsets.ModelViewSet):
    """
    Reception queues a patient to a doctor; only the doctor accepts, starts,
    ends or cancels it. Reception never sets a time — the queue is ordered by
    when it was raised, and the start/end stamps come from the doctor's own
    transitions.
    """
    queryset = Appointment.objects.all()
    serializer_class = AppointmentSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["patient", "doctor", "status"]

    def get_queryset(self):
        user = self.request.user
        if user.role in {"admin", "hospital_admin", "reception"}:
            return Appointment.objects.all()
        if user.role == "doctor":
            return Appointment.objects.filter(doctor=user)
        return Appointment.objects.none()

    def get_permissions(self):
        if self.action == "create":
            return [IsReception()]
        if self.action in {"update", "partial_update", "destroy"}:
            return [IsAdmin()]
        if self.action in {"accept", "cancel", "start", "end"}:
            return [RoleRequired(["doctor"])]
        return [RoleRequired(["reception", "doctor"])]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        doctor = serializer.validated_data["doctor"]
        patient = serializer.validated_data["patient"]
        existing = open_appointment_for(doctor=doctor, patient=patient)
        if existing:
            doctor_name = doctor.get_full_name() or doctor.username
            return Response(
                {"detail": f"{patient} is already in Dr. {doctor_name}'s queue "
                           f"({existing.get_status_display().lower()}). Cancel that entry first "
                           f"if you need to re-queue them."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        appointment = serializer.save(status="queued")
        notify(
            recipient=doctor,
            title=f"New appointment: {appointment.patient}",
            message=appointment.reason,
            category="appointments",
            action_url="/appointments",
        )
        return Response(self.get_serializer(appointment).data, status=status.HTTP_201_CREATED)

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
