"""
Who hears about a referral, and who hears about the result.

The rule both halves share: a notification goes to the people who can act on
it and to nobody else. A bell that fills up with other people's work — or
with your own actions echoed back — is a bell people stop reading, and then
a real result goes unseen.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Notification
from apps.departments.models import Department
from apps.laboratory.models import LabOrder, LabParameter
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


class ReferralNotificationTests(TestCase):
    def setUp(self):
        self.dera = User.objects.create_user(username="dera", password="t", role="doctor")
        self.femi = User.objects.create_user(username="femi", password="t", role="doctor")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.lab2 = User.objects.create_user(username="lab2", password="t", role="laboratory")
        self.nurse = User.objects.create_user(username="nurse", password="t", role="nurse")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Amaka", last_name="Nwosu", sex="F",
                                              created_by=self.reception)
        self.department = Department.objects.create(code="lab", name="Laboratory")
        # Dera is the doctor holding this patient.
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.dera)
        self.client = APIClient()

    def _recipients(self):
        return {n.recipient.username for n in Notification.objects.all()}

    def _refer(self, purpose, **extra):
        self.client.force_authenticate(self.dera)
        return self.client.post("/api/patient-routes/refer/", {
            "patient": self.patient.pk, "purpose": purpose, "notes": "please", **extra,
        }, format="json")

    def test_a_lab_referral_reaches_the_laboratory_and_nobody_else(self):
        self._refer("laboratory")
        self.assertEqual(self._recipients(), {"lab", "lab2"})

    def test_the_doctor_who_referred_is_not_told_about_her_own_referral(self):
        self._refer("laboratory")
        self.assertNotIn("dera", self._recipients())

    def test_another_doctor_hears_nothing_about_a_lab_referral(self):
        self._refer("laboratory")
        self.assertNotIn("femi", self._recipients())

    def test_a_procedure_referral_does_not_go_to_every_doctor_in_the_hospital(self):
        """
        This was the bug: `procedure` is worked by "doctor", and unassigned
        work used to broadcast to the whole role — so referring one patient
        for a dressing pinged every doctor on the system.
        """
        self._refer("procedure")
        self.assertNotIn("femi", self._recipients())
        self.assertNotIn("dera", self._recipients())

    def test_a_procedure_named_to_a_doctor_reaches_that_doctor_only(self):
        self._refer("procedure", assigned_to=self.femi.pk)
        self.assertEqual(self._recipients(), {"femi"})

    def test_a_lab_referral_named_to_one_scientist_does_not_wake_the_others(self):
        self._refer("laboratory", assigned_to=self.lab.pk)
        self.assertEqual(self._recipients(), {"lab"})

    def test_a_vitals_route_still_reaches_every_nurse(self):
        """
        Pooled roles keep the broadcast — that is how a vitals request finds
        whoever is on tonight, and it is the behaviour rule 14 asks for.
        """
        self.client.force_authenticate(self.reception)
        self.client.post("/api/patient-routes/", {
            "visit": self.visit.pk, "department": self.department.pk,
            "purpose": "vitals", "priority": "routine", "notes": "obs",
        }, format="json")
        self.assertEqual(self._recipients(), {"nurse"})

    def test_the_referrer_is_told_when_nobody_was_notified(self):
        """
        Silence is deliberate for doctor-work nobody is holding — but it must
        not be a surprise, or a patient sits in a queue nobody is watching.
        """
        response = self._refer("procedure")
        self.assertEqual(response.data["notified"], [])
        self.assertIn("nobody has been notified", response.data["notice"])

    def test_a_referral_says_who_it_reached(self):
        response = self._refer("laboratory")
        self.assertEqual(sorted(response.data["notified"]), ["lab", "lab2"])
        self.assertNotIn("notice", response.data)

    def test_reception_routing_to_the_lab_reaches_the_lab_only(self):
        self.client.force_authenticate(self.reception)
        self.client.post("/api/patient-routes/", {
            "visit": self.visit.pk, "department": self.department.pk,
            "purpose": "laboratory", "priority": "routine", "notes": "MP",
        }, format="json")
        self.assertEqual(self._recipients(), {"lab", "lab2"})


class ResultNotificationTests(TestCase):
    def setUp(self):
        self.dera = User.objects.create_user(username="dera", password="t", role="doctor")
        self.femi = User.objects.create_user(username="femi", password="t", role="doctor")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Amaka", last_name="Nwosu", sex="F",
                                              created_by=self.reception)
        self.department = Department.objects.create(code="lab", name="Laboratory")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.dera)
        self.route = PatientRoute.objects.create(
            visit=self.visit, department=self.department, purpose="laboratory",
            routed_by=self.dera, notes="FBC please")
        self.client = APIClient()
        self.client.force_authenticate(self.lab)
        order = self.client.post("/api/lab-orders/for-route/", {"route": self.route.pk},
                                 format="json").data
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["fbc"]}, format="json")
        self.order = LabOrder.objects.get(pk=order["id"])
        self.item = self.order.items.get()
        self.hb = LabParameter.objects.get(test__code="fbc", code="hb")
        Notification.objects.all().delete()

    def _submit(self, **extra):
        return self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk, "submit": True,
            "values": [{"parameter": self.hb.pk, "value": "9.2"}], **extra,
        }, format="json")

    def _clinical(self):
        return {n.recipient.username for n in Notification.objects.filter(category="clinical")}

    def test_the_result_goes_to_the_doctor_who_referred(self):
        self._submit()
        self.assertEqual(self._clinical(), {"dera"})

    def test_no_other_doctor_is_told(self):
        self._submit()
        self.assertNotIn("femi", self._clinical())

    def test_submitting_then_verifying_does_not_tell_the_doctor_twice(self):
        self._submit()
        self.client.post(f"/api/lab-orders/{self.order.pk}/verify/", {}, format="json")
        self.assertEqual(Notification.objects.filter(category="clinical").count(), 1)

    def test_the_bench_can_send_the_result_to_a_covering_doctor(self):
        """When the doctor who referred is off, somebody else is holding the patient."""
        response = self._submit(notify_doctor=self.femi.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._clinical(), {"femi"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.report_to, self.femi)
        self.assertEqual(response.data["report_to_name"], "femi")

    def test_redirecting_at_verification_works_too(self):
        self._submit()
        self.client.post(f"/api/lab-orders/{self.order.pk}/verify/",
                         {"notify_doctor": self.femi.pk}, format="json")
        self.order.refresh_from_db()
        self.assertEqual(self.order.report_to, self.femi)

    def test_a_result_cannot_be_addressed_to_someone_who_is_not_a_doctor(self):
        response = self._submit(notify_doctor=self.lab.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn("notify_doctor", response.data)

    def test_the_result_notification_opens_the_chart_s_lab_tab(self):
        """
        The doctor came to read the result, not to hunt for it on the front
        page of the chart.
        """
        self._submit()
        note = Notification.objects.get(category="clinical")
        self.assertEqual(note.action_url, f"/patients/{self.patient.pk}/lab")

    def test_the_default_recipient_is_named_before_anything_is_sent(self):
        """The bench should be able to see where it is going without guessing."""
        data = self.client.get(f"/api/lab-orders/{self.order.pk}/").data
        self.assertEqual(data["report_to_name"], "dera")
