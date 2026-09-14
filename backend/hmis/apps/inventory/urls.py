from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register("items", views.ItemViewSet)
# Catalogue configuration — the same rows Django admin edits.
router.register("item-categories", views.ItemCategoryViewSet)
router.register("units", views.UnitOfMeasureViewSet)
router.register("batches", views.BatchViewSet)
router.register("stock-locations", views.StockLocationViewSet)
# Product + batch + location. Read-only: stock moves through the services.
router.register("stock-records", views.StockRecordViewSet)
router.register("stock-transfers", views.StockTransferViewSet)
router.register("stock-counts", views.StockCountViewSet)
# The CSV count: preview an uploaded sheet, then apply it once.
router.register("stock-count-imports", views.StockCountImportViewSet)
router.register("stock-movements", views.StockMovementViewSet)

urlpatterns = router.urls
