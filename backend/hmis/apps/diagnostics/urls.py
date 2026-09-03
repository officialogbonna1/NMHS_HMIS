from rest_framework.routers import DefaultRouter
from .views import InvestigationCatalogViewSet, InvestigationOrderViewSet, InvestigationResultViewSet
router=DefaultRouter(); router.register("investigations",InvestigationCatalogViewSet); router.register("investigation-orders",InvestigationOrderViewSet); router.register("investigation-results",InvestigationResultViewSet)
urlpatterns=router.urls
