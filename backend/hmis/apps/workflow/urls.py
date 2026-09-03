from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import VisitViewSet, PatientRouteViewSet, DashboardView
router = DefaultRouter(); router.register("visits", VisitViewSet); router.register("patient-routes", PatientRouteViewSet)
urlpatterns = [path("dashboard/", DashboardView.as_view())] + router.urls
