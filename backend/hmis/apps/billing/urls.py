from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (LedgerViewSet, ChargeViewSet, PaymentViewSet, AdjustmentViewSet,
                    BillingItemViewSet, RefundViewSet, FinancialReportView)
router=DefaultRouter(); router.register("ledgers", LedgerViewSet); router.register("charges", ChargeViewSet); router.register("payments", PaymentViewSet); router.register("adjustments", AdjustmentViewSet); router.register("billing-items", BillingItemViewSet); router.register("refunds", RefundViewSet)
# The report is one aggregated read, not a collection, so it is a plain view
# rather than a viewset action hanging off charges or payments — it is built
# from both, and belongs to neither.
urlpatterns = router.urls + [
    path("finance/report/", FinancialReportView.as_view(), name="finance-report"),
]
