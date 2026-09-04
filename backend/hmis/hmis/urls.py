from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# The admin is the back office for this hospital, not a generic Django site.
admin.site.site_header = "Ngozi Maternity and Hospital Services — administration"
admin.site.site_title = "NMHS admin"
admin.site.index_title = "Hospital records and staff"

urlpatterns = [
    path("admin/", admin.site.urls),

    path("api/auth/", include("apps.accounts.urls")),
    path("api/", include("apps.accounts.user_urls")),

    path("api/", include("apps.patients.urls")),
    path("api/", include("apps.clinical.urls")),
    path("api/", include("apps.inventory.urls")),
    path("api/", include("apps.pharmacy.urls")),
    path("api/", include("apps.sales.urls")),
    path("api/", include("apps.appointments.urls")),
    path("api/", include("apps.departments.urls")),
    path("api/", include("apps.workflow.urls")),
    path("api/", include("apps.billing.urls")),
    path("api/", include("apps.diagnostics.urls")),
    path("api/", include("apps.laboratory.urls")),
    path("api/", include("apps.inpatient.urls")),
    path("api/", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
