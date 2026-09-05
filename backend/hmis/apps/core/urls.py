from rest_framework.routers import DefaultRouter
from .views import (
    AuditLogViewSet, HospitalSettingsViewSet, NotificationSettingViewSet, NotificationViewSet,
)
router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")
# Configuration the whole application reads and an admin edits.
router.register("hospital-settings", HospitalSettingsViewSet, basename="hospital-settings")
router.register("notification-settings", NotificationSettingViewSet,
                basename="notification-setting")
urlpatterns = router.urls
