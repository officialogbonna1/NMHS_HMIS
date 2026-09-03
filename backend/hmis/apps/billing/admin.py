from django.contrib import admin

from .models import BillingItem, PatientLedger, Charge, Payment, Adjustment


@admin.register(BillingItem)
class BillingItemAdmin(admin.ModelAdmin):
    """The price list the counter bills from."""
    list_display = ["name", "category", "price", "is_active"]
    list_filter = ["category", "is_active"]
    search_fields = ["name"]
    ordering = ["category", "name"]


class _MoneyAdmin(admin.ModelAdmin):
    """
    Money is settled through billing/services.py, which keeps the charge and
    the ledger in step. Editing these figures by hand moves one without the
    other, so they are readable here and changed on the billing screens.
    """
    list_per_page = 50
    list_select_related = ["patient"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__file_number"]
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


@admin.register(PatientLedger)
class PatientLedgerAdmin(_MoneyAdmin):
    list_display = ["patient", "total_charges", "total_payments", "total_adjustments",
                    "outstanding_balance"]
    date_hierarchy = None

    @admin.display(description="Outstanding")
    def outstanding_balance(self, obj):
        return obj.outstanding_balance
