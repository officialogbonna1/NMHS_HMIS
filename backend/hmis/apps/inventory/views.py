from django.core.exceptions import ValidationError
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import filters
from django_filters.rest_framework import DjangoFilterBackend

from .models import Item, Batch, StockMovement
from . import serializers
from .services import receive_batch, record_stock_count, write_off_expired
from apps.accounts.permissions import RoleRequired
from apps.core.services import audit_event

STOCK_ROLES = ["pharmacist", "inventory_manager"]


class ItemViewSet(viewsets.ModelViewSet):
    queryset = Item.objects.all()
    permission_classes = [RoleRequired]
    # The doctor's drug picker searches this list rather than filtering the
    # first page client-side, which quietly hid every drug past number 25.
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["category"]
    search_fields = ["name", "category"]
    ordering = ["name"]

    def get_permissions(self):
        if self.action == "list" and self.request.user.role == "doctor":
            return [RoleRequired(["doctor"])]
        return [RoleRequired(STOCK_ROLES)]

    def get_serializer_class(self):
        # Doctors get the availability-only view; everyone else sees real numbers.
        if self.request.user.role == "doctor" and self.action == "list":
            return serializers.ItemForPrescribingSerializer
        return serializers.ItemSerializer


class BatchViewSet(viewsets.ModelViewSet):
    """
    The pharmacy's stock ledger: receive a delivery, count the shelf, write
    off what expired. Every one of those goes through inventory.services so
    the quantity change and its StockMovement land together.
    """
    queryset = Batch.objects.select_related("item")
    serializer_class = serializers.BatchSerializer
    permission_classes = [RoleRequired]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["item"]

    def get_permissions(self): return [RoleRequired(STOCK_ROLES)]

    def perform_create(self, serializer):
        batch = serializer.save()
        receive_batch(batch=batch, actor=self.request.user)
        audit_event(actor=self.request.user, action="stock.received", instance=batch,
                    details={"quantity": batch.quantity}, request=self.request)

    @action(detail=True, methods=["post"])
    def count(self, request, pk=None):
        """Stock-take — submit what was physically on the shelf."""
        batch = self.get_object()
        raw = request.data.get("counted_quantity")
        try:
            counted = int(raw)
        except (TypeError, ValueError):
            return Response({"counted_quantity": "Enter the number counted."}, status=status.HTTP_400_BAD_REQUEST)
        before = batch.quantity
        try:
            batch = record_stock_count(batch=batch, counted_quantity=counted, actor=request.user,
                                       note=request.data.get("note", ""))
        except ValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="stock.counted", instance=batch,
                    details={"before": before, "counted": counted}, request=request)
        return Response(self.get_serializer(batch).data)

    @action(detail=True, methods=["post"])
    def write_off(self, request, pk=None):
        batch = self.get_object()
        try:
            batch = write_off_expired(batch=batch, actor=request.user, note=request.data.get("note", ""))
        except ValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="stock.written_off", instance=batch, request=request)
        return Response(self.get_serializer(batch).data)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = StockMovement.objects.select_related("batch__item", "performed_by")
    serializer_class = serializers.StockMovementSerializer
    permission_classes = [RoleRequired]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["batch", "reason"]

    def get_permissions(self): return [RoleRequired(STOCK_ROLES)]
