"""
The pharmacy POS API.

Two role groups decide everything here, and both come from
`accounts/permissions.py`: POS_ROLES (pharmacist, cashier) operate the till —
open and close a register, search products, hold, complete, discard — and
POS_HISTORY_ROLES add the accountant, who reads sales and registers to
reconcile them but never rings one up. A POS return needs POS_RETURN_ROLES
(cashier): money out of a till. A discount is refused inside the service unless
the operator is in POS_DISCOUNT_ROLES. Admins pass all of them.

Every write is a POST to an action that calls `sales/services.py`; there is no
PUT, PATCH or DELETE, and the read serializers have no writable field.
"""
from decimal import Decimal

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import POS_HISTORY_ROLES, POS_RETURN_ROLES, POS_ROLES, RoleRequired
from apps.billing import reporting
from apps.billing.models import Payment, Refund
from apps.inventory.models import Item, StockRecord, dispensing_location
from apps.patients.access import patient_queryset_for

from . import services
from .discount_policy import DiscountRefused
from .models import PosRegister, Sale, SaleReturn
from .serializers import (PosRegisterSerializer, ReturnInput, SaleInput, SaleReturnSerializer,
                          SaleSerializer)

ZERO = Decimal("0.00")


def _refusal(exc):
    """A service's refusal, as the API answers it — the service's own words."""
    if isinstance(exc, DiscountRefused):
        # 403 for "you are not allowed to", 400 for "this is not a thing you
        # may ask for". `code` is what the till branches on: an approval
        # prompt for `discount_needs_approval`, a plain refusal otherwise.
        needs_authority = exc.code in {"discount_needs_approval", "discount_approval_failed"}
        return Response({"detail": exc.message, "code": exc.code, **exc.detail},
                        status=status.HTTP_403_FORBIDDEN if needs_authority
                        else status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, DjangoPermissionDenied):
        return Response({"detail": str(exc) or "You are not allowed to do that.",
                         "code": "forbidden"}, status=status.HTTP_403_FORBIDDEN)
    messages = getattr(exc, "messages", None)
    return Response({"detail": "; ".join(messages) if messages else str(exc)},
                    status=status.HTTP_400_BAD_REQUEST)


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _money(value):
    return f"{(value or ZERO):.2f}"


def _requested_action(view):
    """
    The action a request is addressed to, even when the method is not one the
    action takes. A GET on `/sales/complete/` leaves `view.action` None, and
    falling through to the widest group answered the accountant with a 405 —
    "you reached it" — instead of the 403 the till's own audience decides.
    """
    if view.action:
        return view.action
    match = getattr(view.request, "resolver_match", None)
    name = getattr(match, "url_name", "") or ""
    prefix = f"{view.basename}-"
    return name[len(prefix):].replace("-", "_") if name.startswith(prefix) else None


