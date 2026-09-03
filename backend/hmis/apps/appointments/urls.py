from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register("appointments", views.AppointmentViewSet)

urlpatterns = router.urls
