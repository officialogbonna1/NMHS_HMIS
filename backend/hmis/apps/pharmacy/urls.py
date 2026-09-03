from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register("prescriptions", views.PrescriptionViewSet)

urlpatterns = router.urls
