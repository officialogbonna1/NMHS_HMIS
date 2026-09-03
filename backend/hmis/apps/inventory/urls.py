from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register("items", views.ItemViewSet)
router.register("batches", views.BatchViewSet)
router.register("stock-movements", views.StockMovementViewSet)

urlpatterns = router.urls
