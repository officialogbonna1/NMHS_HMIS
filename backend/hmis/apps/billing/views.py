from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from django.db import models
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.patients.models import Patient
from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from apps.core.services import audit_event
from apps.core.config import ProtectedConfigMixin
from apps.accounts.permissions import BILLING_ROLES, CANCEL_ROLES, REFUND_ROLES, IsAdmin
from .models import (PatientLedger, Charge, Payment, Adjustment, BillingItem, Refund,
                     PaymentAllocation, PaymentDeferral)
from .serializers import (PatientLedgerSerializer, ChargeSerializer, PaymentSerializer,
                          AdjustmentSerializer, BillingItemSerializer, RefundSerializer,
                          RefundRequestSerializer)
from .services import (add_charge, record_payment, refresh_ledger, waive_charge, cancel_charge,
                       apply_percentage_discount, apply_amount_discount, discount_patient_balance,
                       defer_charge, refund_payment, cancel_and_refund, refund_charge,
                       refundable_for_charge, RefundRequired)
from .withdrawn import annotate_withdrawn, withdrawn_q
from apps.accounts.permissions import RoleRequired
from rest_framework.views import APIView
from . import reporting

# BILLING_ROLES lives in accounts.permissions — the counter roles are named
# in one place so the frontend guards can mirror them.


def _discount_error(exc):
    return str(exc) if isinstance(exc, ValueError) else "Enter a percentage between 0 and 100."
# The pharmacy takes money at its own counter for what it dispenses, so it
# reads ledgers/charges and records payments — but never waives or voids.
COLLECTING_ROLES = BILLING_ROLES + ["pharmacist"]

# A payment is stamped with where it was taken, derived from the collector's
# role rather than trusted from the request body.
PAYMENT_CHANNEL_BY_ROLE = {"pharmacist": "pharmacy", "cashier": "cashier", "accountant": "cashier"}

# Who prices the catalogue. Cashiers keep it because they are the ones who
# find out a service has no price when a patient is standing at the counter.
CATALOG_ROLES = ["cashier", "accountant"]

# Who reads the hospital's financial report. The cash desk and the accounts
# office, plus admin — the roles that already read every charge, payment and
# adjustment through the endpoints below, so this endpoint shows them nothing
# they could not already reach; it only spares them summing it by hand.
#
# Reception is deliberately *not* here even though it is in BILLING_ROLES. The
# front desk bills and collects at a window; hospital-wide revenue by
# department is a management figure, and the boundary drawn on discounts and
# waivers (rule 13) is the same one drawn here.
FINANCE_REPORT_ROLES = ["cashier", "accountant"]


