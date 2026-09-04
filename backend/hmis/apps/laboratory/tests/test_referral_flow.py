"""
The laboratory sits inside the workflow the hospital already has: a doctor
refers, the bench opens the referral, works it, and verifying the report
closes the route and files the result on the chart.

Nothing here should need a second request raised by hand.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from decimal import Decimal

from apps.billing.models import Charge, PatientLedger
from apps.billing.services import add_charge
from apps.departments.models import Department
from apps.laboratory.models import LabOrder, LabParameter, LabTest
from apps.patients.models import MedicalTest, Patient
from apps.workflow.models import PatientRoute, Visit


class ReferralFlowTests(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.patient = Patient.objects.create(first_name="Ngozi", last_name="Ike", sex="F",
                                              created_by=self.reception)
        self.department = Department.objects.create(code="lab", name="Laboratory")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)
        self.route = PatientRoute.objects.create(
            visit=self.visit, department=self.department, purpose="laboratory",
            routed_by=self.doctor, notes="Query malaria, please run FBC and MP.")
        self.client = APIClient()

    def _open_the_order(self):
        self.client.force_authenticate(self.scientist)
        response = self.client.post("/api/lab-orders/for-route/", {"route": self.route.pk},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_opening_a_referral_creates_the_order_from_it(self):
        data = self._open_the_order()
        self.assertEqual(data["patient"], self.patient.pk)
        self.assertEqual(data["route"], self.route.pk)
        # The doctor's referral note travels with it — the bench should not
        # have to open another page to see what was asked for.
        self.assertEqual(data["clinical_notes"], self.route.notes)
        self.assertTrue(data["order_number"].startswith("LAB-"))

    def test_opening_the_same_referral_twice_returns_the_same_order(self):
        first = self._open_the_order()
        second = self._open_the_order()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(LabOrder.objects.count(), 1)

    def test_a_panel_is_expanded_to_the_tests_it_names(self):
        order = self._open_the_order()
        response = self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                                    {"panels": ["malaria-screen"]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({i["test_code"] for i in response.data["items"]},
                         {"mrdt", "mp", "fbc"})

    def test_a_test_named_twice_lands_on_the_order_once(self):
        order = self._open_the_order()
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["fbc"]}, format="json")
        response = self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                                    {"panels": ["malaria-screen"], "test_codes": ["fbc"]},
                                    format="json")
        codes = [i["test_code"] for i in response.data["items"]]
        self.assertEqual(codes.count("fbc"), 1)

    def test_verifying_closes_the_route_and_files_the_result_on_the_chart(self):
        order = self._open_the_order()
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["fbc"]}, format="json")
        item = LabOrder.objects.get(pk=order["id"]).items.get()
        hb = LabParameter.objects.get(test__code="fbc", code="hb")

        self.client.post(f"/api/lab-orders/{order['id']}/save-results/", {
            "order_test": item.pk, "submit": True,
            "values": [{"parameter": hb.pk, "value": "9.2"}],
        }, format="json")

        verified = self.client.post(f"/api/lab-orders/{order['id']}/verify/", {}, format="json")
        self.assertEqual(verified.status_code, 200, verified.data)
        self.assertEqual(verified.data["verified_by_name"], "lab")

        self.route.refresh_from_db()
        self.assertEqual(self.route.status, "completed")
        self.assertIn("Haemoglobin", self.route.result)
        self.assertEqual(self.route.result_by, self.scientist)

        # And the permanent copy every other unit files.
        filed = MedicalTest.objects.get(patient=self.patient, source_route=self.route)
        self.assertEqual(filed.test_type, "labs")
        self.assertIn("Haemoglobin", filed.impressions)

    def test_the_doctor_is_told_when_a_result_is_submitted(self):
        from apps.core.models import Notification
        order = self._open_the_order()
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["mp"]}, format="json")
        item = LabOrder.objects.get(pk=order["id"]).items.get()
        result = LabParameter.objects.get(test__code="mp", code="result")
        self.client.post(f"/api/lab-orders/{order['id']}/save-results/", {
            "order_test": item.pk, "submit": True,
            "values": [{"parameter": result.pk, "value": "Detected"}],
        }, format="json")

        note = Notification.objects.filter(recipient=self.doctor, category="clinical").first()
        self.assertIsNotNone(note)
        self.assertEqual(note.action_url, f"/patients/{self.patient.pk}/lab")

    def test_ordering_a_test_raises_the_charge_by_itself(self):
        """
        The doctor's order is what creates the debt — nobody retypes it at
        the counter. The charge still goes through billing/services.py, so
        there is one ledger and one debtors list.
        """
        order = self._open_the_order()
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["fbc"]}, format="json")

        charge = Charge.objects.get(source_type="lab_test")
        self.assertEqual(charge.amount, Decimal("3500.00"))
        self.assertEqual(charge.patient, self.patient)
        self.assertEqual(charge.description, "Laboratory: Full Blood Count (FBC)")
        self.assertEqual(charge.settlement_status, "unpaid")

        billed = self.client.get(f"/api/lab-orders/{order['id']}/").data
        self.assertTrue(billed["billing"]["billed"])
        self.assertFalse(billed["billing"]["settled"])
        self.assertEqual(billed["billing"]["outstanding"], "3500.00")
        self.assertEqual(billed["items"][0]["billing"]["status"], "unpaid")

    def test_the_ledger_carries_the_lab_charge(self):
        """One financial system: the debtors list must see it."""
        order = self._open_the_order()
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["fbc"]}, format="json")
        ledger = PatientLedger.objects.get(patient=self.patient)
        self.assertEqual(ledger.outstanding_balance, Decimal("3500.00"))

    def test_an_unpaid_order_can_still_be_resulted(self):
        """The sample is already drawn; the desk chases the balance."""
        order = self._open_the_order()
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["pcv"]}, format="json")
        item = LabOrder.objects.get(pk=order["id"]).items.get()
        pcv = LabParameter.objects.get(test__code="pcv", code="pcv")
        response = self.client.post(f"/api/lab-orders/{order['id']}/save-results/", {
            "order_test": item.pk, "submit": True,
            "values": [{"parameter": pcv.pk, "value": "31"}],
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Charge.objects.filter(source_type="lab_order").exists())

    def test_correcting_a_reported_value_leaves_an_amendment(self):
        order = self._open_the_order()
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["pcv"]}, format="json")
        item = LabOrder.objects.get(pk=order["id"]).items.get()
        pcv = LabParameter.objects.get(test__code="pcv", code="pcv")
        url = f"/api/lab-orders/{order['id']}/save-results/"

        self.client.post(url, {"order_test": item.pk, "submit": True,
                               "values": [{"parameter": pcv.pk, "value": "31"}]}, format="json")
        self.client.post(url, {"order_test": item.pk, "reason": "Transcription error",
                               "values": [{"parameter": pcv.pk, "value": "34"}]}, format="json")

        amendment = item.amendments.get()
        self.assertEqual((amendment.previous_value, amendment.new_value), ("31", "34"))
        self.assertEqual(amendment.amended_by, self.scientist)
        self.assertEqual(amendment.reason, "Transcription error")
        self.assertEqual(item.values.get().value, "34")

    def test_a_draft_edit_before_reporting_is_not_an_amendment(self):
        order = self._open_the_order()
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["pcv"]}, format="json")
        item = LabOrder.objects.get(pk=order["id"]).items.get()
        pcv = LabParameter.objects.get(test__code="pcv", code="pcv")
        url = f"/api/lab-orders/{order['id']}/save-results/"
        self.client.post(url, {"order_test": item.pk,
                               "values": [{"parameter": pcv.pk, "value": "31"}]}, format="json")
        self.client.post(url, {"order_test": item.pk,
                               "values": [{"parameter": pcv.pk, "value": "34"}]}, format="json")
        self.assertEqual(item.amendments.count(), 0)

    def test_a_doctor_reads_their_own_patients_results(self):
        order = self._open_the_order()
        self.client.force_authenticate(self.doctor)
        response = self.client.get(f"/api/lab-orders/{order['id']}/")
        self.assertEqual(response.status_code, 200)

    def test_reception_cannot_verify_a_report(self):
        order = self._open_the_order()
        self.client.force_authenticate(self.reception)
        response = self.client.post(f"/api/lab-orders/{order['id']}/verify/", {}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_a_referral_that_is_not_the_labs_is_refused(self):
        scan = PatientRoute.objects.create(visit=self.visit, department=self.department,
                                           purpose="ultrasound", routed_by=self.doctor)
        self.client.force_authenticate(self.scientist)
        response = self.client.post("/api/lab-orders/for-route/", {"route": scan.pk},
                                    format="json")
        self.assertEqual(response.status_code, 400)

    def test_removing_a_test_that_has_results_cancels_rather_than_deletes(self):
        order = self._open_the_order()
        self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                         {"test_codes": ["pcv"]}, format="json")
        item = LabOrder.objects.get(pk=order["id"]).items.get()
        pcv = LabParameter.objects.get(test__code="pcv", code="pcv")
        self.client.post(f"/api/lab-orders/{order['id']}/save-results/", {
            "order_test": item.pk, "values": [{"parameter": pcv.pk, "value": "31"}],
        }, format="json")

        self.client.post(f"/api/lab-orders/{order['id']}/remove-test/",
                         {"order_test": item.pk}, format="json")
        item.refresh_from_db()
        self.assertEqual(item.status, "cancelled")
        self.assertEqual(item.values.count(), 1)

    def test_collecting_the_sample_is_stamped(self):
        order = self._open_the_order()
        response = self.client.post(f"/api/lab-orders/{order['id']}/collect-sample/",
                                    {"specimen_id": "S-0012"}, format="json")
        self.assertEqual(response.data["specimen_id"], "S-0012")
        self.assertEqual(response.data["collected_by_name"], "lab")
        self.assertIsNotNone(response.data["specimen_collected_at"])
        self.assertEqual(response.data["status"], "collected")
