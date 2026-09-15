"""
The Eye Doctor Desk is the existing desk: the dashboard, the /eye station,
the chart, referrals and billing — reached by the eye doctor for their own
patients, with nothing eye-specific in the money or the routing.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge
from apps.clinical.models import ConsultationNote
from apps.core.models import Notification
from apps.departments.models import Department
from apps.laboratory.models import LabOrder, LabOrderTest, LabTest
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class EyeDoctorDeskTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.eye_doctor = User.objects.create_user(username="eye", password="t", role="ophthalmologist")
        self.other_eye = User.objects.create_user(username="eye2", password="t", role="ophthalmologist")
        self.optometrist = User.objects.create_user(username="opto", password="t", role="optometrist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.department = Department.objects.create(code="eye-unit-test", name="Eye Unit (test)")

        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F", created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        self.route = PatientRoute.objects.create(visit=self.visit, department=self.department, purpose="eye",
                                                 assigned_to=self.eye_doctor, routed_by=self.reception,
                                                 status="in_progress")
        self.stranger = Patient.objects.create(first_name="Bo", last_name="Eze", sex="M", created_by=self.reception)
        self.stranger_visit = Visit.objects.create(patient=self.stranger, opened_by=self.reception,
                                                   attending_doctor=self.doctor)

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def dashboard(self, user):
        response = self.as_(user).get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        return response.data

    # --- the desk ---------------------------------------------------------------

    def test_the_desk_counts_their_own_patients_referrals_and_consultations(self):
        ConsultationNote.objects.create(patient=self.patient, doctor=self.eye_doctor,
                                        visit_time=timezone.now(), reason_for_visit="Review")
        cards = {c["key"]: c for c in self.dashboard(self.eye_doctor)["cards"] if "key" in c}
        self.assertEqual(cards["my_patients"]["value"], 1)
        self.assertEqual(cards["eye_consultations_today"]["value"], 1)
        self.assertEqual(cards["referrals_in_progress"]["value"], 1)
        self.assertEqual(cards["my_queue"]["href"], "/eye")
        # Nobody else's desk changed.
        self.assertNotIn("eye_consultations_today",
                         {c.get("key") for c in self.dashboard(self.doctor)["cards"]})
        self.assertNotIn("eye_consultations_today",
                         {c.get("key") for c in self.dashboard(self.optometrist)["cards"]})

    def test_a_claimed_referral_opens_the_chart_and_an_unclaimed_one_the_station(self):
        waiting = PatientRoute.objects.create(visit=self.stranger_visit, department=self.department,
                                              purpose="eye", routed_by=self.reception)
        tasks = {task["id"]: task["href"] for task in self.dashboard(self.eye_doctor)["tasks"]}
        self.assertEqual(tasks[self.route.id], f"/patients/{self.patient.uuid}")
        self.assertEqual(tasks[waiting.id], "/eye")

    def test_reception_routes_an_eye_patient_to_the_shared_queue_or_to_a_named_eye_doctor(self):
        front_desk = self.as_(self.reception)
        pooled = front_desk.post("/api/patient-routes/", {
            "visit": self.stranger_visit.id, "department": self.department.id, "purpose": "eye",
            "notes": "Painful red eye"}, format="json")
        self.assertEqual(pooled.status_code, 201, pooled.data)
        self.assertEqual(Notification.objects.get(recipient=self.eye_doctor).action_url, "/eye")

        Notification.objects.all().delete()
        named = front_desk.post("/api/patient-routes/", {
            "visit": self.visit.id, "department": self.department.id, "purpose": "eye",
            "assigned_to": self.other_eye.id}, format="json")
        self.assertEqual(named.status_code, 201, named.data)
        self.assertEqual(Notification.objects.get(recipient=self.other_eye).action_url,
                         f"/patients/{self.patient.uuid}")
        self.assertFalse(Notification.objects.filter(recipient=self.eye_doctor).exists())

    # --- referrals and money ------------------------------------------------------

    def test_a_laboratory_referral_is_billed_by_the_existing_laboratory_path(self):
        api = self.as_(self.eye_doctor)
        referral = api.post("/api/patient-routes/refer/", {
            "patient": self.patient.id, "purpose": "laboratory",
            "notes": "Fasting glucose before cataract surgery"}, format="json")
        self.assertEqual(referral.status_code, 201, referral.data)
        self.assertTrue(Notification.objects.filter(recipient=self.lab, category="routing").exists())

        order = api.post("/api/lab-orders/for-route/", {"route": referral.data["id"]}, format="json")
        self.assertEqual(order.status_code, 200, order.data)
        test = LabTest.objects.filter(is_active=True, price__gt=0).first()
        added = api.post(f"/api/lab-orders/{order.data['id']}/add-tests/", {"tests": [test.id]}, format="json")
        self.assertEqual(added.status_code, 200, added.data)

        line = LabOrderTest.objects.get(order_id=order.data["id"])
        charge = line.charge
        self.assertEqual((charge.patient, charge.source_type, charge.amount, charge.department.code),
                         (self.patient, "lab_test", line.unit_price, "laboratory"))
        self.assertEqual(LabOrder.objects.get(pk=order.data["id"]).requested_by, self.eye_doctor)
        # The eye doctor reads their own patient's order back on the chart.
        self.assertEqual(len(rows(api.get("/api/lab-orders/", {"patient": self.patient.id, "detail": "1"}))), 1)

    def test_no_referral_or_order_for_a_patient_who_is_not_theirs(self):
        api = self.as_(self.eye_doctor)
        refused = api.post("/api/patient-routes/refer/", {"patient": self.stranger.id,
                                                          "purpose": "laboratory"}, format="json")
        self.assertEqual((refused.status_code, refused.data["code"]), (403, "not_your_patient"))
        self.assertFalse(self.stranger_visit.routes.exists())

        doctors_referral = self.as_(self.doctor).post("/api/patient-routes/refer/", {
            "patient": self.stranger.id, "purpose": "laboratory"}, format="json")
        self.assertEqual(doctors_referral.status_code, 201, doctors_referral.data)
        self.assertEqual(api.post("/api/lab-orders/for-route/", {"route": doctors_referral.data["id"]},
                                  format="json").status_code, 403)
        self.assertFalse(LabOrder.objects.exists())
        self.assertFalse(Charge.objects.filter(patient=self.stranger).exists())

    def test_the_optometrist_still_does_not_refer_or_prescribe(self):
        api = self.as_(self.optometrist)
        self.assertEqual(api.post("/api/patient-routes/refer/", {"patient": self.patient.id,
                                                                 "purpose": "laboratory"}, format="json").status_code, 403)
        self.assertEqual(api.post("/api/prescriptions/bulk/", {"patient": self.patient.id, "lines": []},
                                  format="json").status_code, 403)
