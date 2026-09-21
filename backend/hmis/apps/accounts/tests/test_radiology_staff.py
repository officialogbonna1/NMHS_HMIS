"""
The radiology account is an ordinary staff account.

No second user model, no special login, no role invented for it: `radiology`
was already in `accounts.Role` and already mapped to the ultrasound queue
(`workflow.views.PURPOSE_ROLE`). What these hold is that an account carrying
that role reaches its own unit's work and nothing else — the boundary the eye
doctor's tests draw for `ophthalmologist`, drawn again here.
"""
from decimal import Decimal

from django.contrib.admin.sites import site as admin_site
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.billing.models import BillingItem, Charge
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit
from apps.workflow.services import request_services


class RadiologyStaffAccount(TestCase):
    def setUp(self):
        self.sonographer = User.objects.create_user(
            username="radiology_test", password="t", role="radiology",
            first_name="Radiology", last_name="Tester", department="Radiology / Ultrasound")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.department = Department.objects.create(code="rad-staff", name="Radiology (test)")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)
        self.route = PatientRoute.objects.create(visit=self.visit, department=self.department,
                                                 purpose="ultrasound", routed_by=self.doctor,
                                                 notes="RUQ pain")
        self.exam = BillingItem.objects.create(category="ultrasound", price=Decimal("8000"),
                                               name="Abdominal Ultrasound (staff test)")
        request_services(route=self.route, items=[self.exam], author=self.doctor)

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    # --- the account itself ------------------------------------------------

    def test_it_is_the_existing_user_model_with_the_existing_role(self):
        self.assertIsInstance(self.sonographer, User)
        self.assertEqual(self.sonographer.role, Role.RADIOLOGY)
        self.assertEqual(Role.RADIOLOGY.label, "Radiology Staff")

    def test_it_is_numbered_on_the_staff_register_like_anybody_else(self):
        self.assertTrue(self.sonographer.staff_number.startswith("NMHS-S"))
        self.assertTrue(self.sonographer.is_hospital_staff)

    def test_it_is_managed_from_django_admin_through_the_one_user_admin(self):
        self.assertIn(User, admin_site._registry)
        self.assertIn("role", admin_site._registry[User].list_display)

    def test_the_hmis_staff_screens_read_the_same_row(self):
        """Same rows, both doors — the React admin's Users page reads this.
        The open directory carries a name and a role (never an email); the
        administrator's own list carries the account."""
        directory = self.as_(self.reception).get("/api/users/")
        self.assertEqual(directory.status_code, 200)
        rows = directory.data["results"] if isinstance(directory.data, dict) else directory.data
        listed = next(row for row in rows if row["id"] == self.sonographer.pk)
        self.assertEqual(listed["role"], "radiology")
        self.assertEqual(listed["staff_number"], self.sonographer.staff_number)

        admin = User.objects.create_user(username="boss", password="t", role="admin")
        managed = self.as_(admin).get("/api/users/")
        rows = managed.data["results"] if isinstance(managed.data, dict) else managed.data
        account = next(row for row in rows if row["id"] == self.sonographer.pk)
        self.assertEqual(account["username"], "radiology_test")
        self.assertEqual(account["department"], "Radiology / Ultrasound")

    def test_the_account_authenticates_the_ordinary_way(self):
        """No bypass: the existing token endpoint, the existing password."""
        self.sonographer.set_password("a-real-password")
        self.sonographer.save(update_fields=["password"])
        answer = APIClient().post("/api/auth/login/",
                                  {"username": "radiology_test", "password": "a-real-password"},
                                  format="json")
        self.assertEqual(answer.status_code, 200, answer.data)
        self.assertIn("token", answer.data)

    # --- what it can do ----------------------------------------------------

    def test_it_sees_the_ultrasound_queue_and_what_was_asked_for(self):
        queue = self.as_(self.sonographer).get("/api/patient-routes/")
        rows = queue.data["results"] if isinstance(queue.data, dict) else queue.data
        entry = next(r for r in rows if r["id"] == self.route.pk)
        self.assertEqual(entry["patient_name"], self.patient.display_name)
        self.assertEqual([s["name"] for s in entry["services"]], [self.exam.name])
        self.assertEqual(entry["services"][0]["billing"]["status"], "unpaid")

    def test_it_works_the_request_through_to_a_released_report(self):
        api = self.as_(self.sonographer)
        self.assertEqual(api.post(f"/api/patient-routes/{self.route.pk}/accept/").status_code, 200)
        recorded = api.post(f"/api/patient-routes/{self.route.pk}/record-result/",
                            {"report": {"findings": "Normal liver.",
                                        "impression": "Normal study."}}, format="json")
        self.assertEqual(recorded.status_code, 200, recorded.data)
        done = api.post(f"/api/patient-routes/{self.route.pk}/complete/")
        self.assertEqual(done.status_code, 200, done.data)

        self.route.refresh_from_db()
        self.assertEqual(self.route.status, "completed")
        self.assertEqual(self.route.result_by, self.sonographer)

    def test_it_prints_its_own_paperwork(self):
        document = self.as_(self.sonographer).get(
            f"/api/patient-routes/{self.route.pk}/document/")
        self.assertEqual(document.status_code, 200)
        self.assertEqual([s["name"] for s in document.data["route"]["services"]], [self.exam.name])

    # --- what it cannot do -------------------------------------------------

    def test_it_reaches_no_chart(self):
        self.assertEqual(
            self.as_(self.sonographer).get(f"/api/patients/{self.patient.uuid}/overview/").status_code,
            403)

    def test_it_reaches_no_money(self):
        for path in ("/api/charges/", "/api/payments/", "/api/ledgers/", "/api/finance/report/"):
            with self.subTest(path=path):
                self.assertEqual(self.as_(self.sonographer).get(path).status_code, 403)

    def test_it_cannot_bill_the_examination_it_performed(self):
        refused = self.as_(self.sonographer).post("/api/charges/bill-services/", {
            "patient": self.patient.pk, "services": [f"billing_item:{self.exam.pk}"],
        }, format="json")
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(Charge.objects.filter(patient=self.patient).count(), 1)

    def test_it_cannot_reprice_the_catalogue_it_reads(self):
        refused = self.as_(self.sonographer).patch(f"/api/billing-items/{self.exam.pk}/",
                                                   {"price": "1.00"}, format="json")
        self.assertEqual(refused.status_code, 403)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.price, Decimal("8000"))

    def test_another_unit_s_queue_is_not_theirs(self):
        lab_route = PatientRoute.objects.create(visit=self.visit, department=self.department,
                                                purpose="laboratory", routed_by=self.doctor)
        queue = self.as_(self.sonographer).get("/api/patient-routes/")
        rows = queue.data["results"] if isinstance(queue.data, dict) else queue.data
        self.assertNotIn(lab_route.pk, [r["id"] for r in rows])
