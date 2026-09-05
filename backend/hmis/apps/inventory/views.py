"""
The stock API.

Every endpoint that changes a quantity is a POST to an action that calls
`inventory/services.py`. There is no PUT or PATCH anywhere that can move
stock: `StockRecord` is read-only over the wire, `Batch` has no quantity to
write, and the model itself refuses a change that did not come from a
service. That is three layers saying the same thing on purpose — the
serializer so the field does not exist, the viewset so no route reaches it,
and the model so a future view cannot get it wrong.

Who may do it is unchanged: STOCK_ROLES (pharmacist, inventory manager, and
admin, which passes everything). Both locations are worked by the same
people in this hospital — the pharmacist runs the counter and the store
between them — so authority is per *operation*, not per location.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsAdmin, RoleRequired, STOCK_ROLES
from apps.core.config import ProtectedConfigMixin
from apps.core.services import audit_event

from . import serializers
from .models import (
    Batch, Item, ItemCategory, StockCount, StockLocation, StockMovement, StockRecord,
    StockTransfer, UnitOfMeasure, receiving_location,
)
from .services import (
    count_sheet, fefo_lines_for, post_stock_count, receive_stock, record_stock_count,
    transfer_stock, write_off_expired,
)


def _error(exc):
    messages = getattr(exc, "messages", None)
    return {"detail": "; ".join(messages) if messages else str(exc)}


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _CatalogueConfigViewSet(ProtectedConfigMixin, viewsets.ModelViewSet):
    """
    Catalogue configuration: **read by everyone who works stock, written by an
    admin**, and never deleted out from under the records that use it.

    That split is the workspace boundary. Administration configures inventory
    — what products exist, how they are grouped, what they are counted in —
    and Pharmacy operates pharmacy stock. A pharmacist needs to *read* the
    catalogue constantly (every dispense resolves a product, a unit and a
    price) but has no business renaming a category or retiring a drug, and
    removing the navigation link is not what stops them: this is.

    Both administration interfaces edit these same rows — the HMIS screens
    through here, Django admin through the ModelAdmin — so a category added
    in one exists immediately in the other. There is no second copy anywhere.
    """
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [RoleRequired(STOCK_ROLES)]
        return [IsAdmin()]

    def get_queryset(self):
        queryset = super().get_queryset()
        # The default listing is what is in use; `?all=1` is what the
        # administration screen asks for to show retired rows too.
        if (self.action == "list"
                and self.request.query_params.get("all") not in ("1", "true", "True")):
            queryset = queryset.filter(is_active=True)
        return queryset

    def perform_create(self, serializer):
        instance = serializer.save()
        audit_event(actor=self.request.user, action="config.created", instance=instance,
                    details={"model": instance._meta.label}, request=self.request)

    def perform_update(self, serializer):
        instance = serializer.save()
        audit_event(actor=self.request.user, action="config.updated", instance=instance,
                    details={"model": instance._meta.label}, request=self.request)


class ItemCategoryViewSet(_CatalogueConfigViewSet):
    """Product categories. Retired rather than deleted once products use one."""
    queryset = ItemCategory.objects.all()
    serializer_class = serializers.ItemCategorySerializer
    filterset_fields = ["is_active"]
    search_fields = ["name", "description"]
    ordering = ["display_order", "name"]
    protected_relations = ("items",)


class UnitOfMeasureViewSet(_CatalogueConfigViewSet):
    """Units of measure — what a product is counted and labelled in."""
    queryset = UnitOfMeasure.objects.all()
    serializer_class = serializers.UnitOfMeasureSerializer
    filterset_fields = ["is_active"]
    search_fields = ["name", "abbreviation", "description"]
    ordering = ["display_order", "name"]
    protected_relations = ("items",)


class StockLocationViewSet(ProtectedConfigMixin, viewsets.ModelViewSet):
    """
    Where stock stands. Main Store and Pharmacy are seeded by migration;
    adding a third is a row, which is the extensibility the two-location
    setup is built on.

    Read for anyone who works stock, written by admin only — the receiving
    and dispensing flags decide where every delivery lands and where every
    prescription draws from, so they are back-office configuration.
    """
    queryset = StockLocation.objects.all()
    serializer_class = serializers.StockLocationSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["kind", "is_active"]
    # A location that has ever held stock or been on a transfer is part of the
    # ledger; it is deactivated, never deleted.
    protected_relations = ("stock", "movements", "transfers_out", "transfers_in", "counts")

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [RoleRequired(STOCK_ROLES)]
        return [IsAdmin()]


class ItemViewSet(ProtectedConfigMixin, viewsets.ModelViewSet):
    """
    The product catalogue.

    **Everyone who works stock reads it; an admin maintains it.** The
    catalogue is what Administration configures and what Pharmacy consumes: a
    product created under Administration → Inventory → Products is available
    to the pharmacy for stock, batches, FEFO and dispensing the moment it
    exists, and nobody needs the configuration screen to hand over medicine.

    Reads stay wide for exactly that reason — the drug picker, the dispensing
    queue, the stock screens and the doctor's availability flag all resolve
    products here.

    A product with batches or prescriptions behind it is never deleted; it is
    deactivated, so every movement, prescription and bill that names it still
    reads correctly.
    """
    queryset = Item.objects.select_related("category", "unit")
    protected_relations = ("batches", "prescription_set")
    # The doctor's drug picker searches this list rather than filtering the
    # first page client-side, which quietly hid every drug past number 25.
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["category", "unit", "is_active"]
    search_fields = ["name", "category__name"]
    ordering = ["name"]

    def get_permissions(self):
        # getattr, not `.role` — get_permissions runs *before* authentication
        # has been established, so an anonymous request reaches here with an
        # AnonymousUser and must be refused, not crash with a 500.
        if self.action == "list" and getattr(self.request.user, "role", None) == "doctor":
            return [RoleRequired(["doctor"])]
        if self.request.method in permissions.SAFE_METHODS:
            return [RoleRequired(STOCK_ROLES)]
        # Maintaining the catalogue is configuration, not counter work.
        return [IsAdmin()]

    def get_serializer_class(self):
        # Doctors get the availability-only view; everyone else sees real numbers.
        if getattr(self.request.user, "role", None) == "doctor" and self.action == "list":
            return serializers.ItemForPrescribingSerializer
        return serializers.ItemSerializer


class BatchViewSet(viewsets.ModelViewSet):
    """
    The lots: what was delivered, when it expires, what it cost.

    Creating a batch **is** receiving a delivery — the units land in the
    receiving location (Main Store) unless the caller names another, and the
    movement is written by the service. Nothing here can change a quantity
    afterwards; that is a transfer, a count or a write-off.
    """
    queryset = Batch.objects.select_related("item").prefetch_related("stock__location")
    serializer_class = serializers.BatchSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["item"]

    def get_permissions(self):
        return [RoleRequired(STOCK_ROLES)]

    def get_queryset(self):
        queryset = super().get_queryset()
        # `?location=<id>` reads as "batches standing in this location", which
        # is what a store screen and a pharmacy screen each want.
        location = _as_int(self.request.query_params.get("location"))
        if location:
            queryset = queryset.filter(stock__location_id=location,
                                       stock__quantity__gt=0).distinct()
        return queryset

    def perform_create(self, serializer):
        opening = (serializer.validated_data.pop("opening_quantity", None)
                   or serializer.validated_data.pop("quantity", None))
        serializer.validated_data.pop("quantity", None)
        location = serializer.validated_data.pop("location", None)
        batch = serializer.save()
        if opening:
            destination = location or receiving_location()
            receive_stock(batch=batch, quantity=opening, actor=self.request.user,
                          location=destination)
            audit_event(actor=self.request.user, action="stock.received", instance=batch,
                        details={"quantity": opening,
                                 "location": destination.code if destination else None},
                        request=self.request)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """
        A further delivery of a lot already on file — the same batch number
        arriving again, which is one shipment split across two deliveries.
        """
        batch = self.get_object()
        quantity = _as_int(request.data.get("quantity"))
        if not quantity or quantity < 1:
            return Response({"quantity": "How many units arrived?"},
                            status=status.HTTP_400_BAD_REQUEST)
        location = self._location(request)
        try:
            receive_stock(batch=batch, quantity=quantity, actor=request.user,
                          location=location or receiving_location())
        except (ValidationError, PermissionDenied) as exc:
            return Response(_error(exc), status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="stock.received", instance=batch,
                    details={"quantity": quantity}, request=request)
        return Response(self.get_serializer(batch).data)

    @action(detail=True, methods=["post"])
    def count(self, request, pk=None):
        """
        Stock-take of one batch at one location — submit what was physically
        on that shelf.

        **`location` is required, with no default.** A count is a statement
        that somebody stood in front of a shelf and counted; guessing which
        shelf they meant would post the difference as an adjustment against
        the wrong location, inventing stock in one and destroying it in the
        other. Making the caller say it is the only safe reading.
        """
        batch = self.get_object()
        counted = _as_int(request.data.get("counted_quantity"))
        if counted is None:
            return Response({"counted_quantity": "Enter the number counted."},
                            status=status.HTTP_400_BAD_REQUEST)
        location = self._location(request)
        if location is None:
            return Response(
                {"location": "Which location was counted? A count is posted against "
                             "one shelf, so it has to name it."},
                status=status.HTTP_400_BAD_REQUEST)
        before = batch.quantity_at(location)
        try:
            record_stock_count(batch=batch, location=location, counted_quantity=counted,
                               actor=request.user, note=request.data.get("note", ""))
        except (ValidationError, PermissionDenied) as exc:
            return Response(_error(exc), status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="stock.counted", instance=batch,
                    details={"before": before, "counted": counted,
                             "location": location.code if location else None},
                    request=request)
        return Response(self.get_serializer(batch).data)

    @action(detail=True, methods=["post"])
    def write_off(self, request, pk=None):
        """
        Take an expired lot off the shelf. With no `location`, it clears
        every location holding it — expired stock is expired everywhere.
        """
        batch = self.get_object()
        try:
            write_off_expired(batch=batch, actor=request.user,
                              location=self._location(request),
                              note=request.data.get("note", ""))
        except (ValidationError, PermissionDenied) as exc:
            return Response(_error(exc), status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="stock.written_off", instance=batch,
                    request=request)
        return Response(self.get_serializer(batch).data)

    def _location(self, request):
        location_id = _as_int(request.data.get("location"))
        return StockLocation.objects.filter(pk=location_id).first() if location_id else None


class StockRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Product + batch + location. **Read-only over the API, by design** — this
    is the number stock control turns on, and the only things that may move
    it are a receipt, a transfer, a count, a write-off or a dispense.
    """
    queryset = StockRecord.objects.select_related("batch__item", "location")
    serializer_class = serializers.StockRecordSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["location", "batch", "batch__item"]
    search_fields = ["batch__item__name", "batch__batch_no"]

    def get_permissions(self):
        return [RoleRequired(STOCK_ROLES)]