class BillingItemViewSet(ProtectedConfigMixin, viewsets.ModelViewSet):
    """
    The price list. Read by everyone who quotes a price; priced by the desks
    that find out at the window that a service has none.

    A priced item a laboratory test points at is configuration with history
    behind it: deactivate it so old orders keep their price, rather than
    deleting it and leaving them pointing at nothing.
    """
    queryset = BillingItem.objects.all(); serializer_class = BillingItemSerializer
    filter_backends = [DjangoFilterBackend]; filterset_fields = ["is_active", "category"]
    protected_relations = ("lab_tests",)
    def get_permissions(self):
        # Everyone signed in can read the price list — reception bills from
        # it, the pharmacy quotes from it.
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [RoleRequired(CATALOG_ROLES)]
class LedgerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    A ledger per patient. `?owing=true` is the debtors list — filtered in the
    database, biggest balance first, so it stays right past the first page
    instead of being whatever a client happened to fetch and sort.
    """
    queryset = PatientLedger.objects.select_related("patient")
    serializer_class = PatientLedgerSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["patient"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number"]

    def get_permissions(self): return [RoleRequired(COLLECTING_ROLES)]

    def get_queryset(self):
        queryset = super().get_queryset().annotate(
            balance=models.F("total_charges") - models.F("total_payments") - models.F("total_adjustments")
        )
        if self.request.query_params.get("owing") in ("true", "1", "yes"):
            # Settling in full is what takes somebody off this list.
            return queryset.filter(balance__gt=0).order_by("-balance")
        return queryset.order_by("patient__last_name", "patient__first_name")
_MONEY = models.DecimalField(max_digits=12, decimal_places=2)


def _no_money():
    return models.Value(Decimal("0"), output_field=_MONEY)


def _cents(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _day_bound(value, name, *, end=False):
    """
    `?created_from=` / `?created_to=` as the half-open datetime bounds a b-tree
    index can read — the same shape `reporting.Period` filters by. The `to`
    date is inclusive, the way a person reads a range.
    """
    if not value:
        return None
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValidationError({name: "Use a date in the form YYYY-MM-DD."})
    if end:
        day += timedelta(days=1)
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def _truthy(value):
    return str(value or "").lower() in ("1", "true", "yes")


def _outstanding(patient_id):
    ledger = PatientLedger.objects.filter(patient_id=patient_id).first()
    return ledger.outstanding_balance if ledger else Decimal("0")


def _cancellation_audit(charge, *, amount_paid, amount_refunded, refunds=(),
                        outstanding_before=None, outstanding_after=None):
    """
    What a cancellation writes to the audit trail: everything a reader six
    months on needs without opening another table — the patient and their
    number, the service and the department that earned it, what it cost and
    what had been paid, what went back and from which payments, why, and the
    state it was left in. The actor and the time are the audit row's own
    columns. `amount`, `reason` and `outstanding_after` keep the names earlier
    readers of these rows already use.
    """
    def money(value):
        return None if value is None else f"{Decimal(value):.2f}"

    patient = charge.patient
    refunded = Decimal(amount_refunded or 0)
    reason = charge.cancellation_reason
    if refunded > 0:
        summary = (f"{charge.description} was cancelled because it was not used, and the "
                   f"{refunded:,.2f} the patient paid for it was refunded.")
    else:
        summary = (f"{charge.description} was cancelled because it was not used; nothing had "
                   f"been paid for it, so no money moved.")
    return {
        "summary": summary,
        "patient": str(patient),
        "patient_number": patient.patient_number,
        "patient_uuid": str(patient.uuid),
        "charge": charge.pk,
        "service": charge.description,
        "source_type": charge.source_type,
        "department": charge.department.name if charge.department_id else None,
        "department_code": charge.department.code if charge.department_id else None,
        "original_charge": money(charge.amount),
        "amount": money(charge.amount),
        "amount_discounted": money(charge.amount_discounted),
        "amount_waived": money(charge.amount_waived),
        "amount_paid": money(amount_paid or 0),
        "amount_refunded": money(refunded),
        "cancellation_reason": reason,
        "reason": reason,
        "refund_reason": reason if refunded > 0 else None,
        "refunds": [{"refund": r.pk, "payment": r.payment_id, "amount": money(r.amount),
                     "method": r.method} for r in refunds],
        "resulting_status": charge.status,
        "cancelled_at": charge.cancelled_at.isoformat() if charge.cancelled_at else None,
        "outstanding_before": money(outstanding_before),
        "outstanding_after": money(outstanding_after),
    }


class ListSearchFilter(filters.SearchFilter):
    """`?search=` narrows a list. It never hides a charge its own detail route names."""

    def filter_queryset(self, request, queryset, view):
        if getattr(view, "action", None) != "list":
            return queryset
        return super().filter_queryset(request, queryset, view)


class ChargeViewSet(viewsets.ModelViewSet):
    queryset = Charge.objects.select_related("patient", "department", "cancelled_by")
    serializer_class = ChargeSerializer
    filter_backends = [DjangoFilterBackend, ListSearchFilter]
    filterset_fields = ["patient", "status", "department", "source_type"]
    # The Service Cancellations desk finds a bill the way a patient describes
    # themselves at the window: the number on their card, their name, their
    # phone — or the service they are querying.
    search_fields = ["patient__patient_number", "patient__first_name", "patient__last_name",
                     "patient__phone_number", "description"]

    def _fresh(self, charge):
        """
        Re-read a charge through the viewset's own queryset after changing it.

        Not `refresh_from_db()`: that reloads the columns and leaves the
        annotation and the prefetch in place, so the response would still carry
        the refunded total and the open deferral as they were *before* the
        action — a charge just deferred would answer `"deferral": null`.
        """
        return self.get_queryset().get(pk=charge.pk)

    def get_queryset(self):
        # `refunded_total` feeds `Charge.amount_refunded`, so a page of charges
        # on the Refunds screen costs one query rather than one per row.
        # `allocated_total` is what the payment allocations put on the bill;
        # beside the refunded total it is what `untraceable_amount` is worked
        # out from. A correlated subquery rather than a second Sum over a join:
        # two multi-valued joins in one aggregate multiply each other.
        allocated = (PaymentAllocation.objects.filter(charge=models.OuterRef("pk"))
                     .values("charge").annotate(total=models.Sum("amount")).values("total")[:1])
        queryset = super().get_queryset().annotate(
            refunded_total=Coalesce(models.Sum("refund_allocations__amount"), _no_money()),
            allocated_total=Coalesce(models.Subquery(allocated, output_field=_MONEY), _no_money()),
        )
        return annotate_withdrawn(queryset).prefetch_related(
            # `settlement_status` and the `deferral` field both read the open
            # pay-later, which was a query per row until this prefetch.
            models.Prefetch(
                "deferrals",
                queryset=PaymentDeferral.objects.filter(released_at__isnull=True)
                                                .select_related("approved_by"),
                to_attr="open_deferrals",
            )
        ).order_by("-created_at")

    def filter_queryset(self, queryset):
        """
        The Service Cancellations desk's filters: a billed-date range, the
        services a department withdrew (`?awaiting=1`), and those services
        first (`?awaiting_first=1`).

        **List only, never `get_object()`.** A detail route already names the
        charge it means, and cancelling one must not 404 because the page that
        sent the request was filtered — the lesson rule 21 records.
        """
        queryset = super().filter_queryset(queryset)
        if self.action != "list":
            return queryset
        params = self.request.query_params
        start = _day_bound(params.get("created_from"), "created_from")
        end = _day_bound(params.get("created_to"), "created_to", end=True)
        if start:
            queryset = queryset.filter(created_at__gte=start)
        if end:
            queryset = queryset.filter(created_at__lt=end)
        if _truthy(params.get("awaiting")):
            queryset = queryset.filter(service_withdrawn=True)
        if _truthy(params.get("awaiting_first")):
            queryset = queryset.order_by("-service_withdrawn", "-created_at")
        return queryset

    def get_permissions(self):
        # Waiving a charge is a stricter action than creating or recording one
        # — reception can bill and take payment, but not void. Discounting is
        # giving money away, so it sits with the roles that already approve
        # waivers — not with whoever happens to be on the desk.
        if self.action in ("waive", "discount", "discount_amount", "discount_balance"):
            return [RoleRequired(["cashier", "accountant"])]
        # Withdrawing a bill, and the figures heading the desk that does it.
        # CANCEL_ROLES, named apart from REFUND_ROLES, so reaching the Service
        # Cancellations desk is never on its own a way into the drawer.
        if self.action in ("cancel", "cancellation_summary"):
            return [RoleRequired(CANCEL_ROLES)]
        # Cancelling a service *and* handing its money back is two decisions at
        # once, so it needs both groups — DRF lets a request through only when
        # every permission in the list agrees.
        if self.action == "cancel_and_refund":
            return [RoleRequired(CANCEL_ROLES), RoleRequired(REFUND_ROLES)]
        # Money back off one service without cancelling it moves cash out of
        # the drawer, so it sits with REFUND_ROLES — the same boundary a plain
        # refund is held to, not the wider counter.
        if self.action == "refund":
            return [RoleRequired(REFUND_ROLES)]
        # Letting a patient proceed owing is a front-desk decision as well as
        # a cash-desk one — reception is who the patient is standing in front
        # of — but it is still an authorisation, and it is recorded as one.
        if self.action == "defer":
            return [RoleRequired(BILLING_ROLES)]
        if self.action in ("list", "retrieve"): return [RoleRequired(COLLECTING_ROLES)]
        return [RoleRequired(BILLING_ROLES)]

    @action(detail=False, methods=["get"], url_path="cancellation-summary")
    def cancellation_summary(self, request):
        """
        `GET /api/charges/cancellation-summary/` — the figures heading the
        Service Cancellations desk, counted in the database in four queries
        however many charges there are: bills still standing for a service
        their department withdrew and what was paid on them, services in each
        payment state, and what was cancelled this month.
        """
        charges = Charge.objects.order_by()
        by_status = {row["status"]: row["count"]
                     for row in charges.values("status").annotate(count=models.Count("id"))}
        awaiting = charges.filter(withdrawn_q()).aggregate(
            count=models.Count("id"), held=Coalesce(models.Sum("amount_paid"), _no_money()))
        month_start = timezone.make_aware(
            datetime.combine(timezone.localdate().replace(day=1), time.min),
            timezone.get_current_timezone())
        cancelled = charges.filter(status="cancelled", cancelled_at__gte=month_start).aggregate(
            count=models.Count("id"), value=Coalesce(models.Sum("amount"), _no_money()))
        return Response({
            "awaiting": {"count": awaiting["count"], "held": f"{_cents(awaiting['held']):.2f}"},
            "by_status": {key: by_status.get(key, 0) for key, _ in Charge.STATUS},
            "open": sum(by_status.get(key, 0) for key in ("unpaid", "partial", "paid")),
            "cancelled_this_month": {"count": cancelled["count"],
                                     "value": f"{_cents(cancelled['value']):.2f}"},
        })

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        charge = add_charge(patient=serializer.validated_data["patient"], description=serializer.validated_data["description"], amount=serializer.validated_data["amount"], department=serializer.validated_data.get("department"), created_by=request.user, source_type=serializer.validated_data.get("source_type", ""), source_id=serializer.validated_data.get("source_id"))
        audit_event(actor=request.user, action="billing.charge_created", instance=charge, request=request)
        # No notification here: `add_charge` announced it. Raising it a second
        # time from the view is exactly how one action becomes two bells.
        return Response(self.get_serializer(charge).data, status=status.HTTP_201_CREATED)
    @action(detail=True, methods=["post"])
    def discount(self, request, pk=None):
        """Take a percentage off this charge. POST {percent, reason}."""
        charge = self.get_object()
        try:
            adjustment = apply_percentage_discount(
                charge=charge, percent=request.data.get("percent"),
                reason=request.data.get("reason", ""), approved_by=request.user,
            )
        except (ValueError, InvalidOperation, TypeError) as exc:
            return Response({"detail": _discount_error(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.discount", instance=adjustment, request=request)
        return Response(self.get_serializer(self._fresh(charge)).data)

    @action(detail=False, methods=["post"], url_path="discount-balance")
    def discount_balance(self, request, pk=None):
        """Same percentage off everything a patient still owes. POST {patient, percent, reason}."""
        patient_id = request.data.get("patient")
        patient = Patient.objects.filter(pk=patient_id).first() if patient_id else None
        if not patient:
            return Response({"patient": "Unknown patient."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            adjustments = discount_patient_balance(
                patient=patient, percent=request.data.get("percent"),
                reason=request.data.get("reason", ""), approved_by=request.user,
            )
        except (ValueError, InvalidOperation, TypeError) as exc:
            return Response({"detail": _discount_error(exc)}, status=status.HTTP_400_BAD_REQUEST)
        total = sum(a.amount for a in adjustments)
        audit_event(actor=request.user, action="billing.discount_balance", instance=patient,
                    details={"percent": str(request.data.get("percent")), "charges": len(adjustments), "total": str(total)},
                    request=request)
        return Response({"charges_discounted": len(adjustments), "total_discounted": total})

    @action(detail=True, methods=["post"])
    def waive(self, request, pk=None):
        """
        Write money off. `amount` waives part of the charge; omit it and
        everything still owed is waived. Either way the original amount
        survives — the bill shows what was waived and what from.
        """
        charge = self.get_object()
        reason = request.data.get("reason", "")
        if not reason: return Response({"reason": "Required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            adjustment = waive_charge(charge=charge, reason=reason, approved_by=request.user,
                                      amount=request.data.get("amount"))
        except (ValueError, InvalidOperation, TypeError) as exc:
            return Response({"detail": _discount_error(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.waiver", instance=adjustment,
                    details={"amount": str(adjustment.amount)}, request=request)
        return Response(self.get_serializer(self._fresh(charge)).data)

    @action(detail=True, methods=["post"], url_path="discount-amount")
    def discount_amount(self, request, pk=None):
        """Take a flat sum off this charge. POST {amount, reason}."""
        charge = self.get_object()
        try:
            adjustment = apply_amount_discount(
                charge=charge, amount=request.data.get("amount"),
                reason=request.data.get("reason", ""), approved_by=request.user,
            )
        except (ValueError, InvalidOperation, TypeError) as exc:
            return Response({"detail": _discount_error(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.discount", instance=adjustment,
                    details={"amount": str(adjustment.amount)}, request=request)
        return Response(self.get_serializer(self._fresh(charge)).data)

    @action(detail=True, methods=["post"])
    def defer(self, request, pk=None):
        """
        Authorise "pay later" on this charge: the patient may have the
        service now and settle afterwards. It moves no money — the charge
        stays outstanding — but it puts a name and a time against the
        decision to let them proceed.
        """
        charge = self.get_object()
        try:
            deferral = defer_charge(charge=charge, reason=request.data.get("reason", ""),
                                    approved_by=request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.pay_later_approved", instance=deferral,
                    details={"amount": str(deferral.amount_deferred),
                             "charge": charge.description}, request=request)
        return Response(self.get_serializer(self._fresh(charge)).data)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """
        Withdraw this bill: the patient is no longer responsible for it.
        POST `{reason}`.

        This does **not** refund anything. A charge still holding money is
        refused with `code: "refund_required"` and the amount, so the screen
        can offer cancel-and-refund rather than silently putting the patient
        into credit.
        """
        charge = self.get_object()
        try:
            charge = cancel_charge(charge=charge, cancelled_by=request.user,
                                   reason=request.data.get("reason", ""))
        except RefundRequired as exc:
            return Response({"detail": str(exc), "code": "refund_required",
                             "refundable": f"{exc.amount:.2f}"},
                            status=status.HTTP_400_BAD_REQUEST)
        except ValueError as exc:
            return Response({"detail": str(exc), "code": getattr(exc, "code", "cancel_refused")},
                            status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.charge_cancelled", instance=charge,
                    details=_cancellation_audit(
                        charge, amount_paid=charge.amount_paid, amount_refunded=Decimal("0"),
                        outstanding_after=_outstanding(charge.patient_id)),
                    request=request)
        return Response(self.get_serializer(self._fresh(charge)).data)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        """
        `POST /api/charges/<id>/refund/` — hand money back off this service
        **without** cancelling it. Body: `{reason, amount?, method?, reference?}`.

        For a service that was delivered: the bill stays active and becomes
        owed again. Cancelling as well is `cancel-and-refund/`, deliberately a
        separate call because it is a separate decision.
        """
        charge = self.get_object()
        try:
            result = refund_charge(
                charge=charge, reason=request.data.get("reason", ""), actor=request.user,
                amount=request.data.get("amount"),
                method=request.data.get("method"),
                reference=request.data.get("reference", "") or "",
            )
        except (ValueError, InvalidOperation, TypeError) as exc:
            return Response({"detail": str(exc), "code": "refund_refused"},
                            status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.refund", instance=result["charge"],
                    details={"service": result["charge"].description,
                             "amount_refunded": str(result["amount_refunded"]),
                             "reason": request.data.get("reason", ""),
                             "cancelled": False}, request=request)
        return Response({
            "charge": self.get_serializer(self._fresh(result["charge"])).data,
            "amount_refunded": f"{result['amount_refunded']:.2f}",
            "outstanding": f"{result['outstanding']:.2f}",
            "refunds": RefundSerializer(result["refunds"], many=True).data,
        })

    @action(detail=True, methods=["post"], url_path="cancel-and-refund")
    def cancel_and_refund(self, request, pk=None):
        """
        `POST /api/charges/<id>/cancel-and-refund/` — the unused-service
        workflow. Body: `{reason, amount?, method?, reference?}`.

        Withdraws the bill and hands back **everything** paid against it,
        atomically: either both happen or neither does. `amount` is not a
        choice. It is the figure the screen showed, and anything other than the
        full refundable amount is refused with `full_refund_required` and the
        current `refundable` — so a stale screen is corrected rather than acted
        on. Left out, the full amount is used.

        A refusal carries a `code`: `not_cancellable`, `full_refund_required`,
        `untraceable_payment`, `ledger_mismatch` or `cancel_refund_refused`.
        """
        charge = self.get_object()
        method = request.data.get("method") or None
        if method is not None and method not in dict(Payment.METHOD):
            return Response({"method": f"'{method}' is not a way money can go back.",
                             "code": "cancel_refund_refused"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            result = cancel_and_refund(
                charge=charge, reason=request.data.get("reason", ""), actor=request.user,
                expected_amount=request.data.get("amount"),
                method=method,
                reference=request.data.get("reference", "") or "",
            )
        except (ValueError, InvalidOperation, TypeError) as exc:
            body = {"detail": str(exc), "code": getattr(exc, "code", "cancel_refund_refused")}
            if getattr(exc, "refundable", None) is not None:
                body["refundable"] = f"{exc.refundable:.2f}"
            return Response(body, status=status.HTTP_400_BAD_REQUEST)
        cancelled = result["charge"]
        audit_event(actor=request.user, action="billing.cancel_and_refund", instance=cancelled,
                    details=_cancellation_audit(
                        cancelled, amount_paid=result["amount_paid_before"],
                        amount_refunded=result["amount_refunded"], refunds=result["refunds"],
                        outstanding_before=result["outstanding_before"],
                        outstanding_after=result["outstanding"]),
                    request=request)
        return Response({
            "charge": self.get_serializer(self._fresh(cancelled)).data,
            "amount_refunded": f"{result['amount_refunded']:.2f}",
            "outstanding": f"{result['outstanding']:.2f}",
            "refunds": RefundSerializer(result["refunds"], many=True).data,
        })
class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.select_related("patient", "received_by"); serializer_class = PaymentSerializer; http_method_names = ["get", "post", "head", "options"]
    filter_backends = [DjangoFilterBackend]; filterset_fields = ["patient", "channel"]

    def get_queryset(self):
        # What has already gone back, in the same query as the payments —
        # every row carries `refundable_balance`, so a page of payments does
        # not cost one aggregate per row.
        # `.order_by()` is not decoration: aggregating drops the model's
        # `Meta.ordering` (it would have to join the GROUP BY), and an
        # unordered queryset makes paginated pages repeat and drop rows.
        return super().get_queryset().annotate(
            refunded_total=Coalesce(
                models.Sum("refunds__amount"),
                models.Value(Decimal("0"),
                             output_field=models.DecimalField(max_digits=12, decimal_places=2)),
            )
        ).order_by("-created_at")

    def get_permissions(self):
        # Handing money back is not collecting it. Reception and the pharmacy
        # counter may take a payment and never reverse one — refunding sits
        # with the roles that already approve a discount or a waiver.
        if self.action == "refund":
            return [RoleRequired(REFUND_ROLES)]
        return [RoleRequired(COLLECTING_ROLES)]

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        """
        `POST /api/payments/<id>/refund/` — hand part or all of this payment
        back. Body: `{amount, reason, method?, reference?}`.

        The payment itself is never touched. What comes back is the refund
        row, so the caller can show what was returned and what of this payment
        is still refundable.
        """
        payment = self.get_object()
        serializer = RefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            refund = refund_payment(
                payment=payment,
                amount=serializer.validated_data["amount"],
                reason=serializer.validated_data["reason"],
                processed_by=request.user,
                method=serializer.validated_data.get("method"),
                reference=serializer.validated_data.get("reference", ""),
            )
        except (ValueError, InvalidOperation, TypeError) as exc:
            return Response({"detail": str(exc), "code": "refund_refused"},
                            status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.refund", instance=refund,
                    details={"amount": str(refund.amount), "payment": payment.pk,
                             "reason": refund.reason}, request=request)
        # Re-read through the annotated queryset, not `refresh_from_db()`:
        # the latter reloads the columns and leaves the stale `refunded_total`
        # annotation in place, so the response would still claim the money
        # was refundable.
        payment = self.get_queryset().get(pk=payment.pk)
        return Response({
            "refund": RefundSerializer(refund).data,
            "payment": PaymentSerializer(payment).data,
        }, status=status.HTTP_201_CREATED)
    def create(self, request, *args, **kwargs):
        s=self.get_serializer(data=request.data); s.is_valid(raise_exception=True)
        try: payment=record_payment(patient=s.validated_data["patient"], amount=s.validated_data["amount"], received_by=request.user, method=s.validated_data.get("method", "cash"), reference=s.validated_data.get("reference", ""), channel=PAYMENT_CHANNEL_BY_ROLE.get(request.user.role, "front_desk"))
        except ValueError as exc: return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.payment_recorded", instance=payment, request=request); return Response(self.get_serializer(payment).data, status=201)
class AdjustmentViewSet(viewsets.ModelViewSet):
    queryset = Adjustment.objects.select_related("patient", "charge", "approved_by"); serializer_class = AdjustmentSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["patient", "kind"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number", "reason"]
    ordering = ["-created_at"]
    def get_permissions(self):
        # Granting a discount or waiver stays with cashier/accountant, but the
        # front desk has to be able to *read* them: a patient's statement does
        # not add up if the money written off is invisible to the person
        # collecting the rest.
        if self.action in ("list", "retrieve"): return [RoleRequired(BILLING_ROLES)]
        return [RoleRequired(["cashier", "accountant"])]
    def perform_create(self, serializer):
        adj=serializer.save(approved_by=self.request.user); refresh_ledger(adj.patient); audit_event(actor=self.request.user, action=f"billing.{adj.kind}", instance=adj, request=self.request)


class RefundViewSet(viewsets.ReadOnlyModelViewSet):
    """
    The refund register — every payment handed back, with the payment it
    answers, the reason and who authorised it.

    Read-only on purpose: a refund is written by
    `billing.services.refund_payment` and by nothing else, the same rule every
    other money row follows. `POST /api/payments/<id>/refund/` is where one is
    made.

    Read by BILLING_ROLES, the way the write-off register is: the front desk
    cannot grant a refund but has to be able to see one, or a patient's
    statement stops adding up in front of the person collecting the rest.
    """
    # The charge's department rides along with each allocation, so the
    # register can say which unit the money went back from without a query
    # per row — and `from_cancellation` reads the same prefetched charges.
    queryset = Refund.objects.select_related(
        "patient", "payment", "processed_by", "authorized_by").prefetch_related(
        "allocations__charge__department")
    serializer_class = RefundSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["patient", "payment", "method"]
    search_fields = ["patient__first_name", "patient__last_name",
                     "patient__patient_number", "reason", "reference"]

    def get_permissions(self):
        # The figures heading the Refunds desk belong to that desk, which is
        # REFUND_ROLES. The register itself is read like the write-off register.
        if self.action == "summary":
            return [RoleRequired(REFUND_ROLES)]
        return [RoleRequired(BILLING_ROLES)]

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """
        `GET /api/refunds/summary/` — what went back today and this month, in
        two aggregate queries. The same rows the finance report counts as
        `collections.refunds`, dated by when the money left the drawer.
        """
        tz = timezone.get_current_timezone()
        today = timezone.localdate()

        def since(day):
            row = Refund.objects.order_by().filter(
                created_at__gte=timezone.make_aware(datetime.combine(day, time.min), tz)
            ).aggregate(count=models.Count("id"),
                        amount=Coalesce(models.Sum("amount"), _no_money()))
            return {"count": row["count"], "amount": f"{_cents(row['amount']):.2f}"}

        return Response({"today": since(today), "month": since(today.replace(day=1))})


class FinancialReportView(APIView):
    """
    `GET /api/finance/report/` — the financial dashboard, aggregated in the
    database.

    Parameters: `preset` (today · yesterday · week · month · last_month ·
    year · custom), `from` and `to` for a custom range, and `limit` for how
    many transaction rows to return.

    Every figure comes from `billing.reporting`, which reads the charge,
    payment, allocation and ledger rows directly. The response separates the
    two bases it reports on — cash collected in the period, and the bills
    raised in the period — because they answer different questions and only
    the second one reconciles.
    """
    # RoleRequired is used as an instance here, the way every viewset in this
    # app uses it — `permission_classes` would call it a second time.
    def get_permissions(self):
        return [RoleRequired(FINANCE_REPORT_ROLES)]

    def get(self, request):
        try:
            period = reporting.resolve_period(
                request.query_params.get("preset"),
                request.query_params.get("from"),
                request.query_params.get("to"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            limit = int(request.query_params.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50

        report = reporting.financial_report(
            period=period,
            # "Taken at this desk" — what a cashier reconciles at the end of a
            # shift, as against what the hospital took.
            received_by=request.user,
            transaction_limit=limit,
        )
        report["role"] = getattr(request.user, "role", None)
        return Response(report)
