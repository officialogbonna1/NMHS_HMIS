"""
The nurse's day list, and the chain it reports on: a reading taken here has
to reach the doctor the patient was sent to.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.models import Vitals, NursingNote
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class RecordedTodayTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.other_nurse = User.objects.create_user(username="nurse2", password="test", role="nurse")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor",
                                               first_name="Ada", last_name="Obi")
        self.department = Department.objects.create(code="general-medicine", name="General Medicine")

        self.nurse_client = APIClient(); self.nurse_client.force_authenticate(self.nurse)
        self.doctor_client = APIClient(); self.doctor_client.force_authenticate(self.doctor)

    def _patient(self, first="Jane", last="Doe"):
        return Patient.objects.create(first_name=first, last_name=last, sex="F", created_by=self.reception)

    def _visit(self, patient):
        return Visit.objects.create(patient=patient, opened_by=self.reception)

    def _route(self, visit, **kwargs):
        return PatientRoute.objects.create(visit=visit, department=self.department, purpose="vitals",
                                           routed_by=self.reception, assigned_to=self.nurse, **kwargs)

    def _take_vitals(self, patient, nurse=None):
        return Vitals.objects.create(patient=patient, recorded_by=nurse or self.nurse,
                                     visit_time=timezone.now(), heart_rate=88, temperature_c="37.4",
                                     bp_systolic=128, bp_diastolic=84)

    def _today(self, client=None):
        return (client or self.nurse_client).get("/api/vitals/recorded-today/")

    def test_it_lists_the_readings_this_nurse_took_today(self):
        patient = self._patient()
        self._take_vitals(patient)

        response = self._today()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        row = response.data[0]
        self.assertEqual(row["patient_id"], patient.id)
        self.assertEqual(row["patient_name"], str(patient))
        self.assertEqual(row["patient_file_number"], patient.file_number)
        # The figures are on the row, so the nurse need not open the chart.
        labels = {r["label"]: r["value"] for r in row["readings"]}
        self.assertEqual(labels["BP"], "128/84")
        self.assertEqual(labels["HR"], "88")

    def test_another_nurses_readings_are_not_in_my_day(self):
        self._take_vitals(self._patient(), nurse=self.other_nurse)
        self.assertEqual(self._today().data, [])

    def test_yesterdays_readings_are_not_in_my_day(self):
        reading = self._take_vitals(self._patient())
        Vitals.objects.filter(pk=reading.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self._today().data, [])

    def test_the_nursing_note_is_carried_with_the_reading(self):
        patient = self._patient()
        reading = self._take_vitals(patient)
        NursingNote.objects.create(patient=patient, nurse=self.nurse, vitals=reading,
                                   complaint="Headache", observation="Alert, walked in unaided.")
        row = self._today().data[0]
        self.assertEqual(row["note"]["complaint"], "Headache")
        self.assertEqual(row["note"]["observation"], "Alert, walked in unaided.")

    def test_a_reading_with_no_note_still_lists(self):
        self._take_vitals(self._patient())
        self.assertIsNone(self._today().data[0]["note"])

    def test_it_names_the_doctor_the_patient_was_sent_to(self):
        patient = self._patient()
        visit = self._visit(patient)
        self._route(visit, status="in_progress")
        self._take_vitals(patient)

        sent = self.nurse_client.post("/api/patient-routes/send-to-doctor/",
                                      {"patient": patient.id, "doctor": self.doctor.id})
        self.assertEqual(sent.status_code, 201, sent.data)

        row = self._today().data[0]
        self.assertEqual(row["sent_to"]["doctor"], "Ada Obi")
        self.assertEqual(row["sent_to"]["status"], "queued")
        self.assertEqual(row["sent_to"]["route_id"], sent.data["id"])

    def test_a_recheck_reads_as_already_with_the_doctor(self):
        """
        A second reading taken while the patient is with a doctor used to say
        "not sent to a doctor yet" — the opposite of the truth, since the
        doctor could see it the moment it was saved.
        """
        patient = self._patient()
        visit = self._visit(patient)
        self._route(visit, status="in_progress")
        self._take_vitals(patient)
        self.nurse_client.post("/api/patient-routes/send-to-doctor/",
                               {"patient": patient.id, "doctor": self.doctor.id})
        self._take_vitals(patient)   # the re-check, after the hand-off

        rows = self._today().data
        self.assertEqual(len(rows), 2)
        recheck, original = rows[0], rows[1]

        self.assertIsNone(recheck["sent_to"])
        self.assertIsNotNone(recheck["with_doctor"], "the re-check reads as unsent")
        self.assertEqual(recheck["with_doctor"]["doctor"], "Ada Obi")

        # The reading that caused the hand-off still reports it as one.
        self.assertEqual(original["sent_to"]["doctor"], "Ada Obi")

    def test_a_cancelled_consultation_does_not_count_as_holding_the_patient(self):
        patient = self._patient()
        visit = self._visit(patient)
        PatientRoute.objects.create(visit=visit, department=self.department, purpose="consultation",
                                    assigned_to=self.doctor, routed_by=self.reception, status="cancelled")
        self._take_vitals(patient)
        row = self._today().data[0]
        self.assertIsNone(row["sent_to"])
        self.assertIsNone(row["with_doctor"])

    def test_a_patient_not_sent_on_says_so(self):
        patient = self._patient()
        self._route(self._visit(patient))
        self._take_vitals(patient)
        row = self._today().data[0]
        self.assertIsNone(row["sent_to"])
        self.assertIsNone(row["with_doctor"])

    def test_a_consultation_raised_before_the_reading_is_not_claimed_as_the_onward_one(self):
        """Yesterday's consultation must not be reported as where today's
        reading sent the patient."""
        patient = self._patient()
        visit = self._visit(patient)
        old = PatientRoute.objects.create(visit=visit, department=self.department, purpose="consultation",
                                          assigned_to=self.doctor, routed_by=self.reception, status="completed")
        PatientRoute.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))
        self._take_vitals(patient)
        self.assertIsNone(self._today().data[0]["sent_to"])

    def test_each_patients_row_names_their_own_doctor(self):
        for name in ("Ada", "Bola"):
            patient = self._patient(first=name)
            self._route(self._visit(patient))
            self._take_vitals(patient)
            self.nurse_client.post("/api/patient-routes/send-to-doctor/",
                                   {"patient": patient.id, "doctor": self.doctor.id})

        rows = self._today().data
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["patient_name"] for r in rows}, {str(p) for p in Patient.objects.all()})
        for row in rows:
            self.assertEqual(row["sent_to"]["doctor"], "Ada Obi")


class NurseToDoctorChainTests(TestCase):
    """What the doctor can actually read once nursing sends the patient."""

    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.stranger = User.objects.create_user(username="doctor2", password="test", role="doctor")
        self.department = Department.objects.create(code="general-medicine", name="General Medicine")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        PatientRoute.objects.create(visit=self.visit, department=self.department, purpose="vitals",
                                    assigned_to=self.nurse, routed_by=self.reception, status="in_progress")

        self.nurse_client = APIClient(); self.nurse_client.force_authenticate(self.nurse)
        self.doctor_client = APIClient(); self.doctor_client.force_authenticate(self.doctor)

    def _work_the_patient(self):
        reading = self.nurse_client.post("/api/vitals/", {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(),
            "temperature_c": "38.2", "heart_rate": 96, "bp_systolic": 130, "bp_diastolic": 85,
            "bp_position": "", "bp_extremity": "", "glucose_time_of_day": "",
        }, format="json")
        self.assertEqual(reading.status_code, 201, reading.data)
        note = self.nurse_client.post("/api/nursing-notes/", {
            "patient": self.patient.id, "complaint": "Fever since Tuesday",
            "observation": "Febrile, alert.", "vitals": reading.data["id"],
        }, format="json")
        self.assertEqual(note.status_code, 201, note.data)
        return reading.data["id"]

    def test_before_the_hand_off_the_doctor_cannot_reach_the_patient(self):
        self._work_the_patient()
        self.assertEqual(self.doctor_client.get(f"/api/patients/{self.patient.id}/overview/").status_code, 404)

    def test_after_the_hand_off_the_doctor_reads_the_vitals_and_the_note(self):
        self._work_the_patient()
        sent = self.nurse_client.post("/api/patient-routes/send-to-doctor/",
                                      {"patient": self.patient.id, "doctor": self.doctor.id,
                                       "notes": "Please see — febrile"})
        self.assertEqual(sent.status_code, 201, sent.data)

        overview = self.doctor_client.get(f"/api/patients/{self.patient.id}/overview/")
        self.assertEqual(overview.status_code, 200)

        latest = overview.data["latest_vitals"]
        self.assertIsNotNone(latest, "the doctor opened the chart with no vitals on it")
        readings = {r["label"]: str(r["value"]) for r in latest["readings"]}
        self.assertEqual(readings["Blood pressure"], "130/85")
        self.assertEqual(readings["Heart rate"], "96")

        notes = overview.data["nursing_notes"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["complaint"], "Fever since Tuesday")
        self.assertEqual(notes[0]["nurse"], self.nurse.username)

    def test_the_hand_off_does_not_open_the_chart_to_every_doctor(self):
        self._work_the_patient()
        self.nurse_client.post("/api/patient-routes/send-to-doctor/",
                               {"patient": self.patient.id, "doctor": self.doctor.id})
        stranger = APIClient(); stranger.force_authenticate(self.stranger)
        self.assertEqual(stranger.get(f"/api/patients/{self.patient.id}/overview/").status_code, 404)

    def test_the_doctor_also_reads_the_vitals_endpoint_directly(self):
        """The Vitals tab on the chart goes to /vitals/, not the overview."""
        self._work_the_patient()
        self.nurse_client.post("/api/patient-routes/send-to-doctor/",
                               {"patient": self.patient.id, "doctor": self.doctor.id})
        response = self.doctor_client.get("/api/vitals/", {"patient": self.patient.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["heart_rate"], 96)
