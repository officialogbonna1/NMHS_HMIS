"""
How old a patient is, including the ones measured in days.

"0 years" is how a three-day-old disappears from the record, and on a
maternity ward that is most of the register — so age carries a unit, and a
birthdate is rendered in the largest unit that still says something.
"""
from datetime import date, timedelta

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.patients.models import Patient


class AgeDisplayTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")

    def _born(self, days_ago):
        return Patient.objects.create(first_name="Baby", last_name="Doe", sex="F",
                                      birthdate=date.today() - timedelta(days=days_ago),
                                      created_by=self.reception)

    def test_a_newborn_is_counted_in_days(self):
        self.assertEqual(self._born(0).age_display, "0 days")
        self.assertEqual(self._born(1).age_display, "1 day")
        self.assertEqual(self._born(9).age_display, "9 days")

    def test_a_few_weeks_old_is_counted_in_weeks(self):
        self.assertEqual(self._born(21).age_display, "3 weeks")

    def test_an_infant_is_counted_in_months(self):
        self.assertEqual(self._born(210).age_display, "7 months")

    def test_a_child_and_upwards_is_counted_in_years(self):
        self.assertEqual(self._born(365 * 5 + 2).age_display, "5 yrs")
        self.assertEqual(self._born(365 * 42 + 10).age_display, "42 yrs")

    def test_a_stated_age_keeps_the_unit_it_was_given_in(self):
        patient = Patient.objects.create(first_name="Baby", last_name="Roe", sex="M",
                                         age_value=5, age_unit="days", created_by=self.reception)
        self.assertEqual(patient.age_display, "5 days")

        patient.age_value, patient.age_unit = 1, "months"
        self.assertEqual(patient.age_display, "1 month")

        patient.age_value, patient.age_unit = 30, "years"
        self.assertEqual(patient.age_display, "30 yrs")

    def test_a_patient_with_neither_has_no_age(self):
        patient = Patient.objects.create(first_name="Unknown", last_name="Doe", sex="F",
                                         created_by=self.reception)
        self.assertIsNone(patient.age_display)

    def test_the_birthdate_wins_over_a_stated_age(self):
        """The date is the exact fact; the stated age was only ever a guess."""
        patient = Patient.objects.create(first_name="Baby", last_name="Doe", sex="F",
                                         birthdate=date.today() - timedelta(days=3),
                                         age_value=1, age_unit="years",
                                         created_by=self.reception)
        self.assertEqual(patient.age_display, "3 days")


class AgeApiTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.client = APIClient(); self.client.force_authenticate(self.reception)

    def test_a_baby_can_be_registered_in_days(self):
        response = self.client.post("/api/patients/", {
            "first_name": "Baby", "last_name": "Nwosu", "sex": "F",
            "age_value": 3, "age_unit": "days",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["age_value"], 3)
        self.assertEqual(response.data["age_unit"], "days")
        self.assertEqual(response.data["age_display"], "3 days")

    def test_registering_with_a_birthdate_returns_the_right_unit(self):
        response = self.client.post("/api/patients/", {
            "first_name": "Baby", "last_name": "Eze", "sex": "M",
            "birthdate": (date.today() - timedelta(days=2)).isoformat(),
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["age_display"], "2 days")

    def test_the_age_unit_defaults_to_years(self):
        response = self.client.post("/api/patients/", {
            "first_name": "Adult", "last_name": "Doe", "sex": "F", "age_value": 42,
        }, format="json")
        self.assertEqual(response.data["age_display"], "42 yrs")

    def test_the_chart_shows_the_baby_in_days(self):
        doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        created = self.client.post("/api/patients/", {
            "first_name": "Baby", "last_name": "Nwosu", "sex": "F",
            "birthdate": (date.today() - timedelta(days=4)).isoformat(),
        }, format="json")

        from apps.workflow.models import Visit
        Visit.objects.create(patient=Patient.objects.get(pk=created.data["id"]),
                             opened_by=self.reception, attending_doctor=doctor)

        client = APIClient(); client.force_authenticate(doctor)
        overview = client.get(f"/api/patients/{created.data['id']}/overview/")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.data["patient"]["age_display"], "4 days")
