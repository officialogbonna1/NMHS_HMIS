"""
The ward: admitting, moving and discharging.

The API existed with no page in front of it, so none of this had ever been
exercised through HTTP.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inpatient.models import Ward, Bed, Admission
from apps.patients.models import Patient


class AdmissionTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.ward_manager = User.objects.create_user(username="ward", password="test", role="ward_manager")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.other = Patient.objects.create(first_name="John", last_name="Roe", sex="M",
                                            created_by=self.reception)
        self.ward = Ward.objects.create(name="Male Medical")
        self.bed_a = Bed.objects.create(ward=self.ward, number="1")
        self.bed_b = Bed.objects.create(ward=self.ward, number="2")

        self.client = APIClient(); self.client.force_authenticate(self.ward_manager)

    def _admit(self, patient=None, bed=None):
        return self.client.post("/api/admissions/", {
            "patient": (patient or self.patient).id,
            "bed": (bed or self.bed_a).id,
            "attending_doctor": self.doctor.id,
            "diagnosis": "Severe malaria",
        }, format="json")

    def test_admitting_puts_the_patient_in_the_bed(self):
        response = self._admit()
        self.assertEqual(response.status_code, 201, response.data)
        admission = Admission.objects.get(pk=response.data["id"])
        self.assertEqual(admission.status, "admitted")
        self.assertEqual(admission.admitted_by, self.ward_manager)
        # The page reads these rather than fetching each patient.
        self.assertEqual(response.data["patient_name"], str(self.patient))
        self.assertEqual(response.data["ward_name"], "Male Medical")
        self.assertEqual(response.data["bed_number"], "1")

    def test_two_patients_cannot_share_a_bed(self):
        self._admit()
        response = self._admit(patient=self.other)
        self.assertEqual(response.status_code, 400)
        self.assertIn("bed", response.data)
        self.assertEqual(Admission.objects.filter(status="admitted").count(), 1)

    def test_the_bed_board_says_who_is_in_which_bed(self):
        self._admit()
        beds = {b["number"]: b for b in self.client.get("/api/beds/").data["results"]}
        self.assertTrue(beds["1"]["occupied"])
        self.assertEqual(beds["1"]["occupant"], str(self.patient))
        self.assertFalse(beds["2"]["occupied"])
        self.assertIsNone(beds["2"]["occupant"])

    def test_the_ward_counts_its_own_occupancy(self):
        self._admit()
        ward = self.client.get("/api/wards/").data["results"][0]
        self.assertEqual(ward["bed_count"], 2)
        self.assertEqual(ward["occupied_count"], 1)

    def test_moving_a_patient_frees_the_old_bed(self):
        admission_id = self._admit().data["id"]
        response = self.client.post("/api/bed-transfers/", {
            "admission": admission_id, "to_bed": self.bed_b.id, "reason": "Nearer the station",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)

        self.assertEqual(Admission.objects.get(pk=admission_id).bed, self.bed_b)
        beds = {b["number"]: b for b in self.client.get("/api/beds/").data["results"]}
        self.assertFalse(beds["1"]["occupied"])
        self.assertTrue(beds["2"]["occupied"])

    def test_a_patient_cannot_be_moved_into_an_occupied_bed(self):
        first = self._admit().data["id"]
        self._admit(patient=self.other, bed=self.bed_b)
        response = self.client.post("/api/bed-transfers/", {
            "admission": first, "to_bed": self.bed_b.id,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("to_bed", response.data)

    def test_discharging_sends_them_home_and_frees_the_bed(self):
        admission_id = self._admit().data["id"]
        response = self.client.post("/api/discharges/", {
            "admission": admission_id, "diagnosis": "Malaria, resolved",
            "summary": "Responded to artemether. Afebrile 48h.",
            "instructions": "Complete the course.",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)

        admission = Admission.objects.get(pk=admission_id)
        self.assertEqual(admission.status, "discharged")
        self.assertIsNotNone(admission.discharged_at)
        self.assertFalse(self.client.get("/api/beds/").data["results"][0]["occupied"])

    def test_discharging_twice_is_refused(self):
        admission_id = self._admit().data["id"]
        payload = {"admission": admission_id, "diagnosis": "x", "summary": "y"}
        self.assertEqual(self.client.post("/api/discharges/", payload, format="json").status_code, 201)
        second = self.client.post("/api/discharges/", payload, format="json")
        self.assertEqual(second.status_code, 400)

    def test_a_discharged_bed_can_take_the_next_patient(self):
        admission_id = self._admit().data["id"]
        self.client.post("/api/discharges/", {
            "admission": admission_id, "diagnosis": "x", "summary": "y",
        }, format="json")
        self.assertEqual(self._admit(patient=self.other).status_code, 201)

    def test_the_admin_dashboards_bed_count_matches_the_ward(self):
        self._admit()
        admin = User.objects.create_user(username="admin_user", password="test", role="admin")
        client = APIClient(); client.force_authenticate(admin)
        data = client.get("/api/dashboard/").data
        card = next(c for c in data["cards"] if c["label"] == "Active admissions")
        self.assertEqual(card["value"], 1)
        self.assertEqual(card["href"], "/admissions")
