"""
The laboratory request form: `GET /lab-orders/<id>/request-form/`.

It is the sheet that travels with the specimen, so it is deliberately *not*
the report. Two properties are the whole point of it:

1. **It carries no result value.** That is why the front desk and the cash
   desk can print and file it, while `/report/` stays laboratory-and-doctor.
   A widened permission here would be a clinical leak, so it is asserted
   rather than assumed.
2. **It reads the order-time snapshot.** Re-price a test tomorrow and the
   form the patient was handed still says what it said.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.departments.models import Department
from apps.laboratory.models import LabOrder, LabParameter, LabTest
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


class LabRequestFormTests(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor",
                                               first_name="Ada", last_name="Obi")
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.nurse = User.objects.create_user(username="nurse", password="t", role="nurse")
        self.pharmacist = User.objects.create_user(username="pharm", password="t", role="pharmacist")

        self.patient = Patient.objects.create(first_name="Ngozi", last_name="Ike", sex="F",
                                              created_by=self.reception)
        self.department = Department.objects.create(code="lab", name="Laboratory")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)
        self.route = PatientRoute.objects.create(
            visit=self.visit, department=self.department, purpose="laboratory",
            routed_by=self.doctor, notes="Query malaria, please run FBC.")

        self.client = APIClient()
        self.client.force_authenticate(self.scientist)
        order = self.client.post("/api/lab-orders/for-route/", {"route": self.route.pk},
                                 format="json").data
        self.order_id = order["id"]
        self.client.post(f"/api/lab-orders/{self.order_id}/add-tests/",
                         {"test_codes": ["fbc"]}, format="json")

    def _form(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(f"/api/lab-orders/{self.order_id}/request-form/")

    def test_the_form_says_what_was_asked_for_and_who_asked(self):
        response = self._form(self.scientist)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["order"]["requested_by"], "Ada Obi")
        self.assertEqual(response.data["order"]["clinical_notes"],
                         "Query malaria, please run FBC.")
        self.assertEqual(response.data["patient"]["file_number"], self.patient.file_number)
        self.assertEqual(response.data["patient"]["name"], "Ike, Ngozi")
        self.assertEqual([t["name"] for t in response.data["tests"]],
                         ["Full Blood Count (FBC)"])
        # What the bench needs before it can draw: the container and specimen.
        self.assertIn("specimen_type", response.data["tests"][0])
        self.assertIn("container", response.data["tests"][0])

    def test_the_form_carries_no_result_values(self):
        hb = LabParameter.objects.get(test__code="fbc", code="hb")
        item = LabOrder.objects.get(pk=self.order_id).items.get()
        self.client.post(f"/api/lab-orders/{self.order_id}/save-results/", {
            "order_test": item.pk, "submit": True,
            "values": [{"parameter": hb.pk, "value": "9.2"}],
        }, format="json")

        payload = str(self._form(self.scientist).data)
        self.assertNotIn("9.2", payload, "a result value reached the request form")
        self.assertNotIn("values", payload)

    def test_the_desk_can_print_it_because_it_holds_nothing_clinical(self):
        for user in (self.reception, self.cashier, self.doctor):
            with self.subTest(role=user.role):
                self.assertEqual(self._form(user).status_code, 200)

    def test_it_stays_inside_the_worklist_boundary(self):
        """
        WORKLIST_ROLES and nothing wider. A nurse never books a specimen in at
        this hospital and a pharmacist has no business in the laboratory at
        all; both are refused, and `printing.jsx` must not offer either of
        them the button.
        """
        for user in (self.nurse, self.pharmacist):
            with self.subTest(role=user.role):
                self.assertEqual(self._form(user).status_code, 403)

    def test_the_report_stays_narrower_than_the_form(self):
        """The split this endpoint exists for: form is the desk's, report is not."""
        client = APIClient()
        client.force_authenticate(self.reception)
        self.assertEqual(client.get(f"/api/lab-orders/{self.order_id}/report/").status_code, 403)
        self.assertEqual(
            client.get(f"/api/lab-orders/{self.order_id}/request-form/").status_code, 200)

    def test_the_price_on_the_form_is_the_one_charged_not_todays_catalogue(self):
        priced = self._form(self.scientist).data["tests"][0]["price"]

        fbc = LabTest.objects.get(code="fbc")
        fbc.price = Decimal(str(fbc.charge_amount)) + Decimal("2500")
        fbc.billing_item = None
        fbc.save(update_fields=["price", "billing_item"])

        reprinted = self._form(self.scientist).data["tests"][0]["price"]
        self.assertEqual(reprinted, priced,
                         "re-pricing the catalogue rewrote a form already issued")
