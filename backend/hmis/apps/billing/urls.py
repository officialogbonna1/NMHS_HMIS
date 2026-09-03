from rest_framework.routers import DefaultRouter
from .views import LedgerViewSet, ChargeViewSet, PaymentViewSet, AdjustmentViewSet, BillingItemViewSet
router=DefaultRouter(); router.register("ledgers", LedgerViewSet); router.register("charges", ChargeViewSet); router.register("payments", PaymentViewSet); router.register("adjustments", AdjustmentViewSet); router.register("billing-items", BillingItemViewSet)
urlpatterns=router.urls
