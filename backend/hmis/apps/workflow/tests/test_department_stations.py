"""
The referral units as departments in their own right: their own queue, and
a result that finds its way back to the doctor who asked.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Notification
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class DepartmentStationTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor",
                                               first_name="Ada", last_name="Obi")
        self.lab = User.objects.create_user(username="lab", password="test", role="laboratory",
                                            first_name="Chidi", last_name="Eze")
        self.radiology = User.objects.create_user(username="radio", password="test", role="radiology")
        self.department = Department.objects.create(code="general-medicine", name="General Medicine")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)

        self.doctor_client = APIClient(); self.doctor_client.force_authenticate(self.doctor)
        self.lab_client = APIClient(); self.lab_client.force_authenticate(self.lab)

    def _refer(self, purpose="laboratory", notes="FBC please"):
        response = self.doctor_client.post("/api/patient-routes/refer/", {
            "patient": self.patient.id, "purpose": purpose, "notes": notes,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def test_the_unit_is_sent_to_its_own_station_not_the_generic_queue(self):
        self._refer("laboratory")
        note = Notification.objects.get(recipient=self.lab, category="routing")
        self.assertEqual(note.action_url, "/laboratory")

    def test_imaging_is_sent_to_its_own_station(self):
        self._refer("ultrasound")
        note = Notification.objects.get(recipient=self.radiology, category="routing")
        self.assertEqual(note.action_url, "/ultrasound")

    def test_a_unit_records_a_result_and_keeps_the_patient(self):
        route_id = self._refer()
        self.lab_client.post(f"/api/patient-routes/{route_id}/accept/")
        response = self.lab_client.post(f"/api/patient-routes/{route_id}/record-result/",
                                        {"result": "Hb 11.2 g/dL, no parasites seen."}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        route = PatientRoute.objects.get(pk=route_id)
        self.assertEqual(route.result, "Hb 11.2 g/dL, no parasites seen.")
        self.assertEqual(route.result_by, self.lab)
        self.assertIsNotNone(route.result_at)
        # Still theirs — saving a result is not the same as finishing.
        self.assertEqual(route.status, "in_progress")

    def test_the_doctor_who_asked_is_told_the_result_is_in(self):
        route_id = self._refer()
        self.lab_client.post(f"/api/patient-routes/{route_id}/accept/")
        self.lab_client.post(f"/api/patient-routes/{route_id}/record-result/",
                             {"result": "Hb 11.2 g/dL."}, format="json")

        note = Notification.objects.filter(recipient=self.doctor, category="clinical").first()
        self.assertIsNotNone(note, "the doctor was not told their result was ready")
        self.assertIn("Laboratory result", note.title)
        self.assertIn("Hb 11.2", note.message)
        self.assertEqual(note.action_url, f"/patients/{self.patient.id}/lab")

    def test_closing_the_work_can_carry_the_result_with_it(self):
        route_id = self._refer()
        self.lab_client.post(f"/api/patient-routes/{route_id}/accept/")
        response = self.lab_client.post(f"/api/patient-routes/{route_id}/complete/",
                                        {"result": "Normal study."}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        route = PatientRoute.objects.get(pk=route_id)
        self.assertEqual(route.status, "completed")
        self.assertEqual(route.result, "Normal study.")
        self.assertEqual(route.result_by, self.lab)

    def test_an_empty_result_is_refused(self):
        route_id = self._refer()
        self.lab_client.post(f"/api/patient-routes/{route_id}/accept/")
        response = self.lab_client.post(f"/api/patient-routes/{route_id}/record-result/",
                                        {"result": "   "}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("result", response.data)

    def test_somebody_elses_work_cannot_be_written_on(self):
        """
        404, not 403: once a colleague claims the request it leaves everyone
        else's queue, so it is not theirs to find, let alone write on.
        """
        route_id = self._refer()
        other = User.objects.create_user(username="lab2", password="test", role="laboratory")
        self.lab_client.post(f"/api/patient-routes/{route_id}/accept/")

        client = APIClient(); client.force_authenticate(other)
        response = client.post(f"/api/patient-routes/{route_id}/record-result/",
                               {"result": "Made up."}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(PatientRoute.objects.get(pk=route_id).result, "")

    def test_the_result_cannot_be_patched_onto_the_row_directly(self):
        """It is stamped with who wrote it, so it goes through the action."""
        route_id = self._refer()
        self.lab_client.post(f"/api/patient-routes/{route_id}/accept/")
        # Reception is the only role that may PATCH a route at all.
        client = APIClient(); client.force_authenticate(self.reception)
        client.patch(f"/api/patient-routes/{route_id}/", {"result": "Forged."}, format="json")
        self.assertEqual(PatientRoute.objects.get(pk=route_id).result, "")

    def test_the_result_reaches_the_doctors_chart(self):
        route_id = self._refer()
        self.lab_client.post(f"/api/patient-routes/{route_id}/accept/")
        self.lab_client.post(f"/api/patient-routes/{route_id}/complete/",
                             {"result": "Hb 11.2 g/dL, no parasites seen."}, format="json")

        overview = self.doctor_client.get(f"/api/patients/{self.patient.id}/overview/")
        self.assertEqual(overview.status_code, 200)
        routes = [r for v in overview.data["visits"] for r in v["routes"]]
        lab_route = next(r for r in routes if r["purpose"] == "Laboratory")
        self.assertEqual(lab_route["result"], "Hb 11.2 g/dL, no parasites seen.")
        self.assertEqual(lab_route["result_by"], "Chidi Eze")
        self.assertEqual(lab_route["notes"], "FBC please")

    def test_a_unit_only_sees_its_own_work(self):
        lab_route = self._refer("laboratory")
        scan_route = self._refer("ultrasound")

        lab_queue = [r["id"] for r in self.lab_client.get("/api/patient-routes/").data["results"]]
        self.assertIn(lab_route, lab_queue)
        self.assertNotIn(scan_route, lab_queue)

        radio = APIClient(); radio.force_authenticate(self.radiology)
        radio_queue = [r["id"] for r in radio.get("/api/patient-routes/").data["results"]]
        self.assertIn(scan_route, radio_queue)
        self.assertNotIn(lab_route, radio_queue)

    def test_the_queue_row_says_who_asked(self):
        self._refer()
        row = self.lab_client.get("/api/patient-routes/").data["results"][0]
        self.assertEqual(row["routed_by_name"], "Ada Obi")
        self.assertEqual(row["purpose_label"], "Laboratory")
