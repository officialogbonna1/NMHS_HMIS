from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register("patients", views.PatientViewSet)
router.register("allergies", views.AllergyViewSet)
router.register("medications", views.MedicationViewSet)
router.register("conditions", views.MedicalConditionViewSet)
router.register("devices", views.MedicalDeviceViewSet)
router.register("surgical-history", views.SurgicalHistoryViewSet)
router.register("family-history", views.FamilyMedicalHistoryViewSet)
router.register("social-history", views.SocialHistoryViewSet)
router.register("vaccinations", views.VaccinationViewSet)
router.register("medical-tests", views.MedicalTestViewSet)

urlpatterns = router.urls
