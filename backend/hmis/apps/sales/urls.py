from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
# The pharmacy POS. There is no sale-items endpoint: a line of a sale is written
# by the service that also moves its stock, never on its own.
router.register("sales", views.SaleViewSet, basename="sale")
router.register("pos-registers", views.PosRegisterViewSet, basename="pos-register")

urlpatterns = router.urls
