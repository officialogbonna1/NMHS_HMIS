"""
The payload VitalsEntryForm actually posts, pinned.

The form sends every field on every save — a reading with only a temperature
still carries the blood-pressure and glucose boxes the nurse left alone. What
"left alone" serializes to differs by field type, and getting it wrong is a
400 with nothing on the form to explain it.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.models import Vitals, NursingNote
from apps.patients.models import Patient


class VitalsEntryPayloadTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.client = APIClient(); self.client.force_authenticate(self.nurse)

    def _post(self, **overrides):
        """A temperature and a pulse, with every other box untouched."""
        payload = {
            "patient": self.patient.id,
            "visit_time": timezone.now().isoformat(),
            "temperature_c": "38.2", "heart_rate": 96,
            "respiratory_rate": None, "sao2": None,
            "bp_systolic": None, "bp_diastolic": None,
            "height_cm": None, "weight_kg": None,
            "glucose_level": None, "glucose_fasting": None,
            # Blank text on the model, so blank — not null — is what it takes.
            "bp_position": "", "bp_extremity": "", "glucose_time_of_day": "",
        }
        payload.update(overrides)
        return self.client.post("/api/vitals/", payload, format="json")

    def test_the_untouched_boxes_do_not_reject_the_reading(self):
        response = self._post()
        self.assertEqual(response.status_code, 201, response.data)
        reading = Vitals.objects.get(pk=response.data["id"])
        self.assertEqual(reading.heart_rate, 96)
        self.assertIsNone(reading.bp_systolic)
        self.assertEqual(reading.bp_position, "")

    def test_a_full_reading_saves(self):
        response = self._post(bp_systolic=128, bp_diastolic=84, bp_position="Sitting",
                              bp_extremity="Left arm", glucose_level="5.4",
                              glucose_time_of_day="Before meal", glucose_fasting=True)
        self.assertEqual(response.status_code, 201, response.data)

    def test_nulling_a_text_field_is_still_refused_by_name(self):
        """Not a rule we want — a guard that the failure is legible if the
        form regresses. The error names the field, which is what the entry
        form now puts on screen."""
        response = self._post(bp_position=None)
        self.assertEqual(response.status_code, 400)
        self.assertIn("bp_position", response.data)

    def test_the_reading_locks_on_save(self):
        reading = Vitals.objects.get(pk=self._post().data["id"])
        self.assertTrue(reading.is_locked)


class NursingNotePayloadTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.client = APIClient(); self.client.force_authenticate(self.nurse)

    def test_a_note_written_before_the_reading_saves(self):
        """The form omits `vitals` when there is no reading yet."""
        response = self.client.post("/api/nursing-notes/", {
            "patient": self.patient.id, "complaint": "", "observation": "Alert, walked in unaided.",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(NursingNote.objects.get(pk=response.data["id"]).vitals)

    def test_a_note_hangs_off_the_reading_it_was_taken_with(self):
        reading = Vitals.objects.create(patient=self.patient, recorded_by=self.nurse,
                                        visit_time=timezone.now(), heart_rate=90)
        response = self.client.post("/api/nursing-notes/", {
            "patient": self.patient.id, "complaint": "Headache",
            "observation": "Febrile.", "vitals": reading.id,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(NursingNote.objects.get(pk=response.data["id"]).vitals, reading)

    def test_an_observation_is_required(self):
        response = self.client.post("/api/nursing-notes/", {
            "patient": self.patient.id, "observation": "",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("observation", response.data)
