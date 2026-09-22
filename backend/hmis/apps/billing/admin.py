from django.contrib import admin

from apps.core.config import ProtectedConfigAdmin

from .models import (BillingItem, PatientLedger, Charge, Payment, Adjustment, PaymentDeferral,
                     PaymentAllocation, Refund, RefundAllocation)


@admin.register(BillingItem)
class BillingItemAdmin(ProtectedConfigAdmin, admin.ModelAdmin):
    """
    The price list the counter bills from — and, where a row is ticked
    bookable, the list reception queues appointments from. One catalogue, two
    uses: the fee, the department and the eligible providers all follow from
    the row itself (`appointments/booking.py`), so there is nothing else to
    configure and nowhere for a second definition to live.
    """
    list_display = ["name", "category", "price", "is_active", "is_appointment_service"]
    list_editable = ["price", "is_active", "is_appointment_service"]
    list_filter = ["category", "is_active", "is_appointment_service"]
    search_fields = ["name"]
    ordering = ["category", "name"]
    actions = ["activate", "deactivate", "make_bookable", "make_unbookable"]
    # A laboratory test priced from this item keeps pointing at it, and so
    # does every referral that has ordered it (`workflow.RouteService`).
    # Deactivate rather than delete: old orders keep their price.
    protected_relations = ("lab_tests", "route_services")

    @admin.action(description="Activate selected")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} priced item(s) activated.")

    @admin.action(description="Deactivate selected (old charges keep their price)")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} priced item(s) deactivated.")

    @admin.action(description="Offer on the Appointments booking form")
    def make_bookable(self, request, queryset):
        count = queryset.update(is_appointment_service=True)
        self.message_user(request, f"{count} service(s) can now be booked as appointments.")

    @admin.action(description="Remove from the Appointments booking form")
    def make_unbookable(self, request, queryset):
        # Booked appointments keep their own snapshot of the name and the fee,
        # so withdrawing a service never rewrites what was booked.
        count = queryset.update(is_appointment_service=False)
        self.message_user(request, f"{count} service(s) withdrawn from appointment booking.")
    ordering = ["category", "name"]


class _MoneyAdmin(admin.ModelAdmin):
    """
    Money is settled through billing/services.py, which keeps the charge and
    the ledger in step. Editing these figures by hand moves one without the
    other, so they are readable here and changed on the billing screens.
    """
    list_per_page = 50
    list_select_related = ["patient"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number"]
    date_hierarchy = "created_at"

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(Charge)
class ChargeAdmin(_MoneyAdmin):
    list_display = ["created_at", "patient", "description", "amount", "amount_paid",
                    "amount_discounted", "balance", "status"]
    list_filter = ["status", "source_type", "created_at"]

    @admin.display(description="Balance")
    def balance(self, obj):
        return obj.balance


@admin.register(Payment)
class PaymentAdmin(_MoneyAdmin):
    list_display = ["created_at", "patient", "amount", "method", "channel", "received_by", "reference"]
    list_filter = ["method", "channel", "created_at"]


@admin.register(Adjustment)
class AdjustmentAdmin(_MoneyAdmin):
    list_display = ["created_at", "patient", "kind", "charge", "reason"]
    list_filter = ["kind", "created_at"]


@admin.register(PaymentDeferral)
class PaymentDeferralAdmin(_MoneyAdmin):
    """
    The pay-later register: who allowed a patient to have the service before
    paying. Read-only here like every other money row — a deferral is created
    by the counter through `billing/services.defer_charge`, which is also
    what closes it when the charge is finally settled.
    """
    list_display = ["created_at", "patient", "charge", "amount_deferred", "approved_by",
                    "released_at"]
    list_filter = ["created_at", "released_at"]
    search_fields = ["patient__last_name", "patient__first_name", "patient__patient_number",
                     "charge__description"]


@admin.register(PatientLedger)
class PatientLedgerAdmin(_MoneyAdmin):
    list_display = ["patient", "total_charges", "total_payments", "total_adjustments",
                    "outstanding_balance"]
    date_hierarchy = None

    @admin.display(description="Outstanding")
    def outstanding_balance(self, obj):
        return obj.outstanding_balance


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(admin.ModelAdmin):
    """
    Which charge each payment settled. Written by
    `billing.services.allocate_to_charges` inside the same transaction as the
    allocation it records — an audit row, so it is read here and nowhere
    edited, the same rule the rest of the money models follow.

    It does not use `_MoneyAdmin`: an allocation has no patient of its own,
    only the two rows it joins.
    """
    list_display = ["created_at", "payment", "charge", "amount"]
    list_select_related = ["payment", "charge"]
    search_fields = ["charge__description", "charge__patient__patient_number"]
    date_hierarchy = "created_at"
    list_per_page = 50

    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_add_permission(self, request): return False


@admin.register(Refund)
class RefundAdmin(_MoneyAdmin):
    """
    Money handed back. Read-only here like every other money row: a refund is
    written by `billing.services.refund_payment`, which also reopens the bills
    it came off and writes the ledger adjustment beside it. Typing one in here
    would move the refund without any of that.
    """
    list_display = ["created_at", "patient", "amount", "payment", "method",
                    "processed_by", "authorized_by"]
    list_filter = ["method", "created_at"]
    search_fields = ["patient__first_name", "patient__last_name",
                     "patient__patient_number", "reason", "reference"]


@admin.register(RefundAllocation)
class RefundAllocationAdmin(admin.ModelAdmin):
    """
    Which bill each refund came back off — the mirror of PaymentAllocation,
    and read-only for the same reason.
    """
    list_display = ["created_at", "refund", "charge", "amount"]
    list_select_related = ["refund", "charge"]
    search_fields = ["charge__description", "charge__patient__patient_number"]
    date_hierarchy = "created_at"
    list_per_page = 50

    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_add_permission(self, request): return False
