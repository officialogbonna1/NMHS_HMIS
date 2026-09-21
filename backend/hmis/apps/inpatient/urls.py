from rest_framework.routers import DefaultRouter
from .views import (WardViewSet, BedViewSet, AdmissionViewSet, BedTransferViewSet,
                    DischargeSummaryViewSet, AdminDischargeViewSet)
router=DefaultRouter(); router.register("wards",WardViewSet); router.register("beds",BedViewSet); router.register("admissions",AdmissionViewSet); router.register("bed-transfers",BedTransferViewSet); router.register("discharges",DischargeSummaryViewSet)
# The Admin Discharge workspace. Same model, same service, Super Admin only.
router.register("admin-discharges", AdminDischargeViewSet, basename="admin-discharge")
urlpatterns=router.urls
