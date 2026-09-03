"""
The nine health-record tiles on the doctor's overview.

Counts alone told the doctor a surgical history existed without saying what
it was. Every tile now travels with its contents, and this pins that: a tile
counted in `counts` must have a list beside it, or the chart is showing a
number the doctor cannot open.
"""
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.patients.models import (
    Patient, Allergy, Medication, MedicalCondition, MedicalDevice,
    SurgicalHistory, FamilyMedicalHistory, SocialHistory, Vaccination, MedicalTest,
)
from apps.workflow.models import Visit


class OverviewHealthRecordTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        # Attending is what puts this patient on the doctor's chart.
        Visit.objects.create(patient=self.patient, opened_by=self.reception, attending_doctor=self.doctor)

        Allergy.objects.create(patient=self.patient, name="Penicillin",
                               reactions=["Difficulty breathing", "Hives"], is_dangerous=True,
                               notes="Confirmed 2019")
        MedicalCondition.objects.create(patient=self.patient, name="Asthma",
                                        date_diagnosed=date(2015, 3, 1), notes="Exercise-induced")
        Medication.objects.create(patient=self.patient, name="Salbutamol", strength="100mcg",
                                  dose_frequency="PRN", as_needed=True)
        SurgicalHistory.objects.create(patient=self.patient, name="Appendectomy",
                                       surgery_date=date(2018, 6, 12), description="Laparoscopic")
        Vaccination.objects.create(patient=self.patient, name="Tetanus",
                                   date_administered=date(2023, 1, 9), next_due_date=date(2033, 1, 9))
        MedicalDevice.objects.create(patient=self.patient, name="Inhaler spacer", make="AeroChamber",
                                     model="Plus", device_id="AC-9931")
        FamilyMedicalHistory.objects.create(patient=self.patient, relationship="Mother",
                                            is_deceased=True, conditions=["Diabetes", "Hypertension"])
        SocialHistory.objects.create(patient=self.patient, category="Smoking", is_active=False,
                                     frequency="Daily", amount="5/day", started_year=2005)
        MedicalTest.objects.create(patient=self.patient, title="Chest X-ray", test_type="xray",
                                   test_date=date(2026, 8, 2), impressions="Clear",
                                   file=SimpleUploadedFile("xray.pdf", b"%PDF-1.4", content_type="application/pdf"))

        self.client = APIClient(); self.client.force_authenticate(self.doctor)

    def _overview(self):
        response = self.client.get(f"/api/patients/{self.patient.id}/overview/")
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_every_counted_tile_arrives_with_its_contents(self):
        data = self._overview()
        for tile, count in data["counts"].items():
            self.assertIn(tile, data, f"counts names {tile} but the payload carries no list for it")
            self.assertEqual(len(data[tile]), count, tile)

    def test_all_nine_tiles_are_counted(self):
        self.assertEqual(set(self._overview()["counts"]), {
            "allergies", "conditions", "medications", "surgeries", "vaccinations",
            "devices", "tests", "family_history", "social_history",
        })

    def test_the_allergy_carries_its_reactions_not_just_a_name(self):
        allergy = self._overview()["allergies"][0]
        self.assertEqual(allergy["name"], "Penicillin")
        self.assertEqual(allergy["reactions"], ["Difficulty breathing", "Hives"])
        self.assertTrue(allergy["is_dangerous"])
        self.assertEqual(allergy["notes"], "Confirmed 2019")

    def test_the_surgery_carries_its_date_and_description(self):
        surgery = self._overview()["surgeries"][0]
        self.assertEqual(surgery["name"], "Appendectomy")
        self.assertEqual(surgery["surgery_date"], date(2018, 6, 12))
        self.assertEqual(surgery["description"], "Laparoscopic")

    def test_the_vaccination_carries_when_it_is_next_due(self):
        vaccination = self._overview()["vaccinations"][0]
        self.assertEqual(vaccination["date_administered"], date(2023, 1, 9))
        self.assertEqual(vaccination["next_due_date"], date(2033, 1, 9))

    def test_the_device_carries_its_make_model_and_id(self):
        device = self._overview()["devices"][0]
        self.assertEqual((device["make"], device["model"], device["device_id"]),
                         ("AeroChamber", "Plus", "AC-9931"))

    def test_the_test_carries_a_readable_type_and_a_link_to_the_file(self):
        test = self._overview()["tests"][0]
        self.assertEqual(test["title"], "Chest X-ray")
        self.assertEqual(test["test_type"], "X-ray")   # the label, not the code
        self.assertEqual(test["impressions"], "Clear")
        # Root-relative, so the link resolves against the app's own origin
        # rather than the page the doctor happens to be on.
        self.assertTrue(test["file_url"].startswith("/media/"), test["file_url"])

    def test_family_history_carries_the_conditions_it_lists(self):
        family = self._overview()["family_history"][0]
        self.assertEqual(family["relationship"], "Mother")
        self.assertTrue(family["is_deceased"])
        self.assertEqual(family["conditions"], ["Diabetes", "Hypertension"])

    def test_social_history_says_whether_it_is_current(self):
        social = self._overview()["social_history"][0]
        self.assertEqual(social["category"], "Smoking")
        self.assertFalse(social["is_active"])
        self.assertEqual(social["amount"], "5/day")
        self.assertEqual(social["started_year"], 2005)

    def test_a_medication_says_whether_it_is_taken_as_needed(self):
        medication = self._overview()["medications"][0]
        self.assertEqual(medication["name"], "Salbutamol")
        self.assertTrue(medication["as_needed"])

    def test_an_empty_chart_still_answers_with_every_tile(self):
        """A patient with nothing on file must not render a broken chart."""
        blank = Patient.objects.create(first_name="John", last_name="Blank", sex="M",
                                       created_by=self.reception)
        Visit.objects.create(patient=blank, opened_by=self.reception, attending_doctor=self.doctor)
        response = self.client.get(f"/api/patients/{blank.id}/overview/")
        self.assertEqual(response.status_code, 200)
        for tile in response.data["counts"]:
            self.assertEqual(response.data[tile], [], tile)
