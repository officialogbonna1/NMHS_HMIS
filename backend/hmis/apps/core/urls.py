from rest_framework.routers import DefaultRouter
from .views import AuditLogViewSet, NotificationViewSet
router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")
urlpatterns = router.urls
