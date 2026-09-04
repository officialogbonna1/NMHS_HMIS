from decimal import InvalidOperation
from django.db import models

from apps.patients.models import Patient
from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from apps.core.services import audit_event, notify
from apps.accounts.permissions import BILLING_ROLES, IsAdmin
from .models import PatientLedger, Charge, Payment, Adjustment, BillingItem
from .serializers import PatientLedgerSerializer, ChargeSerializer, PaymentSerializer, AdjustmentSerializer, BillingItemSerializer
from .services import (add_charge, record_payment, refresh_ledger, waive_charge, cancel_charge,
                       apply_percentage_discount, apply_amount_discount, discount_patient_balance,
                       defer_charge)
from apps.accounts.permissions import RoleRequired

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


class BillingItemViewSet(viewsets.ModelViewSet):
    queryset = BillingItem.objects.all(); serializer_class = BillingItemSerializer
    filter_backends = [DjangoFilterBackend]; filterset_fields = ["is_active", "category"]
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
    search_fields = ["patient__first_name", "patient__last_name", "patient__file_number"]

    def get_permissions(self): return [RoleRequired(COLLECTING_ROLES)]

    def get_queryset(self):
        queryset = super().get_queryset().annotate(
            balance=models.F("total_charges") - models.F("total_payments") - models.F("total_adjustments")
        )
        if self.request.query_params.get("owing") in ("true", "1", "yes"):
            # Settling in full is what takes somebody off this list.
            return queryset.filter(balance__gt=0).order_by("-balance")
        return queryset.order_by("patient__last_name", "patient__first_name")
class ChargeViewSet(viewsets.ModelViewSet):
    queryset = Charge.objects.select_related("patient", "department"); serializer_class = ChargeSerializer; filter_backends = [DjangoFilterBackend]; filterset_fields = ["patient", "status", "department"]
    def get_permissions(self):
        # Waiving/cancelling a charge is a stricter action than creating or
        # recording one — reception can bill and take payment, but not void.
        # Discounting is giving money away, so it sits with the roles that
        # already approve waivers — not with whoever happens to be on the desk.
        if self.action in ("waive", "cancel", "discount", "discount_amount", "discount_balance"):
            return [RoleRequired(["cashier", "accountant"])]
        # Letting a patient proceed owing is a front-desk decision as well as
        # a cash-desk one — reception is who the patient is standing in front
        # of — but it is still an authorisation, and it is recorded as one.
        if self.action == "defer":
            return [RoleRequired(BILLING_ROLES)]
        if self.action in ("list", "retrieve"): return [RoleRequired(COLLECTING_ROLES)]
        return [RoleRequired(BILLING_ROLES)]
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        charge = add_charge(patient=serializer.validated_data["patient"], description=serializer.validated_data["description"], amount=serializer.validated_data["amount"], department=serializer.validated_data.get("department"), created_by=request.user, source_type=serializer.validated_data.get("source_type", ""), source_id=serializer.validated_data.get("source_id"))
        audit_event(actor=request.user, action="billing.charge_created", instance=charge, request=request)
        _tell_the_cash_desk(charge, request.user)
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
        charge.refresh_from_db()
        return Response(self.get_serializer(charge).data)

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
        charge.refresh_from_db()
        return Response(self.get_serializer(charge).data)

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
        charge.refresh_from_db()
        return Response(self.get_serializer(charge).data)

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
        charge.refresh_from_db()
        return Response(self.get_serializer(charge).data)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        charge = self.get_object()
        try: cancel_charge(charge=charge)
        except ValueError as exc: return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.charge_cancelled", instance=charge, request=request)
        return Response(self.get_serializer(charge).data)
class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.select_related("patient"); serializer_class = PaymentSerializer; http_method_names = ["get", "post", "head", "options"]
    filter_backends = [DjangoFilterBackend]; filterset_fields = ["patient", "channel"]
    def get_permissions(self): return [RoleRequired(COLLECTING_ROLES)]
    def create(self, request, *args, **kwargs):
        s=self.get_serializer(data=request.data); s.is_valid(raise_exception=True)
        try: payment=record_payment(patient=s.validated_data["patient"], amount=s.validated_data["amount"], received_by=request.user, method=s.validated_data.get("method", "cash"), reference=s.validated_data.get("reference", ""), channel=PAYMENT_CHANNEL_BY_ROLE.get(request.user.role, "front_desk"))
        except ValueError as exc: return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        audit_event(actor=request.user, action="billing.payment_recorded", instance=payment, request=request); return Response(self.get_serializer(payment).data, status=201)
class AdjustmentViewSet(viewsets.ModelViewSet):
    queryset = Adjustment.objects.select_related("patient", "charge", "approved_by"); serializer_class = AdjustmentSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["patient", "kind"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__file_number", "reason"]
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


def _tell_the_cash_desk(charge, raised_by):
    """
    Money raised somewhere else is money the cash desk has to collect. The
    front desk bills a card or a consultation and walks the patient over —
    without this the cashier only finds out when the patient is standing
    there.

    Cashiers and accountants are told; whoever raised it is not told about
    their own charge.
    """
    from apps.accounts.models import User

    for cashier in User.objects.filter(role__in=["cashier", "accountant"], is_active=True).exclude(pk=raised_by.pk):
        notify(
            recipient=cashier,
            title=f"To collect: {charge.patient}",
            message=f"{charge.description} — {charge.amount:,.2f}, raised by {raised_by.get_full_name() or raised_by.username}.",
            category="billing",
            action_url="/billing",
        )
