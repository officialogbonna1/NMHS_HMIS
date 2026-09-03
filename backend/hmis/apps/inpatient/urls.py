from rest_framework.routers import DefaultRouter
from .views import WardViewSet,BedViewSet,AdmissionViewSet,BedTransferViewSet,DischargeSummaryViewSet
router=DefaultRouter(); router.register("wards",WardViewSet); router.register("beds",BedViewSet); router.register("admissions",AdmissionViewSet); router.register("bed-transfers",BedTransferViewSet); router.register("discharges",DischargeSummaryViewSet)
urlpatterns=router.urls
