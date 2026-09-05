"""
The printable referral document, and who may read which half of it.

`GET /patient-routes/<id>/document/` answers two documents from one payload:
the request form the unit works from, and the finding that answers it. The
split matters — reception raises referrals and prints the form, but a scan
report is clinical and must never come back to the front desk.

The other rule under test: a document outlives the queue. The queue is
`queued`/`in_progress` on purpose, but the bench reprints the form for work
it finished this morning, and the doctor files the report afterwards.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


class ReferralDocumentTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(
            username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(
            username="doctor", password="test", role="doctor",
            first_name="Ada", last_name="Obi")
        self.other_doctor = User.objects.create_user(
            username="doctor2", password="test", role="doctor")
        self.radiology = User.objects.create_user(
            username="radio", password="test", role="radiology",
            first_name="Ngozi", last_name="Uche")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")

        Department.objects.create(code="general-medicine", name="General Medicine")
        self.patient = Patient.objects.create(
            first_name="Jane", last_name="Doe", sex="F", created_by=self.reception)
        self.visit = Visit.objects.create(
            patient=self.patient, opened_by=self.reception, attending_doctor=self.doctor)

        self.doctor_client = self._client(self.doctor)
        self.radiology_client = self._client(self.radiology)
        self.reception_client = self._client(self.reception)

        response = self.doctor_client.post("/api/patient-routes/refer/", {
            "patient": self.patient.id, "purpose": "ultrasound",
            "notes": "Rule out gallstones.",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.route_id = response.data["id"]

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _document(self, client):
        return client.get(f"/api/patient-routes/{self.route_id}/document/")

    def _write_result(self):
        self.radiology_client.post(f"/api/patient-routes/{self.route_id}/accept/")
        response = self.radiology_client.post(
            f"/api/patient-routes/{self.route_id}/record-result/",
            {"result": "Normal gallbladder. No stones seen.", "title": "Abdominal ultrasound"},
            format="json")
        self.assertEqual(response.status_code, 200, response.data)

    # --- the request form ---------------------------------------------

    def test_the_unit_can_print_the_request_it_is_working_from(self):
        response = self._document(self.radiology_client)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["route"]["purpose"], "ultrasound")
        self.assertEqual(response.data["route"]["notes"], "Rule out gallstones.")
        self.assertEqual(response.data["route"]["routed_by"], "Ada Obi")
        # Enough identity to head a form without a second request.
        self.assertEqual(response.data["patient"]["file_number"], self.patient.file_number)
        self.assertEqual(response.data["patient"]["name"], "Doe, Jane")
        self.assertEqual(response.data["patient"]["sex"], "Female")

    def test_the_doctor_who_referred_can_print_the_form(self):
        self.assertEqual(self._document(self.doctor_client).status_code, 200)

    def test_reception_cannot_print_a_referral_a_doctor_raised(self):
        """
        The front desk prints the forms it raised. A doctor's referral carries
        the doctor's clinical note, and hiding it from reception's queue while
        leaving it printable would only move the leak one URL along.
        """
        self.assertEqual(self._document(self.reception_client).status_code, 404)

    def test_reception_can_print_the_form_it_raised_itself(self):
        route = PatientRoute.objects.create(
            visit=self.visit, department=Department.objects.first(), purpose="vitals",
            routed_by=self.reception, notes="Please take her vitals.")
        response = self.reception_client.get(f"/api/patient-routes/{route.pk}/document/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["route"]["purpose"], "vitals")

    # --- the finding, and who is allowed to read it --------------------

    def test_the_finding_travels_with_the_document_for_the_clinical_roles(self):
        self._write_result()
        for client in (self.radiology_client, self.doctor_client):
            result = self._document(client).data["result"]
            self.assertIsNotNone(result, "a clinical role was refused its own report")
            self.assertEqual(result["text"], "Normal gallbladder. No stones seen.")
            self.assertEqual(result["recorded_by"], "Ngozi Uche")
            self.assertEqual(result["title"], "Abdominal ultrasound")

    def test_reception_never_gets_a_clinical_result(self):
        """
        Belt and braces. Reception cannot reach a doctor's referral at all
        now, but the result half is stripped for the front desk regardless —
        so a route reception *did* raise never carries a clinical finding back
        to the desk either.
        """
        self._write_result()
        self.assertEqual(self._document(self.reception_client).status_code, 404)

        route = PatientRoute.objects.create(
            visit=self.visit, department=Department.objects.first(), purpose="vitals",
            routed_by=self.reception, notes="Please take her vitals.")
        route.result = "BP 180/110, referred urgently."
        route.result_by = self.nurse
        route.save(update_fields=["result", "result_by"])

        response = self.reception_client.get(f"/api/patient-routes/{route.pk}/document/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["result"], "a finding came back to the front desk")
        self.assertNotIn("180/110", str(response.data))

    def test_a_doctor_not_holding_the_patient_cannot_print_the_document(self):
        response = self._document(self._client(self.other_doctor))
        self.assertEqual(response.status_code, 404)

    def test_a_nurse_who_does_not_work_imaging_gets_nothing(self):
        # Vitals is the nurse's pool; an ultrasound referral is not, and this
        # patient is not routed to them.
        self.assertEqual(self._document(self._client(self.nurse)).status_code, 404)

    # --- a document outlives the queue --------------------------------

    def test_the_form_still_prints_after_the_work_is_closed(self):
        self._write_result()
        self.radiology_client.post(f"/api/patient-routes/{self.route_id}/complete/")
        self.assertEqual(
            PatientRoute.objects.get(pk=self.route_id).status, "completed")

        # Off the queue, still on the printer.
        queue = self.radiology_client.get("/api/patient-routes/").data
        rows = queue.get("results", queue)
        self.assertNotIn(self.route_id, [row["id"] for row in rows])
        self.assertEqual(self._document(self.radiology_client).status_code, 200)
        self.assertEqual(self._document(self.doctor_client).status_code, 200)

    def test_the_queue_itself_is_still_only_live_work(self):
        """The widened document rule must not leak into the working queue."""
        self.radiology_client.post(f"/api/patient-routes/{self.route_id}/accept/")
        self.radiology_client.post(f"/api/patient-routes/{self.route_id}/complete/",
                                   {"result": "Normal study."}, format="json")
        queue = self.radiology_client.get("/api/patient-routes/").data
        rows = queue.get("results", queue)
        self.assertEqual([row["id"] for row in rows], [])
