"""
Flagging, the configurable catalogue, and the boundaries around both.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.laboratory.models import LabOrder, LabParameter, LabTest
from apps.laboratory.services import flag_for
from apps.patients.models import Patient


class FlagTests(TestCase):
    def setUp(self):
        self.wbc = LabParameter.objects.get(test__code="fbc", code="wbc")   # 4 – 11
        self.hb = LabParameter.objects.get(test__code="fbc", code="hb")     # sex-dependent
        self.abo = LabParameter.objects.get(test__code="blood-group", code="abo")

    def test_a_number_inside_the_range_is_normal(self):
        self.assertEqual(flag_for(self.wbc, "6.1"), "normal")

    def test_below_and_above_are_low_and_high(self):
        self.assertEqual(flag_for(self.wbc, "2.9"), "low")
        self.assertEqual(flag_for(self.wbc, "14"), "high")

    def test_a_boundary_value_is_normal(self):
        self.assertEqual(flag_for(self.wbc, "4"), "normal")
        self.assertEqual(flag_for(self.wbc, "11"), "normal")

    def test_a_sex_dependent_range_is_printed_but_never_flagged(self):
        """Under-flagging is the safe direction: the doctor reads the range."""
        self.assertEqual(flag_for(self.hb, "11.0"), "")
        self.assertTrue(self.hb.reference_range)

    def test_a_non_numeric_parameter_is_never_flagged(self):
        self.assertEqual(flag_for(self.abo, "O"), "")

    def test_a_comparison_is_not_read_as_a_number(self):
        """"< 5" means the assay could not resolve it, not that it is 5."""
        self.assertEqual(flag_for(self.wbc, "< 5"), "")

    def test_nonsense_does_not_crash_the_flag(self):
        self.assertEqual(flag_for(self.wbc, "haemolysed"), "")

    def test_a_manual_flag_survives_a_later_save(self):
        reception = User.objects.create_user(username="r2", password="t", role="reception")
        scientist = User.objects.create_user(username="l2", password="t", role="laboratory")
        patient = Patient.objects.create(first_name="Ola", last_name="Eze", sex="M",
                                         created_by=reception)
        order = LabOrder.objects.create(patient=patient, created_by=scientist)
        item = order.items.create(test=LabTest.objects.get(code="fbc"))

        client = APIClient()
        client.force_authenticate(scientist)
        client.post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk,
            "values": [{"parameter": self.wbc.pk, "value": "6.1", "flag": "critical"}],
        }, format="json")
        value = item.values.get()
        self.assertEqual(value.flag, "critical")
        self.assertTrue(value.flag_is_manual)


class CatalogueTests(TestCase):
    def setUp(self):
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nurse", password="t", role="nurse")
        self.client = APIClient()

    def test_the_catalogue_arrives_seeded(self):
        self.client.force_authenticate(self.doctor)
        response = self.client.get("/api/lab-tests/", {"page_size": 200})
        rows = response.data["results"] if "results" in response.data else response.data
        codes = {r["code"] for r in rows}
        for expected in ["fbc", "rft", "lft", "lipid", "urinalysis", "mp", "hiv",
                         "semen-analysis", "widal", "tsh", "psa", "blood-culture"]:
            self.assertIn(expected, codes)

    def test_a_doctor_can_read_the_catalogue_but_not_change_it(self):
        self.client.force_authenticate(self.doctor)
        self.assertEqual(self.client.get("/api/lab-tests/").status_code, 200)
        response = self.client.post("/api/lab-tests/", {
            "code": "sneaky", "name": "Sneaky", "category": "other"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_the_lab_adds_a_test_and_its_parameters_without_a_deployment(self):
        self.client.force_authenticate(self.scientist)
        created = self.client.post("/api/lab-tests/", {
            "code": "magnesium", "name": "Serum Magnesium", "category": "chemistry",
            "price": "3500", "specimen_type": "Serum",
        }, format="json")
        self.assertEqual(created.status_code, 201, created.data)

        parameter = self.client.post("/api/lab-parameters/", {
            "test": created.data["id"], "code": "mg", "name": "Magnesium",
            "result_type": "numeric", "unit": "mmol/L", "reference_range": "0.7 – 1.0",
            "ref_low": "0.7", "ref_high": "1.0",
        }, format="json")
        self.assertEqual(parameter.status_code, 201, parameter.data)
        # Off by default, even when the lab does not think about it.
        self.assertFalse(parameter.data["is_required"])

    def test_deleting_a_test_retires_it_so_old_results_still_read(self):
        self.client.force_authenticate(self.scientist)
        test = LabTest.objects.get(code="esr")
        response = self.client.delete(f"/api/lab-tests/{test.pk}/")
        self.assertEqual(response.status_code, 204)
        test.refresh_from_db()
        self.assertFalse(test.is_active)
        self.assertTrue(LabTest.objects.filter(pk=test.pk).exists())

    def test_a_bad_reference_range_is_refused(self):
        self.client.force_authenticate(self.scientist)
        test = LabTest.objects.get(code="esr")
        response = self.client.post("/api/lab-parameters/", {
            "test": test.pk, "code": "backwards", "name": "Backwards",
            "ref_low": "10", "ref_high": "2",
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_parameters_can_be_reordered(self):
        self.client.force_authenticate(self.scientist)
        rows = list(LabParameter.objects.filter(test__code="lft").order_by("display_order"))
        reversed_ids = [p.pk for p in reversed(rows)]
        response = self.client.post("/api/lab-parameters/reorder/",
                                    {"order": reversed_ids}, format="json")
        self.assertEqual(response.status_code, 200)
        again = list(LabParameter.objects.filter(test__code="lft").order_by("display_order"))
        self.assertEqual([p.pk for p in again], reversed_ids)

    def test_a_panel_references_catalogue_tests_rather_than_copying_them(self):
        self.client.force_authenticate(self.doctor)
        response = self.client.get("/api/lab-panels/")
        rows = response.data["results"] if "results" in response.data else response.data
        antenatal = next(p for p in rows if p["code"] == "antenatal-booking")
        codes = {t["code"] for t in antenatal["test_details"]}
        self.assertIn("fbc", codes)
        # The same row the standalone FBC uses — one test, one definition.
        self.assertEqual(
            LabTest.objects.filter(code="fbc").count(), 1)

    def test_a_nurse_cannot_enter_a_result(self):
        reception = User.objects.create_user(username="r3", password="t", role="reception")
        patient = Patient.objects.create(first_name="Uche", last_name="Nwosu", sex="M",
                                         created_by=reception)
        order = LabOrder.objects.create(patient=patient, created_by=self.doctor)
        item = order.items.create(test=LabTest.objects.get(code="fbc"))
        self.client.force_authenticate(self.nurse)
        response = self.client.post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk,
            "values": [{"parameter": LabParameter.objects.get(
                test__code="fbc", code="hb").pk, "value": "13"}],
        }, format="json")
        self.assertEqual(response.status_code, 403)


class ResultBoundaryTests(TestCase):
    """
    A laboratory value is a clinical record. It reaches the bench and the
    doctor holding the patient — the same boundary `patients/overview.py`
    draws — while the counter gets the worklist it needs to bill from and
    nothing more.
    """

    def setUp(self):
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.nurse = User.objects.create_user(username="nurse", password="t", role="nurse")
        self.patient = Patient.objects.create(first_name="Chidi", last_name="Okafor", sex="M",
                                              created_by=self.reception)
        self.order = LabOrder.objects.create(patient=self.patient, requested_by=self.doctor,
                                             created_by=self.doctor)
        item = self.order.items.create(test=LabTest.objects.get(code="fbc"))
        item.values.create(parameter=LabParameter.objects.get(test__code="fbc", code="hb"),
                           value="9.2", flag="", recorded_by=self.scientist)
        self.client = APIClient()

    def test_the_bench_and_the_doctor_read_the_values(self):
        for user in (self.scientist, self.doctor):
            self.client.force_authenticate(user)
            response = self.client.get(f"/api/lab-orders/{self.order.pk}/")
            self.assertEqual(response.status_code, 200, user.role)
            self.assertEqual(response.data["items"][0]["values"][0]["value"], "9.2")

    def test_reception_and_the_cash_desk_cannot_open_a_result(self):
        for user in (self.reception, self.cashier):
            self.client.force_authenticate(user)
            self.assertEqual(
                self.client.get(f"/api/lab-orders/{self.order.pk}/").status_code, 403, user.role)
            self.assertEqual(
                self.client.get(f"/api/lab-orders/{self.order.pk}/report/").status_code, 403,
                user.role)

    def test_the_cash_desk_still_sees_the_order_exists_so_it_can_bill_it(self):
        self.client.force_authenticate(self.cashier)
        response = self.client.get("/api/lab-orders/")
        self.assertEqual(response.status_code, 200)
        rows = response.data["results"] if "results" in response.data else response.data
        row = next(r for r in rows if r["id"] == self.order.pk)
        self.assertEqual(row["test_names"], ["Full Blood Count (FBC)"])
        # The summary carries no values — that is the whole point of it.
        self.assertNotIn("items", row)

    def test_a_nurse_reaches_neither(self):
        self.client.force_authenticate(self.nurse)
        self.assertEqual(self.client.get("/api/lab-orders/").status_code, 403)
        self.assertEqual(
            self.client.get(f"/api/lab-orders/{self.order.pk}/").status_code, 403)

    def test_reception_can_still_read_the_catalogue_to_quote_a_price(self):
        self.client.force_authenticate(self.reception)
        self.assertEqual(self.client.get("/api/lab-tests/").status_code, 200)

    def test_the_chart_can_ask_the_list_for_the_values(self):
        """The patient's chart shows results inline, so it asks for detail."""
        self.client.force_authenticate(self.doctor)
        response = self.client.get("/api/lab-orders/", {"patient": self.patient.pk, "detail": 1})
        rows = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(rows[0]["items"][0]["values"][0]["value"], "9.2")

    def test_asking_for_detail_does_not_get_the_cash_desk_the_values(self):
        self.client.force_authenticate(self.cashier)
        response = self.client.get("/api/lab-orders/", {"detail": 1})
        rows = response.data["results"] if "results" in response.data else response.data
        self.assertNotIn("items", rows[0])
