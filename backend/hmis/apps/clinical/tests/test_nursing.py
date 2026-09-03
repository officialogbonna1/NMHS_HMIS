from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.clinical.models import NursingNote
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class NursingStationTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.other_nurse = User.objects.create_user(username="nurse2", password="test", role="nurse")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F", created_by=self.reception)
        self.nursing = Department.objects.create(code="nursing", name="Nursing")

        self.reception_client = APIClient(); self.reception_client.force_authenticate(self.reception)
        self.nurse_client = APIClient(); self.nurse_client.force_authenticate(self.nurse)
        self.doctor_client = APIClient(); self.doctor_client.force_authenticate(self.doctor)

    def _route_for_vitals(self):
        visit = self.reception_client.post("/api/visits/", {"patient": self.patient.id, "visit_type": "opd", "reason": "Fever"})
        return self.reception_client.post("/api/patient-routes/", {
            "visit": visit.data["id"], "department": self.nursing.id,
            "purpose": "vitals", "assigned_to": self.nurse.id, "priority": "routine",
        })

    def test_reception_routes_a_patient_to_a_nurse_for_vitals(self):
        response = self._route_for_vitals()
        self.assertEqual(response.status_code, 201)
        route = PatientRoute.objects.get(pk=response.data["id"])
        self.assertEqual(route.purpose, "vitals")
        self.assertEqual(route.assigned_to, self.nurse)

        # It lands in that nurse's queue and nobody else's.
        queue = self.nurse_client.get("/api/patient-routes/")
        self.assertEqual([r["id"] for r in queue.data["results"]], [route.id])
        other = APIClient(); other.force_authenticate(self.other_nurse)
        self.assertEqual(other.get("/api/patient-routes/").data["results"], [])

    def test_routing_to_a_disabled_account_is_rejected(self):
        self.nurse.is_active = False
        self.nurse.save(update_fields=["is_active"])
        response = self._route_for_vitals()
        self.assertEqual(response.status_code, 400)
        self.assertIn("disabled", str(response.data))

    def test_the_nurse_records_vitals_and_a_note_then_closes_the_route(self):
        route_id = self._route_for_vitals().data["id"]

        vitals = self.nurse_client.post("/api/vitals/", {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(),
            "temperature_c": "38.2", "heart_rate": 96, "bp_systolic": 130, "bp_diastolic": 85,
        })
        self.assertEqual(vitals.status_code, 201)

        note = self.nurse_client.post("/api/nursing-notes/", {
            "patient": self.patient.id, "complaint": "Headache since morning",
            "observation": "Alert, febrile, no rash.", "vitals": vitals.data["id"],
        })
        self.assertEqual(note.status_code, 201)
        saved = NursingNote.objects.get(pk=note.data["id"])
        self.assertEqual(saved.nurse, self.nurse)
        self.assertTrue(saved.is_locked)

        response = self.nurse_client.post(f"/api/patient-routes/{route_id}/complete/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PatientRoute.objects.get(pk=route_id).status, "completed")

    def test_a_nurse_cannot_close_a_route_that_is_not_theirs(self):
        route_id = self._route_for_vitals().data["id"]
        other = APIClient(); other.force_authenticate(self.other_nurse)
        # The route is not in their queue at all, so it does not resolve.
        self.assertEqual(other.post(f"/api/patient-routes/{route_id}/complete/").status_code, 404)

    def test_nursing_notes_are_not_editable_once_saved(self):
        note = self.nurse_client.post("/api/nursing-notes/", {
            "patient": self.patient.id, "observation": "Stable.",
        })
        self.assertEqual(note.status_code, 201)
        response = self.nurse_client.patch(f"/api/nursing-notes/{note.data['id']}/", {"observation": "Changed"})
        self.assertEqual(response.status_code, 405)

    def test_the_doctor_reads_the_nursing_note_for_their_own_patient(self):
        self.nurse_client.post("/api/nursing-notes/", {
            "patient": self.patient.id, "complaint": "Headache", "observation": "Febrile.",
        })
        self.assertEqual(self.doctor_client.get("/api/nursing-notes/").data["results"], [])

        Appointment.objects.create(patient=self.patient, doctor=self.doctor, reason="Fever")
        results = self.doctor_client.get("/api/nursing-notes/", {"patient": self.patient.id}).data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["observation"], "Febrile.")
        self.assertEqual(results[0]["nurse_name"], "nurse")

    def test_reception_cannot_read_nursing_notes(self):
        self.nurse_client.post("/api/nursing-notes/", {"patient": self.patient.id, "observation": "Febrile."})
        self.assertEqual(self.reception_client.get("/api/nursing-notes/").status_code, 403)
