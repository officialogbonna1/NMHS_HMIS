from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (LedgerViewSet, ChargeViewSet, PaymentViewSet, AdjustmentViewSet,
                    BillableServiceView, BillingItemViewSet, RefundViewSet,
                    FinancialReportView)
router=DefaultRouter(); router.register("ledgers", LedgerViewSet); router.register("charges", ChargeViewSet); router.register("payments", PaymentViewSet); router.register("adjustments", AdjustmentViewSet); router.register("billing-items", BillingItemViewSet); router.register("refunds", RefundViewSet)
# The report is one aggregated read, not a collection, so it is a plain view
# rather than a viewset action hanging off charges or payments — it is built
# from both, and belongs to neither.
urlpatterns = router.urls + [
    path("finance/report/", FinancialReportView.as_view(), name="finance-report"),
    # One window onto the configured services the counter bills — the price
    # list *and* the laboratory catalogue a doctor orders from, so the desk
    # can bill what was ordered. A read, never a second catalogue.
    path("billable-services/", BillableServiceView.as_view(), name="billable-services"),
]
