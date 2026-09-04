"""
The whole journey, scenario by scenario:

    doctor orders → charge raised → reception or the cash desk settles it
    → the bench sees where the money stands → runs the test → submits
    → an authorised user verifies → the doctor reads it on the chart.

Scenarios A–I are the ones the workflow was specified against. Each one is a
statement about money or history that has to keep being true.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge, PatientLedger, PaymentDeferral
from apps.departments.models import Department
from apps.laboratory.models import LabOrder, LabParameter, LabTest
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


class LabWorkflowTests(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.department = Department.objects.create(code="lab", name="Laboratory")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)
        self.client = APIClient()

    # --- helpers ---------------------------------------------------------

    def _order(self, *codes, notes="", purpose="laboratory"):
        """What a doctor referring to the lab actually does."""
        route = PatientRoute.objects.create(
            visit=self.visit, department=self.department, purpose=purpose,
            routed_by=self.doctor, notes=notes)
        self.client.force_authenticate(self.doctor)
        order = self.client.post("/api/lab-orders/for-route/", {"route": route.pk},
                                 format="json").data
        if codes:
            self.client.post(f"/api/lab-orders/{order['id']}/add-tests/",
                             {"test_codes": list(codes)}, format="json")
        return LabOrder.objects.get(pk=order["id"]), route

    def _charge(self, order):
        return order.items.first().charge

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    def _lab_view(self, order):
        return self._as(self.scientist).get(f"/api/lab-orders/{order.pk}/").data

    # --- Scenario I: only what the doctor asked for -----------------------

    def test_scenario_I_an_order_holds_only_the_test_the_doctor_asked_for(self):
        order, _ = self._order("mp", notes="Fever, query malaria.")
        self.assertEqual([i.test.code for i in order.items.all()], ["mp"])

        # And nothing else has crept in from the catalogue.
        for code in ["fbc", "pcv", "rbg", "hiv", "hbsag", "urinalysis", "genotype"]:
            self.assertFalse(order.items.filter(test__code=code).exists(), code)

    def test_an_order_with_no_tests_named_starts_empty(self):
        """Opening a referral is not an invitation to load the catalogue."""
        order, _ = self._order(notes="Please run whatever the history suggests.")
        self.assertEqual(order.items.count(), 0)

    def test_the_lab_can_add_a_test_and_it_is_marked_as_theirs_and_billed(self):
        order, _ = self._order("mp")
        self._as(self.scientist).post(f"/api/lab-orders/{order.pk}/add-tests/",
                                      {"test_codes": ["fbc"], "source": "laboratory"},
                                      format="json")
        added = order.items.get(test__code="fbc")
        self.assertEqual(added.source, "laboratory")
        # Added by the bench, billed exactly the same way.
        self.assertIsNotNone(added.charge)
        self.assertEqual(added.charge.amount, Decimal("3500.00"))
        self.assertEqual(order.items.get(test__code="mp").source, "requested")

    # --- Scenario A: fully paid ------------------------------------------

    def test_scenario_A_ordered_paid_run_submitted_verified(self):
        order, route = self._order("fbc")
        charge = self._charge(order)
        self.assertEqual(charge.amount, Decimal("3500.00"))
        self.assertEqual(charge.settlement_status, "unpaid")

        # Reception takes the money.
        paid = self._as(self.reception).post("/api/payments/", {
            "patient": self.patient.pk, "amount": "3500", "method": "cash"}, format="json")
        self.assertEqual(paid.status_code, 201, paid.data)

        charge.refresh_from_db()
        self.assertEqual(charge.amount_paid, Decimal("3500.00"))
        self.assertEqual(charge.balance, Decimal("0.00"))
        self.assertEqual(charge.settlement_status, "paid")

        # The bench can see it is paid.
        self.assertEqual(self._lab_view(order)["items"][0]["billing"]["status"], "paid")

        # Run it, submit it, verify it.
        item = order.items.get()
        hb = LabParameter.objects.get(test__code="fbc", code="hb")
        self._as(self.scientist).post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk, "submit": True,
            "values": [{"parameter": hb.pk, "value": "9.2"}]}, format="json")
        item.refresh_from_db()
        self.assertEqual(item.status, "submitted")

        self._as(self.scientist).post(f"/api/lab-orders/{order.pk}/verify/", {}, format="json")
        item.refresh_from_db()
        order.refresh_from_db()
        route.refresh_from_db()
        self.assertEqual(item.status, "verified")
        self.assertEqual(item.verified_by, self.scientist)
        self.assertEqual(order.status, "completed")
        self.assertEqual(route.status, "completed")

    # --- Scenario B: unpaid ----------------------------------------------

    def test_scenario_B_an_unpaid_test_says_so_everywhere(self):
        order, _ = self._order("fbc")
        charge = self._charge(order)
        self.assertEqual(charge.settlement_status, "unpaid")
        self.assertEqual(charge.amount_paid, Decimal("0.00"))
        self.assertEqual(charge.balance, Decimal("3500.00"))

        # On the bench's screen.
        view = self._lab_view(order)
        self.assertEqual(view["items"][0]["billing"]["status"], "unpaid")
        self.assertEqual(view["billing"]["outstanding"], "3500.00")

        # And on the debtors list the counter works from.
        owing = self._as(self.cashier).get("/api/ledgers/", {"owing": "true"}).data
        rows = owing["results"] if "results" in owing else owing
        self.assertIn(self.patient.pk, [r["patient"] for r in rows])

    # --- Scenario C: partial payment -------------------------------------

    def test_scenario_C_partial_payment(self):
        order, _ = self._order("fbc")
        self._as(self.reception).post("/api/payments/", {
            "patient": self.patient.pk, "amount": "2000"}, format="json")

        charge = self._charge(order)
        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal("3500.00"))
        self.assertEqual(charge.amount_paid, Decimal("2000.00"))
        self.assertEqual(charge.balance, Decimal("1500.00"))
        self.assertEqual(charge.settlement_status, "partial")
        self.assertEqual(self._lab_view(order)["items"][0]["billing"]["status"], "partial")

        self._as(self.reception).post("/api/payments/", {
            "patient": self.patient.pk, "amount": "1500"}, format="json")
        charge.refresh_from_db()
        self.assertEqual(charge.balance, Decimal("0.00"))
        self.assertEqual(charge.settlement_status, "paid")

    # --- Scenario D: pay later -------------------------------------------

    def test_scenario_D_pay_later_is_authorised_and_still_outstanding(self):
        order, _ = self._order("fbc")
        charge = self._charge(order)

        approved = self._as(self.reception).post(
            f"/api/charges/{charge.pk}/defer/",
            {"reason": "Patient will pay on Friday."}, format="json")
        self.assertEqual(approved.status_code, 200, approved.data)

        charge.refresh_from_db()
        # Deferred is not paid. The money is still owed, still on the ledger,
        # still on the debtors list.
        self.assertEqual(charge.amount_paid, Decimal("0.00"))
        self.assertEqual(charge.balance, Decimal("3500.00"))
        self.assertEqual(charge.settlement_status, "deferred")
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         Decimal("3500.00"))

        deferral = PaymentDeferral.objects.get(charge=charge)
        self.assertEqual(deferral.approved_by, self.reception)
        self.assertEqual(deferral.amount_deferred, Decimal("3500.00"))
        self.assertEqual(deferral.reason, "Patient will pay on Friday.")
        self.assertIsNone(deferral.released_at)

        # The bench is told it may proceed, and by whom.
        item_billing = self._lab_view(order)["items"][0]["billing"]
        self.assertEqual(item_billing["status"], "deferred")
        self.assertTrue(item_billing["deferred"])

        # Later, they pay.
        self._as(self.reception).post("/api/payments/", {
            "patient": self.patient.pk, "amount": "3500"}, format="json")
        charge.refresh_from_db()
        deferral.refresh_from_db()
        self.assertEqual(charge.balance, Decimal("0.00"))
        self.assertEqual(charge.settlement_status, "paid")
        self.assertIsNotNone(deferral.released_at)

    def test_a_lab_scientist_cannot_approve_pay_later(self):
        order, _ = self._order("fbc")
        response = self._as(self.scientist).post(
            f"/api/charges/{self._charge(order).pk}/defer/", {"reason": "go on"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_pay_later_cannot_be_approved_twice_over(self):
        order, _ = self._order("fbc")
        charge = self._charge(order)
        self._as(self.reception).post(f"/api/charges/{charge.pk}/defer/",
                                      {"reason": "first"}, format="json")
        again = self._as(self.reception).post(f"/api/charges/{charge.pk}/defer/",
                                              {"reason": "second"}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(PaymentDeferral.objects.filter(charge=charge).count(), 1)

    # --- Scenario E: discount --------------------------------------------

    def test_scenario_E_a_flat_discount_keeps_the_original_amount(self):
        order, _ = self._order("fbc")
        charge = self._charge(order)

        response = self._as(self.cashier).post(
            f"/api/charges/{charge.pk}/discount-amount/",
            {"amount": "500", "reason": "Staff family"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal("3500.00"))          # untouched
        self.assertEqual(charge.amount_discounted, Decimal("500.00"))
        self.assertEqual(charge.payable, Decimal("3000.00"))
        self.assertEqual(charge.balance, Decimal("3000.00"))

        adjustment = charge.adjustments.get()
        self.assertEqual(adjustment.kind, "discount")
        self.assertEqual(adjustment.approved_by, self.cashier)
        self.assertEqual(adjustment.reason, "Staff family")

    def test_a_percentage_discount_still_works(self):
        order, _ = self._order("fbc")
        charge = self._charge(order)
        self._as(self.cashier).post(f"/api/charges/{charge.pk}/discount/",
                                    {"percent": "10", "reason": "Goodwill"}, format="json")
        charge.refresh_from_db()
        self.assertEqual(charge.amount_discounted, Decimal("350.00"))
        self.assertEqual(charge.payable, Decimal("3150.00"))

    def test_a_discount_does_not_touch_the_catalogue_price(self):
        order, _ = self._order("fbc")
        self._as(self.cashier).post(f"/api/charges/{self._charge(order).pk}/discount-amount/",
                                    {"amount": "500", "reason": "Goodwill"}, format="json")
        self.assertEqual(LabTest.objects.get(code="fbc").price, Decimal("3500.00"))

    # --- Scenario F: waiver ----------------------------------------------

    def test_scenario_F_a_partial_waiver_keeps_original_and_waived_apart(self):
        order, _ = self._order("fbc")
        charge = self._charge(order)

        response = self._as(self.cashier).post(
            f"/api/charges/{charge.pk}/waive/",
            {"amount": "1000", "reason": "Hardship"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal("3500.00"))       # original preserved
        self.assertEqual(charge.amount_waived, Decimal("1000.00"))
        self.assertEqual(charge.payable, Decimal("2500.00"))
        self.assertEqual(charge.balance, Decimal("2500.00"))
        self.assertEqual(charge.settlement_status, "unpaid")

        adjustment = charge.adjustments.get()
        self.assertEqual((adjustment.kind, adjustment.amount), ("waiver", Decimal("1000.00")))
        self.assertEqual(adjustment.approved_by, self.cashier)

    def test_a_full_waiver_settles_the_charge_without_anyone_paying(self):
        order, _ = self._order("fbc")
        charge = self._charge(order)
        self._as(self.cashier).post(f"/api/charges/{charge.pk}/waive/",
                                    {"reason": "Indigent patient"}, format="json")
        charge.refresh_from_db()
        self.assertEqual(charge.amount_waived, Decimal("3500.00"))
        self.assertEqual(charge.payable, Decimal("0.00"))
        self.assertEqual(charge.balance, Decimal("0.00"))
        self.assertEqual(charge.settlement_status, "waived")
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         Decimal("0.00"))

    def test_reception_cannot_waive_or_discount(self):
        order, _ = self._order("fbc")
        charge = self._charge(order)
        for url, body in [(f"/api/charges/{charge.pk}/waive/", {"reason": "x"}),
                          (f"/api/charges/{charge.pk}/discount-amount/",
                           {"amount": "100", "reason": "x"})]:
            self.assertEqual(self._as(self.reception).post(url, body, format="json").status_code,
                             403, url)

    # --- Scenario G: catalogue price change ------------------------------

    def test_scenario_G_a_price_change_does_not_rewrite_an_old_bill(self):
        order, _ = self._order("fbc")
        charge = self._charge(order)
        self.assertEqual(charge.amount, Decimal("3500.00"))

        # The administrator reprices the test.
        self._as(self.scientist).patch(f"/api/lab-tests/{LabTest.objects.get(code='fbc').pk}/",
                                       {"price": "4000"}, format="json")

        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal("3500.00"))
        self.assertEqual(order.items.get().unit_price, Decimal("3500.00"))
        self.assertEqual(self._lab_view(order)["billing"]["total"], "3500.00")

        # A new order takes the new price.
        later, _ = self._order("fbc")
        self.assertEqual(self._charge(later).amount, Decimal("4000.00"))

    # --- Scenario H: reference range change ------------------------------

    def test_scenario_H_a_reference_range_change_does_not_rewrite_an_old_report(self):
        order, _ = self._order("fbg")
        item = order.items.get()
        glucose = LabParameter.objects.get(test__code="fbg", code="glucose")
        self.assertEqual(item.parameters[0]["reference_range"], "3.9 – 5.5")

        self._as(self.scientist).post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk, "submit": True,
            "values": [{"parameter": glucose.pk, "value": "6.2"}]}, format="json")

        # The administrator changes the range afterwards.
        self._as(self.scientist).patch(f"/api/lab-parameters/{glucose.pk}/", {
            "reference_range": "4.0 – 7.0", "ref_low": "4.0", "ref_high": "7.0",
        }, format="json")

        report = self._as(self.doctor).get(f"/api/lab-orders/{order.pk}/report/").data
        row = report["sections"][0]["values"][0]
        self.assertEqual(row["reference_range"], "3.9 – 5.5")
        # And the flag it was read under stands: 6.2 was high against 3.9–5.5.
        self.assertEqual(row["flag"], "high")

    def test_a_test_renamed_in_the_catalogue_keeps_its_name_on_an_old_order(self):
        order, _ = self._order("mp")
        self._as(self.scientist).patch(f"/api/lab-tests/{LabTest.objects.get(code='mp').pk}/",
                                       {"name": "Blood Film for MPs"}, format="json")
        self.assertEqual(self._lab_view(order)["items"][0]["test_name"],
                         "Malaria Parasite (MP) — Microscopy")

    def test_a_parameter_retired_later_still_answers_on_an_old_order(self):
        order, _ = self._order("fbc")
        item = order.items.get()
        rdw = LabParameter.objects.get(test__code="fbc", code="rdw")
        self._as(self.scientist).delete(f"/api/lab-parameters/{rdw.pk}/")

        saved = self._as(self.scientist).post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk, "values": [{"parameter": rdw.pk, "value": "13.1"}],
        }, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(item.values.get().value, "13.1")

    def test_a_parameter_added_after_the_order_is_not_on_that_order(self):
        order, _ = self._order("fbc")
        item = order.items.get()
        added = self._as(self.scientist).post("/api/lab-parameters/", {
            "test": LabTest.objects.get(code="fbc").pk, "code": "retics",
            "name": "Reticulocytes", "result_type": "numeric", "unit": "%",
        }, format="json").data

        codes = [p["code"] for p in self._lab_view(order)["items"][0]["parameters"]]
        self.assertNotIn("retics", codes)
        # And a value against it is ignored rather than fatal.
        response = self._as(self.scientist).post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk, "values": [{"parameter": added["id"], "value": "1.2"}],
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(item.values.count(), 0)

    # --- lifecycle guards -------------------------------------------------

    def test_a_released_result_needs_a_reason_before_it_can_be_changed(self):
        order, _ = self._order("pcv")
        item = order.items.get()
        pcv = LabParameter.objects.get(test__code="pcv", code="pcv")
        lab = self._as(self.scientist)
        lab.post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk, "submit": True,
            "values": [{"parameter": pcv.pk, "value": "31"}]}, format="json")
        lab.post(f"/api/lab-orders/{order.pk}/verify/", {}, format="json")

        blocked = lab.post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk, "values": [{"parameter": pcv.pk, "value": "34"}]}, format="json")
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.data["code"], "amendment_reason_required")

        allowed = lab.post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk, "reason": "Transcription error",
            "values": [{"parameter": pcv.pk, "value": "34"}]}, format="json")
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(item.amendments.get().reason, "Transcription error")

    def test_an_unverified_result_is_not_presented_as_released(self):
        order, _ = self._order("pcv")
        item = order.items.get()
        pcv = LabParameter.objects.get(test__code="pcv", code="pcv")
        self._as(self.scientist).post(f"/api/lab-orders/{order.pk}/save-results/", {
            "order_test": item.pk, "submit": True,
            "values": [{"parameter": pcv.pk, "value": "31"}]}, format="json")

        report = self._as(self.doctor).get(f"/api/lab-orders/{order.pk}/report/").data
        self.assertFalse(report["sections"][0]["released"])
        self.assertIsNone(report["order"]["verified_at"])

    def test_a_test_with_no_price_raises_no_charge(self):
        """A zero-value charge is noise on a bill, not a debt."""
        free = LabTest.objects.create(code="free-test", name="Ward Glucose Strip",
                                      category="chemistry", price=0)
        order, _ = self._order()
        self._as(self.doctor).post(f"/api/lab-orders/{order.pk}/add-tests/",
                                   {"tests": [free.pk]}, format="json")
        self.assertIsNone(order.items.get().charge)
        self.assertFalse(Charge.objects.filter(patient=self.patient).exists())
