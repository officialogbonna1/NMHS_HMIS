"""
The doctor's dashboard cards.

A card is a way in, not just a number: whatever it counts, clicking it has
to open the page showing that thing. "My queue" used to land on the patient
list, which left a doctor with somebody waiting no route through from here.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class DoctorDashboardCardTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.department = Department.objects.create(code="general-medicine", name="General Medicine")
        self.client = APIClient(); self.client.force_authenticate(self.doctor)

    def _cards(self, client=None):
        response = (client or self.client).get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        return {card["key"]: card for card in response.data["cards"] if "key" in card}

    def _patient(self, first="Jane"):
        return Patient.objects.create(first_name=first, last_name="Doe", sex="F", created_by=self.reception)

    def _queue_for_me(self, patient, status="queued"):
        visit = Visit.objects.create(patient=patient, opened_by=self.reception)
        return PatientRoute.objects.create(visit=visit, department=self.department, purpose="consultation",
                                           assigned_to=self.doctor, routed_by=self.reception, status=status)

    def test_my_patients_comes_before_my_queue(self):
        order = [card.get("key") for card in self.client.get("/api/dashboard/").data["cards"]]
        self.assertLess(order.index("my_patients"), order.index("my_queue"))

    def test_each_card_opens_the_page_it_counts(self):
        cards = self._cards()
        self.assertEqual(cards["my_patients"]["href"], "/patients")
        self.assertEqual(cards["my_queue"]["href"], "/queue")
        self.assertEqual(cards["unread"]["href"], "/notifications")

    def test_my_patients_counts_the_same_list_the_patients_page_shows(self):
        for name in ("Jane", "Bola", "Chidi"):
            self._queue_for_me(self._patient(name))
        # Somebody else's patient must not be counted.
        Patient.objects.create(first_name="Not", last_name="Mine", sex="M", created_by=self.reception)

        listed = self.client.get("/api/patients/", {"page_size": 200}).data["count"]
        self.assertEqual(self._cards()["my_patients"]["value"], listed)
        self.assertEqual(listed, 3)

    def test_my_queue_counts_only_work_still_waiting(self):
        self._queue_for_me(self._patient("Jane"))
        self._queue_for_me(self._patient("Bola"), status="in_progress")
        self._queue_for_me(self._patient("Chidi"), status="completed")
        self.assertEqual(self._cards()["my_queue"]["value"], 2)

    def test_a_doctor_with_nothing_waiting_still_gets_the_cards(self):
        cards = self._cards()
        self.assertEqual(cards["my_queue"]["value"], 0)
        self.assertEqual(cards["my_patients"]["value"], 0)

    def test_a_role_with_no_queue_page_is_not_sent_to_one(self):
        """`/queue` is routable for reception, doctors and nurses only."""
        pharmacist = User.objects.create_user(username="pharmacist", password="test", role="pharmacist")
        client = APIClient(); client.force_authenticate(pharmacist)
        self.assertEqual(self._cards(client)["my_queue"]["href"], "/patients")

    def test_the_nurse_keeps_their_own_card_set(self):
        nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        client = APIClient(); client.force_authenticate(nurse)
        keys = set(self._cards(client))
        self.assertIn("vitals_waiting", keys)
        self.assertNotIn("my_queue", keys)
