"""
The doctor's hand-off: lab, ultrasound, eye clinic, procedure.

Mirrors nursing's send-to-doctor, with one difference that matters — the
consultation stays open, because a referral is work done during it and the
patient comes back with a result.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Notification
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit, PatientRoute


class ReferralTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor",
                                               first_name="Ada", last_name="Obi")
        self.lab = User.objects.create_user(username="lab", password="test", role="laboratory")
        self.other_lab = User.objects.create_user(username="lab2", password="test", role="laboratory")
        self.radiology = User.objects.create_user(username="radio", password="test", role="radiology")
        self.optometrist = User.objects.create_user(username="opto", password="test", role="optometrist")
        self.eye_doctor = User.objects.create_user(username="eyedoc", password="test", role="ophthalmologist")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")

        self.department = Department.objects.create(code="general-medicine", name="General Medicine")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)
        self.consultation = PatientRoute.objects.create(
            visit=self.visit, department=self.department, purpose="consultation",
            assigned_to=self.doctor, routed_by=self.reception, status="in_progress")

        self.client = APIClient(); self.client.force_authenticate(self.doctor)

    def _refer(self, purpose="laboratory", **extra):
        return self.client.post("/api/patient-routes/refer/", {
            "patient": self.patient.id, "purpose": purpose, **extra,
        }, format="json")

    def _queue(self, user):
        client = APIClient(); client.force_authenticate(user)
        return [r["id"] for r in client.get("/api/patient-routes/").data["results"]]

    def test_a_lab_referral_reaches_every_lab_scientist(self):
        response = self._refer("laboratory", notes="FBC and malaria parasite")
        self.assertEqual(response.status_code, 201, response.data)
        route_id = response.data["id"]

        self.assertIn(route_id, self._queue(self.lab))
        self.assertIn(route_id, self._queue(self.other_lab))
        for scientist in (self.lab, self.other_lab):
            note = Notification.objects.filter(recipient=scientist, category="routing").first()
            self.assertIsNotNone(note, scientist.username)
            self.assertIn("Laboratory requested", note.title)
            self.assertIn("FBC and malaria", note.message)
            self.assertIn("Ada Obi", note.message)
            # The unit's own station, not the generic queue — same reason a
            # nurse is sent to /vitals.
            self.assertEqual(note.action_url, "/laboratory")

    def test_an_ultrasound_referral_goes_to_radiology_not_the_lab(self):
        route_id = self._refer("ultrasound").data["id"]
        self.assertIn(route_id, self._queue(self.radiology))
        self.assertEqual(self._queue(self.lab), [])

    def test_an_eye_referral_reaches_both_eye_roles(self):
        """Sending it to one role only strands the patient when they are off."""
        route_id = self._refer("eye").data["id"]
        self.assertIn(route_id, self._queue(self.optometrist))
        self.assertIn(route_id, self._queue(self.eye_doctor))

    def test_the_consultation_stays_open(self):
        self._refer("laboratory")
        self.consultation.refresh_from_db()
        self.assertEqual(self.consultation.status, "in_progress")
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.attending_doctor, self.doctor)

    def test_the_unit_can_start_and_finish_the_work(self):
        route_id = self._refer("laboratory").data["id"]
        lab_client = APIClient(); lab_client.force_authenticate(self.lab)

        accepted = lab_client.post(f"/api/patient-routes/{route_id}/accept/")
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data["assigned_to"], self.lab.id)
        # Claimed, so it leaves the other scientist's queue.
        self.assertEqual(self._queue(self.other_lab), [])

        done = lab_client.post(f"/api/patient-routes/{route_id}/complete/")
        self.assertEqual(done.status_code, 200, done.data)
        self.assertEqual(PatientRoute.objects.get(pk=route_id).status, "completed")

    def test_a_named_person_gets_it_alone(self):
        route_id = self._refer("laboratory", assigned_to=self.lab.id).data["id"]
        self.assertIn(route_id, self._queue(self.lab))
        self.assertEqual(self._queue(self.other_lab), [])

    def test_it_will_not_name_somebody_who_cannot_do_the_work(self):
        response = self._refer("laboratory", assigned_to=self.radiology.id)
        self.assertEqual(response.status_code, 400)
        self.assertIn("assigned_to", response.data)

    def test_the_same_referral_twice_is_refused(self):
        self._refer("laboratory")
        again = self._refer("laboratory")
        self.assertEqual(again.status_code, 409)
        self.assertIn("already waiting", again.data["detail"])
        self.assertEqual(PatientRoute.objects.filter(purpose="laboratory").count(), 1)

    def test_two_different_referrals_can_run_at_once(self):
        self.assertEqual(self._refer("laboratory").status_code, 201)
        self.assertEqual(self._refer("ultrasound").status_code, 201)

    def test_a_doctor_cannot_refer_a_consultation(self):
        """Handing a patient to another doctor is nursing's job — a doctor
        doing it sideways would quietly reassign the chart."""
        response = self._refer("consultation")
        self.assertEqual(response.status_code, 400)
        self.assertIn("purpose", response.data)

    def test_a_patient_with_no_open_visit_cannot_be_referred(self):
        self.visit.status = "completed"
        self.visit.save(update_fields=["status"])
        response = self._refer("laboratory")
        self.assertEqual(response.status_code, 400)
        self.assertIn("no open visit", response.data["detail"])

    def test_only_a_doctor_refers(self):
        for user in (self.nurse, self.lab, self.reception):
            client = APIClient(); client.force_authenticate(user)
            response = client.post("/api/patient-routes/refer/", {
                "patient": self.patient.id, "purpose": "laboratory",
            }, format="json")
            self.assertEqual(response.status_code, 403, user.role)
        self.assertEqual(PatientRoute.objects.filter(purpose="laboratory").count(), 0)

    def test_referring_raises_no_charge(self):
        """Money is created by the counter, from the Billing Catalog."""
        from apps.billing.models import Charge
        self._refer("laboratory")
        self.assertFalse(Charge.objects.exists())


class BillingCatalogAccessTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="test", role="admin")
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier")
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")

    def _client(self, user):
        client = APIClient(); client.force_authenticate(user)
        return client

    def _price(self, client, category="laboratory", name="Full blood count"):
        return client.post("/api/billing-items/", {
            "category": category, "name": name, "price": "3500", "is_active": True,
        }, format="json")

    def test_the_new_departments_are_billable_categories(self):
        client = self._client(self.cashier)
        for category in ("laboratory", "ultrasound", "eye", "procedure"):
            response = self._price(client, category=category, name=f"{category} service")
            self.assertEqual(response.status_code, 201, response.data)

    def test_a_cashier_can_price_the_catalogue(self):
        self.assertEqual(self._price(self._client(self.cashier)).status_code, 201)

    def test_an_admin_can_too(self):
        self.assertEqual(self._price(self._client(self.admin), name="Urinalysis").status_code, 201)

    def test_reception_reads_the_price_list_but_does_not_set_it(self):
        self._price(self._client(self.cashier))
        client = self._client(self.reception)
        self.assertEqual(client.get("/api/billing-items/").status_code, 200)
        self.assertEqual(self._price(client, name="Made up test").status_code, 403)

    def test_a_priced_service_can_then_be_billed(self):
        self._price(self._client(self.cashier))
        patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                         created_by=self.reception)
        item = self._client(self.reception).get("/api/billing-items/", {"category": "laboratory"}).data["results"][0]
        charged = self._client(self.reception).post("/api/charges/", {
            "patient": patient.id, "description": item["name"], "amount": item["price"],
        }, format="json")
        self.assertEqual(charged.status_code, 201, charged.data)
