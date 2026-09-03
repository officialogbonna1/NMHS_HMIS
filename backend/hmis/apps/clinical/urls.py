from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register("vitals", views.VitalsViewSet)
router.register("notes", views.ConsultationNoteViewSet)
router.register("nursing-notes", views.NursingNoteViewSet)
router.register("note-amendments", views.ConsultationNoteAmendmentViewSet)

urlpatterns = router.urls
