"""
The eye doctor reads the chart through the rule the general doctor does
(`patients.access.doctor_patient_q`) — and nothing wider.

* their patient opens; another eye doctor's, and anybody else's, does not —
  by UUID, by integer pk, by hospital number or through a `?patient=` filter;
* an unclaimed eye referral is in the shared /eye queue, but its chart opens
  only once somebody claims it (rule 14);
* a patient they have treated stays readable (rule 9);
* vitals are the existing vitals: read, never recorded;
* the optometrist keeps exactly what they had.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.models import ConsultationNote, NursingNote, Vitals
from apps.core.models import Notification
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class EyeDoctorPatientAccessTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="t", role="nurse")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.eye_a = User.objects.create_user(username="eye_a", password="t", role="ophthalmologist")
        self.eye_b = User.objects.create_user(username="eye_b", password="t", role="ophthalmologist")
        self.optometrist = User.objects.create_user(username="opto", password="t", role="optometrist")
        self.department = Department.objects.create(code="eye-unit-test", name="Eye Unit (test)")

        self.patient_a = self._patient("Ada")
        self.route_a = self._route(self.patient_a, assigned_to=self.eye_a)
        self.patient_b = self._patient("Bola")
        self._route(self.patient_b, assigned_to=self.eye_b)
        self.waiting = self._patient("Chi")
        self.unclaimed = self._route(self.waiting, assigned_to=None, status="queued")
        self.stranger = self._patient("Dayo")

    def _patient(self, first_name):
        return Patient.objects.create(first_name=first_name, last_name="Test", sex="F",
                                      created_by=self.reception)

    def _route(self, patient, assigned_to, status="in_progress"):
        visit = Visit.objects.create(patient=patient, opened_by=self.reception)
        return PatientRoute.objects.create(visit=visit, department=self.department, purpose="eye",
                                           assigned_to=assigned_to, routed_by=self.reception,
                                           status=status)

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def ids(self, response):
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return {row["id"] for row in rows(response)}

    def take_vitals(self, patient, temperature="37.1"):
        response = self.as_(self.nurse).post("/api/vitals/", {
            "patient": patient.id, "visit_time": timezone.now().isoformat(),
            "temperature_c": temperature, "heart_rate": 80}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response

    # --- which patients -------------------------------------------------------

    def test_the_patient_list_is_the_eye_doctors_own(self):
        self.assertEqual(self.ids(self.as_(self.eye_a).get("/api/patients/")), {self.patient_a.id})
        self.assertEqual(self.ids(self.as_(self.eye_b).get("/api/patients/")), {self.patient_b.id})

    def test_another_patient_cannot_be_opened_by_changing_the_identifier(self):
        api = self.as_(self.eye_a)
        for patient in (self.patient_b, self.stranger, self.waiting):
            for ident in (patient.uuid, patient.pk):
                with self.subTest(patient=patient.first_name, ident=ident):
                    self.assertEqual(api.get(f"/api/patients/{ident}/").status_code, 404)
                    self.assertEqual(api.get(f"/api/patients/{ident}/overview/").status_code, 404)
            self.assertEqual(self.ids(api.get("/api/patients/", {"search": patient.patient_number})), set())
        self.assertEqual(api.get(f"/api/patients/{self.patient_a.uuid}/overview/").status_code, 200)

    def test_nothing_on_another_patients_chart_comes_back_through_a_filter(self):
        self.take_vitals(self.patient_b)
        self.as_(self.nurse).post("/api/nursing-notes/", {"patient": self.patient_b.id,
                                                          "observation": "Red eye"}, format="json")
        ConsultationNote.objects.create(patient=self.patient_b, doctor=self.eye_b,
                                        visit_time=timezone.now(), reason_for_visit="Red eye",
                                        eye_examination={"iop_right": 30})
        api = self.as_(self.eye_a)
        for path in ("/api/vitals/", "/api/nursing-notes/", "/api/notes/", "/api/allergies/",
                     "/api/prescriptions/", "/api/lab-orders/", "/api/admissions/"):
            with self.subTest(path=path):
                self.assertEqual(self.ids(api.get(path, {"patient": self.patient_b.id, "detail": "1"})), set())

    def test_an_unclaimed_referral_is_queued_but_its_chart_opens_only_once_claimed(self):
        api = self.as_(self.eye_a)
        self.assertIn(self.unclaimed.id, self.ids(api.get("/api/patient-routes/")))
        self.assertEqual(api.get(f"/api/patients/{self.waiting.uuid}/").status_code, 404)

        self.assertEqual(api.post(f"/api/patient-routes/{self.unclaimed.id}/accept/").status_code, 200)
        self.assertEqual(api.get(f"/api/patients/{self.waiting.uuid}/overview/").status_code, 200)
        # Claimed, it leaves the other eye doctor's queue, and their chart stays shut.
        other = self.as_(self.eye_b)
        self.assertNotIn(self.unclaimed.id, self.ids(other.get("/api/patient-routes/")))
        self.assertEqual(other.get(f"/api/patients/{self.waiting.uuid}/").status_code, 404)

    def test_another_eye_doctors_named_referral_is_not_in_the_queue(self):
        self.assertEqual(self.ids(self.as_(self.eye_a).get("/api/patient-routes/")),
                         {self.route_a.id, self.unclaimed.id})

    def test_a_patient_they_have_treated_stays_readable_after_moving_on(self):
        self.route_a.status = "completed"
        self.route_a.save(update_fields=["status"])
        self._route(self.patient_a, assigned_to=self.eye_b)
        self.assertEqual(self.as_(self.eye_a).get(f"/api/patients/{self.patient_a.uuid}/").status_code, 200)
        self.assertEqual(self.as_(self.eye_b).get(f"/api/patients/{self.patient_a.uuid}/").status_code, 200)

    # --- vitals ---------------------------------------------------------------

    def test_vitals_and_their_history_come_from_the_existing_vitals_system(self):
        self.take_vitals(self.patient_a, "37.1")
        self.take_vitals(self.patient_a, "38.4")
        self.as_(self.nurse).post("/api/nursing-notes/", {"patient": self.patient_a.id,
                                                          "observation": "Watering right eye"}, format="json")
        api = self.as_(self.eye_a)
        readings = rows(api.get("/api/vitals/", {"patient": self.patient_a.id}))
        self.assertEqual(sorted(r["temperature_c"] for r in readings), ["37.1", "38.4"])
        self.assertEqual([n["observation"] for n in rows(api.get("/api/nursing-notes/", {"patient": self.patient_a.id}))],
                         ["Watering right eye"])
        overview = api.get(f"/api/patients/{self.patient_a.uuid}/overview/").data
        self.assertEqual(len(overview["vitals_history"]), 2)
        # Still the nurse's readings, and still only these two.
        self.assertEqual(set(Vitals.objects.values_list("recorded_by", flat=True)), {self.nurse.pk})
        self.assertEqual(Vitals.objects.count(), 2)

    def test_the_eye_doctor_reads_vitals_but_never_records_them(self):
        api = self.as_(self.eye_a)
        response = api.post("/api/vitals/", {"patient": self.patient_a.id, "visit_time": timezone.now().isoformat(),
                                             "temperature_c": "37.0"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(api.post("/api/nursing-notes/", {"patient": self.patient_a.id,
                                                          "observation": "x"}, format="json").status_code, 403)
        self.assertFalse(Vitals.objects.exists())
        self.assertFalse(NursingNote.objects.exists())

    def test_the_eye_doctor_holding_the_patient_is_told_when_vitals_land(self):
        self.take_vitals(self.patient_a)
        note = Notification.objects.get(recipient=self.eye_a, category="clinical")
        self.assertEqual(note.action_url, f"/patients/{self.patient_a.uuid}/vitals")
        self.assertFalse(Notification.objects.filter(recipient=self.eye_b).exists())

    # --- the roles around them ------------------------------------------------

    def test_the_optometrist_keeps_exactly_what_they_had(self):
        api = self.as_(self.optometrist)
        self.assertEqual(api.get(f"/api/patients/{self.patient_a.uuid}/overview/").status_code, 403)
        for path in ("/api/vitals/", "/api/notes/", "/api/prescriptions/", "/api/admissions/"):
            with self.subTest(path=path):
                self.assertEqual(api.get(path).status_code, 403)
        self.assertEqual(api.post("/api/patient-routes/refer/", {"patient": self.patient_a.id,
                                                                 "purpose": "laboratory"}, format="json").status_code, 403)
        # And still works the eye queue.
        self.assertIn(self.unclaimed.id, self.ids(api.get("/api/patient-routes/")))

    def test_a_general_doctor_still_reaches_only_the_patients_they_hold(self):
        Visit.objects.create(patient=self.stranger, opened_by=self.reception, attending_doctor=self.doctor)
        self.assertEqual(self.ids(self.as_(self.doctor).get("/api/patients/")), {self.stranger.id})