class PosRegisterViewSet(viewsets.ReadOnlyModelViewSet):
    """Till sessions: open, current, close, and the reconciliation of each."""
    serializer_class = PosRegisterSerializer

    def get_permissions(self):
        if _requested_action(self) in ("open", "current", "close"):
            return [RoleRequired(POS_ROLES)]
        return [RoleRequired(POS_HISTORY_ROLES)]

    def get_queryset(self):
        registers = PosRegister.objects.select_related("location", "opened_by", "closed_by")
        if self.action == "list":
            params = self.request.query_params
            if params.get("status") in ("open", "closed"):
                registers = registers.filter(status=params["status"])
            operator = _as_int(params.get("opened_by"))
            if operator:
                registers = registers.filter(opened_by_id=operator)
        return registers

    @action(detail=False, methods=["get"])
    def current(self, request):
        register = services.current_register(request.user)
        if register is None:
            return Response({"register": None, "summary": None})
        return Response({"register": PosRegisterSerializer(register).data,
                         "summary": services.register_summary(register)})

    @action(detail=False, methods=["post"])
    def open(self, request):
        try:
            register = services.open_register(operator=request.user,
                                              opening_float=request.data.get("opening_float") or 0,
                                              request=request)
        except (ValidationError, DjangoPermissionDenied) as exc:
            return _refusal(exc)
        return Response({"register": PosRegisterSerializer(register).data,
                         "summary": services.register_summary(register)},
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        register = self.get_object()
        summary = (register.closing_summary if register.status == "closed" and register.closing_summary
                   else services.register_summary(register))
        return Response({"register": PosRegisterSerializer(register).data, "summary": summary})

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        register = self.get_object()
        try:
            closed = services.close_register(register=register, actor=request.user,
                                             counted_cash=request.data.get("counted_cash"),
                                             note=request.data.get("note", ""), request=request)
        except (ValidationError, DjangoPermissionDenied) as exc:
            return _refusal(exc)
        return Response({"register": PosRegisterSerializer(closed).data,
                         "summary": closed.closing_summary})


class SaleViewSet(viewsets.ReadOnlyModelViewSet):
    """
    POS sales: the till's actions and the sales history.

    History is kept apart from prescription dispensing on purpose — that stays
    on `/prescriptions/` and the Pharmacy counter, exactly as it was.
    """
    serializer_class = SaleSerializer

    def get_permissions(self):
        action = _requested_action(self)
        if action in ("products", "hold", "complete", "discard"):
            return [RoleRequired(POS_ROLES)]
        if action == "return_items":
            return [RoleRequired(POS_RETURN_ROLES)]
        return [RoleRequired(POS_HISTORY_ROLES)]

    def get_queryset(self):
        sales = (Sale.objects
                 .select_related("patient", "sold_by", "discount_by", "discount_approved_by",
                                 "register")
                 .prefetch_related("lines__item__unit", "lines__pieces__batch",
                                   "returns__processed_by", "returns__register",
                                   "returns__lines__sale_item__batch__item")
                 .annotate(returned_total=Coalesce(
                     Sum("returns__amount"), Value(ZERO),
                     output_field=DecimalField(max_digits=12, decimal_places=2)))
                 .order_by("-created_at"))
        # Filters narrow the list only — never `get_object()` (rule 21).
        if self.action != "list":
            return sales
        params = self.request.query_params
        for field in ("status", "customer_type", "payment_method"):
            if params.get(field):
                sales = sales.filter(**{field: params[field]})
        for field in ("sold_by", "register", "patient"):
            value = _as_int(params.get(field))
            if value:
                sales = sales.filter(**{f"{field}_id": value})
        date_from, date_to = parse_date(params.get("date_from") or ""), parse_date(params.get("date_to") or "")
        if date_from:
            sales = sales.filter(created_at__date__gte=date_from)
        if date_to:
            sales = sales.filter(created_at__date__lte=date_to)
        term = (params.get("search") or "").strip()
        if term:
            # A subquery rather than a join, so matching two lines of one sale
            # cannot double the returned total annotated above.
            matching = Sale.objects.filter(
                Q(reference__icontains=term) | Q(customer_name__icontains=term)
                | Q(customer_phone__icontains=term) | Q(patient__first_name__icontains=term)
                | Q(patient__last_name__icontains=term) | Q(patient__patient_number__icontains=term)
                | Q(lines__item__name__icontains=term) | Q(lines__item__sku__iexact=term)
                | Q(lines__item__barcode__iexact=term)
            ).values("pk")
            sales = sales.filter(pk__in=matching)
        product = _as_int(params.get("product"))
        if product:
            sales = sales.filter(pk__in=Sale.objects.filter(lines__item_id=product).values("pk"))
        return sales

    def _fresh(self, sale):
        return self.get_queryset().get(pk=sale.pk)

    def _patient(self, values):
        if values.get("customer_type") != "patient":
            return None
        patient_id = values.get("patient")
        if not patient_id:
            raise ValidationError("Pick the registered patient this sale is for.")
        # The same scoping the patient list applies (rule 9): a till cannot be
        # used to reach a patient the operator could not look up.
        patient = patient_queryset_for(self.request.user).filter(pk=patient_id).first()
        if patient is None:
            raise ValidationError("That patient could not be found.")
        return patient

    def _held(self, values):
        held_id = values.get("held_sale")
        if not held_id:
            return None
        sale = Sale.objects.filter(pk=held_id).first()
        if sale is None:
            raise ValidationError("That held sale no longer exists.")
        return sale

    @action(detail=False, methods=["get"])
    def products(self, request):
        """
        The till's product search: name, category, SKU or barcode. Availability
        is what stands on the dispensing shelf, unexpired; the price shown is
        the next batch FEFO would sell. What a sale actually charges is decided
        when it completes, batch by batch.
        """
        location = dispensing_location()
        items = Item.objects.filter(is_active=True).select_related("category", "unit")
        category = _as_int(request.query_params.get("category"))
        if category:
            items = items.filter(category_id=category)
        # `?ids=3,7` — the products on a held cart being resumed, so the till
        # can show their current price and what is on the shelf.
        ids = [pk for pk in (_as_int(part) for part in
                             (request.query_params.get("ids") or "").split(",")) if pk]
        if ids:
            items = items.filter(pk__in=ids)
        term = (request.query_params.get("search") or "").strip()
        exact = None
        if term:
            exact = items.filter(Q(barcode__iexact=term) | Q(sku__iexact=term)).first()
            items = items.filter(Q(name__icontains=term) | Q(sku__icontains=term)
                                 | Q(barcode__icontains=term) | Q(category__name__icontains=term))
        items = list(items.order_by("name")[:60])
        if exact is not None and exact.pk not in {item.pk for item in items}:
            items.insert(0, exact)

        stock = {}
        if location is not None and items:
            records = (StockRecord.objects
                       .filter(location=location, quantity__gt=0, batch__item__in=items)
                       .exclude(batch__expiry_date__lt=timezone.localdate())
                       .select_related("batch").order_by("batch__expiry_date", "batch_id"))
            for record in records:
                entry = stock.setdefault(record.batch.item_id,
                                         {"available": 0, "price": record.batch.sale_price})
                entry["available"] += record.quantity

        results = [{
            "id": item.pk, "name": item.name, "sku": item.sku or "", "barcode": item.barcode or "",
            "category": item.category_id, "category_name": item.category_name,
            "unit_label": item.unit_label, "strength": item.strength,
            "dosage_form": item.dosage_form,
            "available": stock.get(item.pk, {}).get("available", 0),
            "price": _money(stock[item.pk]["price"]) if item.pk in stock else None,
        } for item in items]
        if exact is not None:
            results.sort(key=lambda row: row["id"] != exact.pk)
        return Response({"location": location.name if location else None,
                         "exact_match": exact.pk if exact else None,
                         "categories": self._categories(),
                         "results": results})

    @staticmethod
    def _categories():
        """
        The category chips above the till's product grid.

        Computed over the **whole** catalogue, not over the results beside it,
        so picking a category does not collapse the strip to the one chip you
        picked, and a category whose drugs sort past the sixtieth result still
        appears. It is served from here rather than read from
        `/item-categories/` because a cashier works the till but is not in
        STOCK_ROLES: sending the POS to the configuration endpoint would have
        widened who may read the catalogue in order to draw a row of buttons.
        """
        rows = (Item.objects.filter(is_active=True, category__isnull=False,
                                    category__is_active=True)
                .values("category_id", "category__name", "category__display_order")
                .distinct()
                .order_by("category__display_order", "category__name"))
        return [{"id": row["category_id"], "name": row["category__name"]} for row in rows]

    @action(detail=False, methods=["post"])
    def hold(self, request):
        data = SaleInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        try:
            sale = services.hold_sale(
                operator=request.user, lines=values["lines"],
                customer_type=values["customer_type"], patient=self._patient(values),
                customer_name=values.get("customer_name", ""),
                customer_phone=values.get("customer_phone", ""),
                sale=self._held(values), request=request)
        except (ValidationError, DjangoPermissionDenied, DiscountRefused) as exc:
            return _refusal(exc)
        return Response(SaleSerializer(self._fresh(sale)).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def complete(self, request):
        data = SaleInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        try:
            sale, created = services.complete_sale(
                operator=request.user, lines=values["lines"],
                customer_type=values["customer_type"], patient=self._patient(values),
                customer_name=values.get("customer_name", ""),
                customer_phone=values.get("customer_phone", ""),
                discount=values.get("discount"),
                discount_reason=values.get("discount_reason", ""),
                authorization=values.get("authorization"),
                payment_method=values["payment_method"],
                amount_tendered=values.get("amount_tendered"),
                client_token=values.get("client_token"), held_sale=self._held(values),
                request=request)
        except (ValidationError, DjangoPermissionDenied, DiscountRefused, ValueError) as exc:
            return _refusal(exc)
        return Response(SaleSerializer(self._fresh(sale)).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def discard(self, request, pk=None):
        sale = self.get_object()
        try:
            services.discard_held_sale(sale=sale, actor=request.user, request=request)
        except (ValidationError, DjangoPermissionDenied) as exc:
            return _refusal(exc)
        return Response(SaleSerializer(self._fresh(sale)).data)

    @action(detail=True, methods=["post"], url_path="return")
    def return_items(self, request, pk=None):
        sale = self.get_object()
        data = ReturnInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        try:
            sale_return = services.process_return(
                sale=sale, operator=request.user, lines=values["lines"],
                reason=values.get("reason", ""), refund_method=values.get("refund_method") or None,
                request=request)
        except (ValidationError, DjangoPermissionDenied, ValueError) as exc:
            return _refusal(exc)
        return Response({"return": SaleReturnSerializer(sale_return).data,
                         "sale": SaleSerializer(self._fresh(sale)).data},
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def returns(self, request):
        """
        The POS returns register: every return, newest first — what came back,
        off which batch, what went back and how, who took it and why. Read-only;
        a return is taken against its sale (`POST /sales/<id>/return/`).
        """
        returns = (SaleReturn.objects
                   .select_related("sale__patient", "register", "processed_by")
                   .prefetch_related("lines__sale_item__batch__item")
                   .order_by("-created_at"))
        params = request.query_params
        date_from, date_to = parse_date(params.get("date_from") or ""), parse_date(params.get("date_to") or "")
        if date_from:
            returns = returns.filter(created_at__date__gte=date_from)
        if date_to:
            returns = returns.filter(created_at__date__lte=date_to)
        if params.get("refund_method"):
            returns = returns.filter(refund_method=params["refund_method"])
        term = (params.get("search") or "").strip()
        if term:
            returns = returns.filter(
                Q(reference__icontains=term) | Q(sale__reference__icontains=term)
                | Q(sale__customer_name__icontains=term) | Q(reason__icontains=term)
                | Q(sale__patient__patient_number__icontains=term))
        page = self.paginate_queryset(returns)
        data = SaleReturnSerializer(page if page is not None else returns, many=True).data
        return self.get_paginated_response(data) if page is not None else Response(data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """
        POS takings for a period, and the Pharmacy department's takings split
        by where they came from — every figure read off the `Payment` and
        `Refund` rows the finance report counts.
        """
        params = request.query_params
        try:
            period = reporting.resolve_period(params.get("preset") or "today",
                                              params.get("from"), params.get("to"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # One implementation of the POS figures: the finance report's own block.
        pos = reporting.pos_sales(period)
        methods = (period.filter(Payment.objects.filter(pos_sale__isnull=False))
                   .values("method").annotate(amount=Sum("amount"), count=Count("id"))
                   .order_by("-amount"))
        labels = dict(Payment.METHOD)
        return Response({
            "period": period.as_dict(),
            "sales": {"count": pos["count"], "gross": _money(pos["gross"]),
                      "discounts": _money(pos["discounts"]), "total": _money(pos["paid"])},
            "walk_in": pos["walk_in"], "registered": pos["registered"],
            "reconciles": pos["reconciles"],
            "methods": [{"key": row["method"], "label": labels.get(row["method"], row["method"]),
                         "amount": _money(row["amount"]), "count": row["count"]} for row in methods],
            "refunds": {"amount": _money(pos["returned"]), "count": pos["returns_count"]},
            "categories": self._by_category(period),
            "discounts": self._discounts(period),
            "pharmacy": reporting.pharmacy_sales(period),
        })

    @staticmethod
    def _discounts(period):
        """
        What the till gave away, and on whose authority.

        A breakdown of `sales.discounts` — the same `Sale` rows the block above
        totals, over the same payment timestamps — never a second figure: the
        three groupings each add up to it, which `reconciles` asserts. It exists
        because "pharmacy discounts" as one number cannot answer the question an
        accountant actually asks, which is *who* and *why*.

        Aggregated in the database, three queries whatever the period holds.
        """
        sales = period.filter(Sale.objects.filter(status="completed", payment__isnull=False,
                                                  discount_amount__gt=0),
                              field="payment__created_at")

        def group(*fields, label):
            rows = (sales.values(*fields)
                    .annotate(amount=Coalesce(Sum("discount_amount"), ZERO), count=Count("id"))
                    .order_by("-amount"))
            return [{"key": label(row), "label": label(row), "amount": _money(row["amount"]),
                     "count": row["count"]} for row in rows]

        given = sales.aggregate(v=Coalesce(Sum("discount_amount"), ZERO))["v"] or ZERO
        # A sale whose only discount is on its lines has no sale-wide type, so
        # it is named rather than left under a blank key.
        by_type = group("discount_type",
                        label=lambda row: dict(Sale.DISCOUNT).get(row["discount_type"])
                        or "Item discounts only")
        by_reason = group("discount_reason",
                          label=lambda row: row["discount_reason"] or "No reason recorded")
        by_cashier = group("discount_by__first_name", "discount_by__last_name",
                           "discount_by__username",
                           label=lambda row: (f'{row["discount_by__first_name"]} '
                                              f'{row["discount_by__last_name"]}').strip()
                           or row["discount_by__username"] or "—")
        approved = period.filter(
            Sale.objects.filter(status="completed", payment__isnull=False,
                                discount_approved_by__isnull=False),
            field="payment__created_at").aggregate(
                amount=Coalesce(Sum("discount_amount"), ZERO), count=Count("id"))
        return {
            "total": _money(given),
            "by_type": by_type, "by_reason": by_reason, "by_cashier": by_cashier,
            # What needed authorising above a cashier's limit, which is the row
            # an audit looks for first.
            "approved": {"amount": _money(approved["amount"]), "count": approved["count"]},
            # Decimals, not the formatted strings beside them: comparing
            # `Decimal("1400.00")` with `"1400.00"` is quietly always False,
            # which would report every period as not reconciling.
            "reconciles": all(sum((Decimal(row["amount"]) for row in rows), ZERO) == given
                              for rows in (by_type, by_reason, by_cashier)),
        }

    @staticmethod
    def _by_category(period):
        """
        What the till sold, by what kind of thing it was.

        The same `SaleLine` figures the block above totals, grouped by the
        product's own category — so it is a breakdown of money already
        reported, never a second calculation of it: the categories' `gross`,
        `discounts` and `net` add up to the period's `sales` figures. Aggregated
        in the database, one query, whatever the till sold.

        A product with no category is reported as "Uncategorised" rather than
        dropped, the same way `reporting.department_for` never drops a charge.
        """
        from .models import SaleLine

        rows = (period.filter(SaleLine.objects.filter(sale__status="completed",
                                                      sale__payment__isnull=False),
                              field="sale__payment__created_at")
                .values("item__category_id", "item__category__name")
                .annotate(units=Coalesce(Sum("quantity"), 0),
                          lines=Count("id"),
                          gross=Coalesce(Sum("gross"), ZERO, output_field=DecimalField()),
                          discounts=Coalesce(Sum("discount"), ZERO, output_field=DecimalField()),
                          net=Coalesce(Sum("net"), ZERO, output_field=DecimalField()))
                .order_by("-net"))
        return [{
            "category": row["item__category_id"],
            "label": row["item__category__name"] or "Uncategorised",
            "units": row["units"], "lines": row["lines"],
            "gross": _money(row["gross"]), "discounts": _money(row["discounts"]),
            "net": _money(row["net"]),
        } for row in rows]