class StockTransferViewSet(viewsets.ModelViewSet):
    """
    Internal movement between locations — Main Store → Pharmacy, normally.

    POST only creates; a transfer is applied when it is created and is a
    completed fact afterwards, so there is no update or delete. Lines may
    name batches explicitly, or name an item and let the server pick FEFO
    out of the source location.
    """
    queryset = (StockTransfer.objects
                .select_related("source", "destination", "transferred_by")
                .prefetch_related("lines__batch__item"))
    serializer_class = serializers.StockTransferSerializer
    http_method_names = ["get", "post", "head", "options"]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["source", "destination"]

    def get_permissions(self):
        return [RoleRequired(STOCK_ROLES)]

    def create(self, request, *args, **kwargs):
        source = StockLocation.objects.filter(pk=_as_int(request.data.get("source"))).first()
        destination = StockLocation.objects.filter(
            pk=_as_int(request.data.get("destination"))).first()
        if source is None or destination is None:
            return Response(
                {"detail": "Name the location the stock is leaving and the one it is going to."},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            lines = self._lines(request.data.get("lines") or [], source)
        except ValidationError as exc:
            return Response(_error(exc), status=status.HTTP_400_BAD_REQUEST)

        try:
            transfer = transfer_stock(source=source, destination=destination, lines=lines,
                                      actor=request.user, note=request.data.get("note", ""))
        except (ValidationError, PermissionDenied) as exc:
            return Response(_error(exc), status=status.HTTP_400_BAD_REQUEST)

        audit_event(actor=request.user, action="stock.transferred", instance=transfer,
                    details={"source": source.code, "destination": destination.code,
                             "units": transfer.total_units}, request=request)
        return Response(self.get_serializer(transfer).data, status=status.HTTP_201_CREATED)

    def _lines(self, raw, source):
        """
        Turn the posted lines into `[{batch, quantity}]`.

        A line is either `{"batch": id, "quantity": n}` — the pharmacist
        moving a named lot — or `{"item": id, "quantity": n}`, which the
        server resolves FEFO out of the source so the short-dated stock moves
        to the counter first instead of ageing in the store.
        """
        if not isinstance(raw, list) or not raw:
            return []
        lines = []
        for entry in raw:
            entry = entry or {}
            quantity = _as_int(entry.get("quantity"))
            if not quantity or quantity < 1:
                raise ValidationError("Every line needs a quantity of at least 1.")
            batch_id = _as_int(entry.get("batch"))
            if batch_id:
                batch = Batch.objects.filter(pk=batch_id).first()
                if batch is None:
                    raise ValidationError("One of these batches is no longer on file.")
                lines.append({"batch": batch, "quantity": quantity})
                continue
            item = Item.objects.filter(pk=_as_int(entry.get("item"))).first()
            if item is None:
                raise ValidationError("Name a batch, or an item to pick one by expiry.")
            lines.extend(fefo_lines_for(item=item, quantity=quantity, location=source))
        return _merge(lines)


def _merge(lines):
    """
    Fold duplicate batches into one line.

    Two FEFO-resolved items can land on the same batch, and the service
    rejects a batch named twice — correctly, because two lines against one
    lot is how a transfer double-counts. Merging here keeps that check strict
    without making the caller do the arithmetic.
    """
    merged = {}
    for line in lines:
        key = line["batch"].pk
        if key in merged:
            merged[key]["quantity"] += line["quantity"]
        else:
            merged[key] = dict(line)
    return list(merged.values())


class StockCountViewSet(viewsets.ModelViewSet):
    """
    Physical inventory, one location at a time.

    `GET /stock-counts/sheet/?location=<id>` is the sheet to walk the shelves
    with — product, batch, location, system quantity — and POSTing the
    counted numbers back files the count and posts one adjustment per line
    that differs.
    """
    queryset = (StockCount.objects
                .select_related("location", "counted_by")
                .prefetch_related("lines__batch__item"))
    serializer_class = serializers.StockCountSerializer
    http_method_names = ["get", "post", "head", "options"]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["location"]

    def get_permissions(self):
        return [RoleRequired(STOCK_ROLES)]

    @action(detail=False, methods=["get"])
    def sheet(self, request):
        """
        The count sheet: every batch standing in this location, earliest
        expiry first, with what the system believes is there. `counted_qty`
        and the difference are what the person walking the shelves fills in.
        """
        location = StockLocation.objects.filter(
            pk=_as_int(request.query_params.get("location"))).first()
        if location is None:
            location = receiving_location()
        if location is None:
            return Response({"detail": "No stock locations are configured."},
                            status=status.HTTP_400_BAD_REQUEST)
        item = Item.objects.filter(pk=_as_int(request.query_params.get("item"))).first()
        rows = count_sheet(
            location=location, item=item,
            include_empty=request.query_params.get("include_empty") in ("1", "true", "True"),
        )
        return Response({
            "location": serializers.StockLocationSerializer(location).data,
            "lines": [
                {
                    "batch": record.batch_id,
                    "item": record.batch.item_id,
                    "item_name": record.batch.item.name,
                    "item_unit": record.batch.item.unit_label,
                    "batch_no": record.batch.batch_no,
                    "expiry_date": record.batch.expiry_date,
                    "is_expired": record.batch.is_expired,
                    "location": record.location_id,
                    "location_name": record.location.name,
                    "system_quantity": record.quantity,
                }
                for record in rows
            ],
        })

    def create(self, request, *args, **kwargs):
        location = StockLocation.objects.filter(
            pk=_as_int(request.data.get("location"))).first()
        if location is None:
            return Response({"location": "Which location was counted?"},
                            status=status.HTTP_400_BAD_REQUEST)

        lines = []
        for entry in request.data.get("lines") or []:
            entry = entry or {}
            batch = Batch.objects.filter(pk=_as_int(entry.get("batch"))).first()
            if batch is None:
                return Response({"lines": "One of these batches is no longer on file."},
                                status=status.HTTP_400_BAD_REQUEST)
            counted = _as_int(entry.get("counted_quantity"))
            if counted is None:
                return Response(
                    {"lines": f"Enter the number counted for {batch.item.name} "
                              f"batch {batch.batch_no}."},
                    status=status.HTTP_400_BAD_REQUEST)
            lines.append({"batch": batch, "counted_quantity": counted})

        try:
            count = post_stock_count(location=location, lines=lines, actor=request.user,
                                     note=request.data.get("note", ""))
        except (ValidationError, PermissionDenied) as exc:
            return Response(_error(exc), status=status.HTTP_400_BAD_REQUEST)

        audit_event(actor=request.user, action="stock.counted", instance=count,
                    details={"location": location.code,
                             "lines": len(lines),
                             "discrepancies": count.discrepancy_count}, request=request)
        return Response(self.get_serializer(count).data, status=status.HTTP_201_CREATED)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = StockMovement.objects.select_related(
        "batch__item", "location", "performed_by", "transfer")
    serializer_class = serializers.StockMovementSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["batch", "reason", "location", "transfer"]

    def get_permissions(self):
        return [RoleRequired(STOCK_ROLES)]
