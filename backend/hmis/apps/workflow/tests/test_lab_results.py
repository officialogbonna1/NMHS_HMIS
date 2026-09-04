"""
Conducting a test: the lab does the work, files the result — typed values,
an uploaded report, or both — and the doctor who asked is told and can read
it on the chart.

The result is filed twice on purpose. The route carries it so the station
and the queue can show it; a MedicalTest carries it so it is still on the
patient's record next year, after the visit has closed.
"""
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Notification
from apps.departments.models import Department
from apps.patients.models import Patient, MedicalTest
from apps.workflow.models import Visit, PatientRoute

MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class LabResultTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor",
                                               first_name="Ada", last_name="Obi")
        self.lab = User.objects.create_user(username="lab", password="test", role="laboratory",
                                            first_name="Chidi", last_name="Eze")
        self.department = Department.objects.create(code="general-medicine", name="General Medicine")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)

        self.doctor_client = APIClient(); self.doctor_client.force_authenticate(self.doctor)
        self.lab_client = APIClient(); self.lab_client.force_authenticate(self.lab)

    def _report(self, name="fbc.pdf", content=b"%PDF-1.4 result"):
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def _refer(self, purpose="laboratory", notes="FBC and MP"):
        route_id = self.doctor_client.post("/api/patient-routes/refer/", {
            "patient": self.patient.id, "purpose": purpose, "notes": notes,
        }, format="json").data["id"]
        self.lab_client.post(f"/api/patient-routes/{route_id}/accept/")
        return route_id

    def _record(self, route_id, **payload):
        return self.lab_client.post(f"/api/patient-routes/{route_id}/record-result/",
                                    payload, format="multipart")

    def test_a_typed_result_is_filed_on_the_patients_record(self):
        route_id = self._refer()
        response = self._record(route_id, result="Hb 11.2 g/dL, no parasites seen.",
                                title="Full blood count")
        self.assertEqual(response.status_code, 200, response.data)

        test = MedicalTest.objects.get(patient=self.patient)
        self.assertEqual(test.title, "Full blood count")
        self.assertEqual(test.test_type, "labs")
        self.assertEqual(test.impressions, "Hb 11.2 g/dL, no parasites seen.")
        self.assertIn("Ada Obi", test.notes)

    def test_the_report_can_be_uploaded(self):
        route_id = self._refer()
        response = self._record(route_id, result="See attached report.", file=self._report())
        self.assertEqual(response.status_code, 200, response.data)

        test = MedicalTest.objects.get(patient=self.patient)
        self.assertTrue(test.file)
        self.assertIn("fbc", test.file.name)
        # The station shows what it attached without a second request.
        self.assertTrue(response.data["result_file_url"].startswith("/media/"))
        self.assertIn("fbc", response.data["result_file_name"])

    def test_an_upload_alone_is_enough(self):
        """A scanned printout is a result even with nothing typed."""
        route_id = self._refer()
        response = self._record(route_id, file=self._report())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(MedicalTest.objects.get(patient=self.patient).file)

    def test_neither_values_nor_a_report_is_refused(self):
        route_id = self._refer()
        response = self._record(route_id, result="   ")
        self.assertEqual(response.status_code, 400)
        self.assertIn("result", response.data)
        self.assertFalse(MedicalTest.objects.exists())

    def test_saving_again_updates_the_same_test_rather_than_filing_a_second(self):
        route_id = self._refer()
        self._record(route_id, result="Preliminary: awaiting culture.")
        self._record(route_id, result="Final: no growth at 48h.", file=self._report())

        self.assertEqual(MedicalTest.objects.filter(patient=self.patient).count(), 1)
        test = MedicalTest.objects.get(patient=self.patient)
        self.assertEqual(test.impressions, "Final: no growth at 48h.")
        self.assertTrue(test.file)

    def test_the_title_falls_back_to_what_the_doctor_asked_for(self):
        route_id = self._refer(notes="Malaria parasite screen")
        self._record(route_id, result="Negative.")
        self.assertEqual(MedicalTest.objects.get(patient=self.patient).title,
                         "Malaria parasite screen")

    def test_closing_the_work_files_the_result_the_same_way(self):
        """Whichever button the unit presses, the chart gets the same copy."""
        route_id = self._refer()
        response = self.lab_client.post(f"/api/patient-routes/{route_id}/complete/",
                                        {"result": "Normal study.", "file": self._report()},
                                        format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(PatientRoute.objects.get(pk=route_id).status, "completed")
        test = MedicalTest.objects.get(patient=self.patient)
        self.assertEqual(test.impressions, "Normal study.")
        self.assertTrue(test.file)

    def test_an_ultrasound_is_filed_under_its_own_test_type(self):
        radiology = User.objects.create_user(username="radio", password="test", role="radiology")
        route_id = self.doctor_client.post("/api/patient-routes/refer/", {
            "patient": self.patient.id, "purpose": "ultrasound", "notes": "Abdominal scan",
        }, format="json").data["id"]
        client = APIClient(); client.force_authenticate(radiology)
        client.post(f"/api/patient-routes/{route_id}/accept/")
        client.post(f"/api/patient-routes/{route_id}/record-result/",
                    {"result": "No gallstones."}, format="multipart")
        self.assertEqual(MedicalTest.objects.get(patient=self.patient).test_type, "ultrasound")

    def test_the_doctor_is_told_and_the_message_says_something_useful(self):
        route_id = self._refer()
        self._record(route_id, file=self._report())      # upload only, nothing typed
        note = Notification.objects.filter(recipient=self.doctor, category="clinical").first()
        self.assertIsNotNone(note)
        self.assertIn("Laboratory result", note.title)
        self.assertIn("uploaded", note.message)
        self.assertIn("Chidi Eze", note.message)
        # Straight to the chart's Lab tab, not its front page: the doctor is
        # being told a result exists, so the link should be the result.
        self.assertEqual(note.action_url, f"/patients/{self.patient.id}/lab")

    def test_the_doctor_can_read_the_result_and_open_the_report(self):
        route_id = self._refer()
        self._record(route_id, result="Hb 11.2 g/dL.", title="Full blood count",
                     file=self._report())

        overview = self.doctor_client.get(f"/api/patients/{self.patient.id}/overview/")
        self.assertEqual(overview.status_code, 200)

        # On the Tests & Diagnostics tile, with the document attached…
        filed = overview.data["tests"][0]
        self.assertEqual(filed["title"], "Full blood count")
        self.assertEqual(filed["test_type"], "Labs")
        self.assertEqual(filed["impressions"], "Hb 11.2 g/dL.")
        self.assertTrue(filed["file_url"].startswith("/media/"))

        # …and against the referral itself, so the ask and the answer sit
        # together.
        routes = [r for v in overview.data["visits"] for r in v["routes"]]
        lab_route = next(r for r in routes if r["purpose"] == "Laboratory")
        self.assertEqual(lab_route["result"], "Hb 11.2 g/dL.")
        self.assertEqual(lab_route["result_by"], "Chidi Eze")

    def test_the_result_survives_the_visit_closing(self):
        """A route is one errand; the record is what is there next year."""
        route_id = self._refer()
        self._record(route_id, result="Hb 11.2 g/dL.")
        self.lab_client.post(f"/api/patient-routes/{route_id}/complete/", {}, format="json")
        self.visit.status = "completed"
        self.visit.save(update_fields=["status"])

        self.assertEqual(MedicalTest.objects.filter(patient=self.patient).count(), 1)
