from rest_framework.routers import DefaultRouter

from .views import (
    LabOrderViewSet, LabPanelViewSet, LabParameterViewSet, LabTestViewSet,
)

router = DefaultRouter()
router.register("lab-tests", LabTestViewSet)
router.register("lab-parameters", LabParameterViewSet)
router.register("lab-panels", LabPanelViewSet)
router.register("lab-orders", LabOrderViewSet)

urlpatterns = router.urls
