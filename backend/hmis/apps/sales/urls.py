from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register("sales", views.SaleViewSet)
router.register("sale-items", views.SaleItemViewSet)

urlpatterns = router.urls
