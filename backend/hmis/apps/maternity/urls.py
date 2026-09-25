from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("pregnancies", views.PregnancyViewSet)
router.register("maternity-encounters", views.MaternityEncounterViewSet)
router.register("maternity-visit-types", views.MaternityVisitTypeViewSet)
router.register("maternity-options", views.MaternityOptionViewSet)
router.register("labour-episodes", views.LabourEpisodeViewSet)
router.register("deliveries", views.DeliveryViewSet)
router.register("newborns", views.NewbornViewSet)

urlpatterns = [
    path("maternity/lookup/", views.MaternityLookupView.as_view(), name="maternity-lookup"),
    path("maternity/patients/", views.MaternityPatientsView.as_view(), name="maternity-patients"),
    path("maternity/staff/", views.MaternityStaffView.as_view(), name="maternity-staff"),
    path("maternity/assignment/", views.MaternityAssignmentView.as_view(),
         name="maternity-assignment"),
    *router.urls,
]
