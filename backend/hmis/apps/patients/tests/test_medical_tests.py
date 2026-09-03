"""
Test results on the health record: a document, a typed finding, or both.
"""
import shutil
import tempfile
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.patients.models import Patient, MedicalTest
from apps.workflow.models import Visit

MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class MedicalTestUploadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        Visit.objects.create(patient=self.patient, opened_by=self.reception, attending_doctor=self.doctor)
        self.client = APIClient(); self.client.force_authenticate(self.doctor)

    def _upload(self, name="result.pdf", content=b"%PDF-1.4 result", content_type="application/pdf"):
        return SimpleUploadedFile(name, content, content_type=content_type)

    def _post(self, **overrides):
        payload = {
            "patient": self.patient.id, "title": "Full blood count",
            "test_type": "labs", "test_date": "2026-09-01",
        }
        payload.update(overrides)
        return self.client.post("/api/medical-tests/", payload, format="multipart")

    def test_a_result_can_be_a_document(self):
        response = self._post(file=self._upload())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["file_url"].startswith("/media/"))
        self.assertIn("result", response.data["file_name"])
        self.assertEqual(response.data["test_type_label"], "Labs")

    def test_a_result_can_be_typed_instead(self):
        """No scanner at the desk: the finding is typed and still recorded."""
        response = self._post(impressions="Hb 11.2 g/dL, WBC normal. No parasites seen.")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["file_url"])
        saved = MedicalTest.objects.get(pk=response.data["id"])
        self.assertFalse(saved.file)
        self.assertIn("Hb 11.2", saved.impressions)

    def test_an_image_is_accepted_as_readily_as_a_pdf(self):
        response = self._post(file=self._upload("xray.png", b"\x89PNG\r\n", "image/png"))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["file_name"].endswith(".png"))

    def test_a_result_with_neither_document_nor_finding_is_refused(self):
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertIn("impressions", response.data)

    def test_whitespace_is_not_a_typed_result(self):
        response = self._post(impressions="   ")
        self.assertEqual(response.status_code, 400)

    def test_editing_the_findings_does_not_wipe_the_document(self):
        """The modal omits an untouched file field; the record must keep it."""
        created = self._post(file=self._upload())
        test_id = created.data["id"]
        response = self.client.patch(f"/api/medical-tests/{test_id}/",
                                     {"impressions": "Reviewed — normal."}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(MedicalTest.objects.get(pk=test_id).file)
        self.assertEqual(response.data["impressions"], "Reviewed — normal.")

    def test_a_document_can_be_added_to_a_typed_result_later(self):
        created = self._post(impressions="Verbal result from the lab.")
        test_id = created.data["id"]
        response = self.client.patch(f"/api/medical-tests/{test_id}/",
                                     {"file": self._upload()}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["file_url"])

    def test_it_reaches_the_doctors_overview(self):
        self._post(file=self._upload(), impressions="No acute findings.")
        overview = self.client.get(f"/api/patients/{self.patient.id}/overview/")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.data["counts"]["tests"], 1)
        test = overview.data["tests"][0]
        self.assertEqual(test["title"], "Full blood count")
        self.assertEqual(test["test_type"], "Labs")
        self.assertEqual(test["impressions"], "No acute findings.")
        self.assertTrue(test["file_url"].startswith("/media/"))

    def test_a_typed_result_reaches_the_overview_without_a_file(self):
        self._post(impressions="Hb 11.2 g/dL.")
        test = self.client.get(f"/api/patients/{self.patient.id}/overview/").data["tests"][0]
        self.assertIsNone(test["file_url"])
        self.assertEqual(test["impressions"], "Hb 11.2 g/dL.")

    def test_a_nurse_cannot_read_or_write_test_results(self):
        self._post(impressions="Hb 11.2 g/dL.")
        nurse_client = APIClient(); nurse_client.force_authenticate(self.nurse)
        self.assertEqual(nurse_client.get("/api/medical-tests/").status_code, 403)
        self.assertEqual(
            nurse_client.post("/api/medical-tests/", {
                "patient": self.patient.id, "title": "X", "test_type": "labs",
                "test_date": "2026-09-01", "impressions": "y",
            }).status_code,
            403,
        )

    def test_a_doctor_the_patient_is_not_assigned_to_sees_nothing(self):
        self._post(impressions="Hb 11.2 g/dL.")
        stranger = User.objects.create_user(username="doctor2", password="test", role="doctor")
        client = APIClient(); client.force_authenticate(stranger)
        self.assertEqual(client.get("/api/medical-tests/").data["results"], [])
