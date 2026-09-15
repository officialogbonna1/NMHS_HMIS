"""
The eye doctor works the existing ward for their own patients only
(`OWN_PATIENT_WARD_ROLES`): free and occupied beds, a name only on a bed that
is theirs, and admitting, moving and discharging only their own patient. The
general doctor and the rest of WARD_ROLES keep the whole ward.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.departments.models import Department
from apps.inpatient.models import Admission, Bed, Ward
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class EyeDoctorWardScopeTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.eye_a = User.objects.create_user(username="eye_a", password="t", role="ophthalmologist")
        self.eye_b = User.objects.create_user(username="eye_b", password="t", role="ophthalmologist")
        self.department = Department.objects.create(code="eye-unit-test", name="Eye Unit (test)")

        self.patient_a = self._patient("Ada", eye_doctor=self.eye_a)
        self.patient_b = self._patient("Bola", eye_doctor=self.eye_b)
        self.other = Patient.objects.create(first_name="Chi", last_name="Test", sex="M", created_by=self.reception)
        Visit.objects.create(patient=self.other, opened_by=self.reception, attending_doctor=self.doctor)

        self.ward = Ward.objects.create(name="Ward A (test)")
        self.beds = [Bed.objects.create(ward=self.ward, number=str(n)) for n in (1, 2, 3)]
        # A general doctor's inpatient already in bed 1.
        response = self.as_(self.doctor).post("/api/admissions/", {
            "patient": self.other.id, "bed": self.beds[0].id, "diagnosis": "Pneumonia"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.other_admission = response.data["id"]

    def _patient(self, first_name, eye_doctor):
        patient = Patient.objects.create(first_name=first_name, last_name="Test", sex="F", created_by=self.reception)
        visit = Visit.objects.create(patient=patient, opened_by=self.reception)
        PatientRoute.objects.create(visit=visit, department=self.department, purpose="eye",
                                    assigned_to=eye_doctor, routed_by=self.reception, status="in_progress")
        return patient

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def bed(self, user, bed):
        return next(row for row in rows(self.as_(user).get("/api/beds/")) if row["id"] == bed.id)

    def ward_row(self, user):
        return next(row for row in rows(self.as_(user).get("/api/wards/")) if row["id"] == self.ward.id)

    def admit(self, user, patient, bed):
        return self.as_(user).post("/api/admissions/", {"patient": patient.id, "bed": bed.id,
                                                        "diagnosis": "Endophthalmitis"}, format="json")

    def test_the_bed_board_shows_a_taken_bed_without_whose_it_is(self):
        taken = self.bed(self.eye_a, self.beds[0])
        self.assertEqual((taken["occupied"], taken["occupant"]), (True, None))
        self.assertFalse(self.bed(self.eye_a, self.beds[1])["occupied"])
        ward = self.ward_row(self.eye_a)
        self.assertEqual((ward["bed_count"], ward["occupied_count"]), (3, 1))
        # The ward itself still sees the name.
        self.assertEqual(self.bed(self.doctor, self.beds[0])["occupant"], str(self.other))

    def test_the_eye_doctor_admits_their_own_patient_through_the_existing_admission(self):
        response = self.admit(self.eye_a, self.patient_a, self.beds[1])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Admission.objects.get(pk=response.data["id"]).admitted_by, self.eye_a)
        self.assertEqual(self.bed(self.eye_a, self.beds[1])["occupant"], str(self.patient_a))
        self.assertEqual(self.ward_row(self.eye_a)["occupied_count"], 2)
        # Another eye doctor sees the bed taken, not by whom.
        self.assertEqual(self.bed(self.eye_b, self.beds[1])["occupant"], None)

    def test_somebody_elses_patient_cannot_be_admitted(self):
        for patient in (self.patient_b, self.other):
            with self.subTest(patient=patient.first_name):
                self.assertEqual(self.admit(self.eye_a, patient, self.beds[1]).status_code, 403)
        self.assertFalse(Admission.objects.filter(bed=self.beds[1]).exists())

    def test_an_occupied_bed_is_still_refused(self):
        self.assertEqual(self.admit(self.eye_a, self.patient_a, self.beds[0]).status_code, 400)
        self.assertFalse(Admission.objects.filter(patient=self.patient_a).exists())

    def test_only_their_own_inpatients_are_listed(self):
        self.admit(self.eye_a, self.patient_a, self.beds[1])
        self.assertEqual({row["patient"] for row in rows(self.as_(self.eye_a).get("/api/admissions/"))},
                         {self.patient_a.id})
        self.assertEqual(self.as_(self.eye_a).get(f"/api/admissions/{self.other_admission}/").status_code, 404)
        self.assertEqual(len(rows(self.as_(self.doctor).get("/api/admissions/"))), 2)

    def test_they_move_and_discharge_their_own_patient_and_nobody_elses(self):
        api = self.as_(self.eye_a)
        admission = self.admit(self.eye_a, self.patient_a, self.beds[1]).data["id"]

        moved = api.post("/api/bed-transfers/", {"admission": admission, "to_bed": self.beds[2].id,
                                                 "reason": "Nearer the nurses' station"}, format="json")
        self.assertEqual(moved.status_code, 201, moved.data)
        self.assertEqual(Admission.objects.get(pk=admission).bed, self.beds[2])

        self.assertEqual(api.post("/api/bed-transfers/", {"admission": self.other_admission,
                                                          "to_bed": self.beds[1].id}, format="json").status_code, 403)
        self.assertEqual(Admission.objects.get(pk=self.other_admission).bed, self.beds[0])
        self.assertEqual(api.post("/api/discharges/", {"admission": self.other_admission, "diagnosis": "x",
                                                       "summary": "x"}, format="json").status_code, 403)
        self.assertEqual(Admission.objects.get(pk=self.other_admission).status, "admitted")

        done = api.post("/api/discharges/", {"admission": admission, "diagnosis": "Endophthalmitis",
                                             "summary": "Treated with intravitreal antibiotics"}, format="json")
        self.assertEqual(done.status_code, 201, done.data)
        self.assertEqual(Admission.objects.get(pk=admission).status, "discharged")
        self.assertFalse(self.bed(self.eye_a, self.beds[2])["occupied"])

    def test_wards_and_beds_stay_configuration(self):
        api = self.as_(self.eye_a)
        self.assertEqual(api.post("/api/wards/", {"name": "Eye ward"}, format="json").status_code, 403)
        self.assertEqual(api.post("/api/beds/", {"ward": self.ward.id, "number": "9"}, format="json").status_code, 403)
