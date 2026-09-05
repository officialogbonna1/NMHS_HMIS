from django.core.exceptions import ValidationError
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .models import Prescription
from .serializers import PrescriptionSerializer
# OutOfStockError and AlreadyDispensedError both subclass ValidationError,
# which is what the actions below turn into a 400 with the service's message.
from .services import (create_prescription, create_prescriptions, dispense_prescription,
                       cancel_prescription)
from apps.accounts.permissions import IsDoctor, IsPharmacist, RoleRequired
from apps.core.services import audit_event, notify


class PrescriptionViewSet(viewsets.ModelViewSet):
    """
    Doctors write prescriptions; pharmacists fill them.

    Creating one does not move stock — it puts the request in the pharmacy
    queue. The stock deduction, the audit rows and the patient charge all
    happen when a pharmacist posts to /dispense/, so the person handing over
    the drugs is the one recorded as having released them.
    """
    queryset = Prescription.objects.all()
    serializer_class = PrescriptionSerializer
    http_method_names = ["get", "post", "head", "options"]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["patient", "doctor", "status", "item"]

    def get_permissions(self):
        if self.action in {"create", "bulk"}:
            return [IsDoctor()]
        if self.action == "dispense":
            return [IsPharmacist()]
        if self.action == "cancel":
            return [RoleRequired(["doctor", "pharmacist"])]
        return [RoleRequired(["doctor", "pharmacist"])]

    def get_queryset(self):
        user = self.request.user
        base = Prescription.objects.select_related("patient", "item", "doctor", "dispensed_by")
        if user.role in {"admin", "hospital_admin", "pharmacist"}:
            return base
        if user.role == "doctor":
            return base.filter(doctor=user)
        return Prescription.objects.none()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            prescription = create_prescription(
                patient=serializer.validated_data["patient"],
                doctor=request.user,
                item=serializer.validated_data["item"],
                quantity=serializer.validated_data["quantity"],
                dosage_instructions=serializer.validated_data.get("dosage_instructions", ""),
            )
        except ValidationError as exc:
            return Response({"detail": _message(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="prescription.written", instance=prescription, request=request)
        _notify_pharmacy(prescription)
        return Response(self.get_serializer(prescription).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request):
        """
        A whole script at once: one patient, several drugs.

        Written one drug at a time, a doctor prescribing five and being
        refused on the third leaves two queued and three lost with no way to
        tell which. The service writes them inside one transaction, so the
        script either reaches the pharmacy whole or not at all — and the
        error names the drug that stopped it.
        """
        from apps.inventory.models import Item
        from apps.patients.models import Patient

        patient = Patient.objects.filter(pk=_as_id(request.data.get("patient"))).first()
        if not patient:
            return Response({"patient": "Choose the patient you are prescribing for."},
                            status=status.HTTP_400_BAD_REQUEST)

        raw_lines = request.data.get("lines") or []
        if not isinstance(raw_lines, list) or not raw_lines:
            return Response({"lines": "Add at least one drug to the prescription."},
                            status=status.HTTP_400_BAD_REQUEST)

        lines = []
        for entry in raw_lines:
            item = Item.objects.filter(pk=_as_id((entry or {}).get("item"))).first()
            if not item:
                return Response({"lines": "One of these drugs is no longer in the catalogue."},
                                status=status.HTTP_400_BAD_REQUEST)
            try:
                quantity = int(entry.get("quantity"))
            except (TypeError, ValueError):
                return Response({"lines": f"How many units of {item.name}?"},
                                status=status.HTTP_400_BAD_REQUEST)
            lines.append({
                "item": item, "quantity": quantity,
                "dosage_instructions": entry.get("dosage_instructions", "") or "",
            })

        try:
            prescriptions = create_prescriptions(patient=patient, doctor=request.user, lines=lines)
        except ValidationError as exc:
            return Response({"detail": _message(exc)}, status=status.HTTP_400_BAD_REQUEST)

        for prescription in prescriptions:
            audit_event(actor=request.user, action="prescription.written",
                        instance=prescription, request=request)
        _notify_pharmacy_of_script(prescriptions)
        return Response(self.get_serializer(prescriptions, many=True).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def dispense(self, request, pk=None):
        prescription = self.get_object()
        try:
            prescription = dispense_prescription(prescription=prescription, pharmacist=request.user)
        except ValidationError as exc:
            return Response({"detail": _message(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="prescription.dispensed", instance=prescription, request=request)
        notify(
            recipient=prescription.doctor,
            title=f"Dispensed: {prescription.item.name} for {prescription.patient}",
            message=f"{prescription.quantity} {prescription.item.unit_label}(s) handed over by the pharmacy.",
            category="pharmacy",
            action_url=f"/patients/{prescription.patient_id}",
        )
        return Response(self.get_serializer(prescription).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        prescription = self.get_object()
        if request.user.role == "doctor" and prescription.doctor_id != request.user.id:
            return Response({"detail": "You can only cancel your own prescriptions."}, status=status.HTTP_403_FORBIDDEN)
        try:
            prescription = cancel_prescription(
                prescription=prescription, actor=request.user,
                reason=request.data.get("reason", ""),
            )
        except ValidationError as exc:
            return Response({"detail": _message(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="prescription.cancelled", instance=prescription, request=request)
        return Response(self.get_serializer(prescription).data)


def _as_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _notify_pharmacy_of_script(prescriptions):
    """
    One notification for the script, not one per drug — five drugs for the
    same patient is a single errand at the counter, and five separate pings
    is how a pharmacist starts ignoring them.
    """
    from apps.accounts.models import User

    if not prescriptions:
        return
    first = prescriptions[0]
    drugs = ", ".join(f"{p.item.name} ×{p.quantity}" for p in prescriptions)
    title = (f"New prescription: {first.item.name}" if len(prescriptions) == 1
             else f"New prescription: {len(prescriptions)} drugs for {first.patient}")
    for pharmacist in User.objects.filter(role="pharmacist", is_active=True):
        notify(recipient=pharmacist, title=title,
               message=f"{drugs} — for {first.patient}",
               category="pharmacy", action_url="/pharmacy")


def _notify_pharmacy(prescription):
    from apps.accounts.models import User

    for pharmacist in User.objects.filter(role="pharmacist", is_active=True):
        notify(
            recipient=pharmacist,
            title=f"New prescription: {prescription.item.name}",
            message=f"{prescription.quantity} {prescription.item.unit_label}(s) for {prescription.patient}",
            category="pharmacy",
            action_url="/pharmacy",
        )


def _message(exc):
    return "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
