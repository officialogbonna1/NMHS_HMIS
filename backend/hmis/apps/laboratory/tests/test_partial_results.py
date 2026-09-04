"""
The rule this module exists for: a laboratory scientist enters the values
they measured and nothing else, and the system takes it.

If any of these fail, somebody has made a parameter mandatory.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.laboratory.models import LabOrder, LabParameter, LabResultValue, LabTest
from apps.patients.models import Patient


class PartialResultTests(TestCase):
    def setUp(self):
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.fbc = LabTest.objects.get(code="fbc")
        self.client = APIClient()
        self.client.force_authenticate(self.scientist)

        self.order = LabOrder.objects.create(patient=self.patient, requested_by=self.doctor,
                                             created_by=self.doctor)
        self.item = self.order.items.create(test=self.fbc)

    def _param(self, code):
        return LabParameter.objects.get(test=self.fbc, code=code)

    def test_nothing_in_the_seeded_catalogue_is_mandatory(self):
        self.assertEqual(LabParameter.objects.filter(is_required=True).count(), 0)

    def test_three_of_fourteen_values_save_and_submit(self):
        """The example from the brief: Hb, PCV, neutrophils and platelets only."""
        response = self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk,
            "submit": True,
            "values": [
                {"parameter": self._param("hb").pk, "value": "13.5"},
                {"parameter": self._param("pcv").pk, "value": "40"},
                {"parameter": self._param("wbc").pk, "value": ""},        # not run
                {"parameter": self._param("neutrophils").pk, "value": "55"},
                {"parameter": self._param("lymphocytes").pk, "value": ""},  # not run
                {"parameter": self._param("platelets").pk, "value": "250"},
            ],
        }, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.item.values.count(), 4)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "submitted")
        self.assertEqual(self.item.performed_by, self.scientist)

    def test_an_empty_parameter_stores_no_row_at_all(self):
        self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk,
            "values": [{"parameter": self._param("hb").pk, "value": "  "}],
        }, format="json")
        self.assertFalse(LabResultValue.objects.filter(order_test=self.item).exists())

    def test_clearing_a_value_removes_it_rather_than_blanking_it(self):
        url = f"/api/lab-orders/{self.order.pk}/save-results/"
        hb = self._param("hb").pk
        self.client.post(url, {"order_test": self.item.pk,
                               "values": [{"parameter": hb, "value": "13.5"}]}, format="json")
        self.assertEqual(self.item.values.count(), 1)

        self.client.post(url, {"order_test": self.item.pk,
                               "values": [{"parameter": hb, "value": ""}]}, format="json")
        self.assertEqual(self.item.values.count(), 0)

    def test_one_value_is_enough_to_submit(self):
        response = self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk, "submit": True,
            "values": [{"parameter": self._param("hb").pk, "value": "9.1"}],
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)

    def test_submitting_nothing_at_all_is_refused(self):
        """One value is a result. No values and no comment is not."""
        response = self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk, "submit": True, "values": [],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "empty_result")

    def test_a_comment_alone_is_a_result(self):
        """A culture reporting "no growth" has no parameters to fill in."""
        item = self.order.items.create(test=LabTest.objects.get(code="blood-culture"))
        response = self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": item.pk, "submit": True, "values": [],
            "comments": "No growth after 5 days.",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        item.refresh_from_db()
        self.assertEqual(item.status, "submitted")

    def test_a_draft_keeps_the_test_open(self):
        response = self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk, "submit": False,
            "values": [{"parameter": self._param("hb").pk, "value": "13.5"}],
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "draft")
        self.assertEqual(self.order.items.first().values.count(), 1)

    def test_the_report_leaves_out_what_was_not_measured(self):
        self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk, "submit": True,
            "values": [
                {"parameter": self._param("hb").pk, "value": "13.5"},
                {"parameter": self._param("platelets").pk, "value": "250"},
                {"parameter": self._param("wbc").pk, "value": ""},
            ],
        }, format="json")
        report = self.client.get(f"/api/lab-orders/{self.order.pk}/report/").data
        rows = report["sections"][0]["values"]
        self.assertEqual([r["parameter"] for r in rows],
                         ["Haemoglobin (Hb)", "Platelets"])

    def test_a_test_with_no_result_is_left_off_the_report(self):
        self.order.items.create(test=LabTest.objects.get(code="lft"))
        self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk, "submit": True,
            "values": [{"parameter": self._param("hb").pk, "value": "13.5"}],
        }, format="json")
        report = self.client.get(f"/api/lab-orders/{self.order.pk}/report/").data
        self.assertEqual([s["test_code"] for s in report["sections"]], ["fbc"])

    def test_a_parameter_that_is_not_on_this_test_is_ignored_not_fatal(self):
        other = LabParameter.objects.filter(test__code="lft").first()
        response = self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk,
            "values": [
                {"parameter": other.pk, "value": "12"},
                {"parameter": self._param("hb").pk, "value": "13.5"},
            ],
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.item.values.count(), 1)

    def test_a_reading_of_zero_is_a_result_not_a_blank(self):
        """Basophils 0% is a measurement. Sent as a JSON number, it must survive."""
        response = self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk, "submit": True,
            "values": [{"parameter": self._param("basophils").pk, "value": 0}],
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        value = self.item.values.get()
        self.assertEqual(value.value, "0")
        self.assertEqual(value.flag, "normal")

    def test_a_numeric_value_sent_as_a_number_is_stored(self):
        self.client.post(f"/api/lab-orders/{self.order.pk}/save-results/", {
            "order_test": self.item.pk,
            "values": [{"parameter": self._param("hb").pk, "value": 13.5}],
        }, format="json")
        self.assertEqual(self.item.values.get().value, "13.5")
