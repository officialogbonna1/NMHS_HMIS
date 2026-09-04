"""
Retiring a catalogue test, and bringing it back.

A test is never deleted — orders already filed point at the row, and a report
from last year has to keep saying what it said. That makes retiring a state,
and every state a thing can be put into has to be one it can be taken out of.

It was not. `LabTestViewSet.get_queryset()` applied its active-only browsing
default to *every* action, so `get_object()` could not find a row it had just
retired: the detail route, the PATCH and any restore all answered
`No LabTest matches the given query.` Retiring was a one-way door. The default
now applies to `list` alone, and `POST /lab-tests/<id>/restore/` is the
mirror of the retire.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.departments.models import Department
from apps.laboratory.models import LabOrder, LabParameter, LabTest
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


class RetireAndRestoreTests(TestCase):
    def setUp(self):
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.client = APIClient()
        self.test = LabTest.objects.get(code="esr")

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    def _retire(self):
        return self._as(self.scientist).delete(f"/api/lab-tests/{self.test.pk}/")

    def _restore(self):
        return self._as(self.scientist).post(f"/api/lab-tests/{self.test.pk}/restore/")

    # --- active → retired -------------------------------------------------

    def test_retiring_an_active_test_makes_it_inactive_without_deleting_it(self):
        parameters_before = set(self.test.parameters.values_list("pk", flat=True))
        self.assertTrue(parameters_before)

        self.assertEqual(self._retire().status_code, 204)

        self.test.refresh_from_db()
        self.assertFalse(self.test.is_active)
        self.assertTrue(LabTest.objects.filter(pk=self.test.pk).exists())
        # The result lines travel with it: retiring a test is not a cascade.
        self.assertEqual(set(self.test.parameters.values_list("pk", flat=True)),
                         parameters_before)

    # --- retired → active -------------------------------------------------

    def test_a_retired_test_can_still_be_fetched_by_id(self):
        """The bug itself: the row existed and the API could not find it."""
        self._retire()
        response = self._as(self.scientist).get(f"/api/lab-tests/{self.test.pk}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["is_active"])

    def test_restoring_a_retired_test_brings_it_back(self):
        self._retire()
        response = self._restore()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["is_active"])
        self.test.refresh_from_db()
        self.assertTrue(self.test.is_active)

    def test_restoring_keeps_the_same_row_and_its_parameters(self):
        """Brought back, not re-created — the id and the lines are the same."""
        parameters_before = set(self.test.parameters.values_list("pk", flat=True))
        self._retire()
        restored = self._restore().data

        self.assertEqual(restored["id"], self.test.pk)
        self.assertEqual(restored["code"], "esr")
        self.assertEqual(set(LabTest.objects.get(pk=self.test.pk)
                             .parameters.values_list("pk", flat=True)),
                         parameters_before)

    def test_retire_and_restore_can_be_repeated(self):
        """Neither direction is a one-way door."""
        for _ in range(2):
            self.assertEqual(self._retire().status_code, 204)
            self.test.refresh_from_db()
            self.assertFalse(self.test.is_active)
            self.assertEqual(self._restore().status_code, 200)
            self.test.refresh_from_db()
            self.assertTrue(self.test.is_active)

    def test_restoring_a_test_that_is_already_active_is_harmless(self):
        response = self._restore()
        self.assertEqual(response.status_code, 200)
        self.test.refresh_from_db()
        self.assertTrue(self.test.is_active)

    def test_both_directions_leave_an_audit_row_naming_who_did_it(self):
        self._retire()
        self._restore()
        actions = list(AuditLog.objects.filter(actor=self.scientist)
                       .values_list("action", flat=True))
        self.assertIn("lab.test_deactivated", actions)
        self.assertIn("lab.test_restored", actions)

    # --- who may do it ----------------------------------------------------

    def test_a_doctor_can_read_the_catalogue_but_cannot_restore_a_test(self):
        self._retire()
        response = self._as(self.doctor).post(f"/api/lab-tests/{self.test.pk}/restore/")
        self.assertEqual(response.status_code, 403)
        self.test.refresh_from_db()
        self.assertFalse(self.test.is_active)

    # --- what the lists show ---------------------------------------------

    def test_the_default_catalogue_listing_hides_a_retired_test(self):
        self._retire()
        rows = self._as(self.doctor).get("/api/lab-tests/", {"page_size": 400}).data["results"]
        self.assertNotIn("esr", {r["code"] for r in rows})

    def test_the_catalogue_page_asks_for_all_and_sees_it(self):
        self._retire()
        rows = self._as(self.scientist).get(
            "/api/lab-tests/", {"all": 1, "page_size": 400}).data["results"]
        row = next(r for r in rows if r["code"] == "esr")
        self.assertFalse(row["is_active"])

    def test_a_restored_test_is_back_in_the_default_listing(self):
        self._retire()
        self._restore()
        rows = self._as(self.doctor).get("/api/lab-tests/", {"page_size": 400}).data["results"]
        self.assertIn("esr", {r["code"] for r in rows})

    def test_the_category_counts_exclude_a_retired_test(self):
        def esr_category_count():
            body = self._as(self.scientist).get("/api/lab-tests/categories/").data
            return next(c["count"] for c in body["categories"]
                        if c["value"] == self.test.category)

        before = esr_category_count()
        self._retire()
        self.assertEqual(esr_category_count(), before - 1)
        self._restore()
        self.assertEqual(esr_category_count(), before)


class RetiredTestsAndOrdersTests(TestCase):
    """What retiring does to work already in the building, and to new work."""

    def setUp(self):
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.department = Department.objects.create(code="lab", name="Laboratory")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)
        self.client = APIClient()

    def _as(self, user):
        self.client.force_authenticate(user)
        return self.client

    def _order(self, *codes):
        route = PatientRoute.objects.create(visit=self.visit, department=self.department,
                                            purpose="laboratory", routed_by=self.doctor)
        order = self._as(self.doctor).post("/api/lab-orders/for-route/",
                                           {"route": route.pk}, format="json").data
        if codes:
            self._as(self.doctor).post(f"/api/lab-orders/{order['id']}/add-tests/",
                                       {"test_codes": list(codes)}, format="json")
        return LabOrder.objects.get(pk=order["id"])

    def test_an_order_placed_before_a_retirement_is_untouched_by_it(self):
        order = self._order("esr")
        item = order.items.get()
        snapshot_before = item.parameters_snapshot
        price_before = item.unit_price
        charge_before = item.charge_id

        self._as(self.scientist).delete(f"/api/lab-tests/{item.test_id}/")

        item.refresh_from_db()
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(item.test_name, LabTest.objects.get(code="esr").name)
        # The line reads its own snapshot, so nothing about it moved.
        self.assertEqual(item.parameters_snapshot, snapshot_before)
        self.assertEqual(item.unit_price, price_before)
        self.assertEqual(item.charge_id, charge_before)

    def test_a_filed_result_still_reads_after_the_test_is_retired_and_restored(self):
        order = self._order("esr")
        item = order.items.get()
        parameter = LabParameter.objects.filter(test__code="esr", is_active=True).first()

        saved = self._as(self.scientist).post(
            f"/api/lab-orders/{order.pk}/save-results/",
            {"order_test": item.pk,
             "values": [{"parameter": parameter.pk, "value": "18"}]},
            format="json")
        self.assertIn(saved.status_code, (200, 201), saved.data)

        self._as(self.scientist).delete(f"/api/lab-tests/{item.test_id}/")
        item.refresh_from_db()
        self.assertEqual(item.values.count(), 1)
        self.assertEqual(item.values.get().value, "18")

        self._as(self.scientist).post(f"/api/lab-tests/{item.test_id}/restore/")
        item.refresh_from_db()
        self.assertEqual(item.values.get().value, "18")

        # And the report still renders it.
        report = self._as(self.scientist).get(f"/api/lab-orders/{order.pk}/report/")
        self.assertEqual(report.status_code, 200, report.data)

    def test_a_retired_test_cannot_be_added_to_a_new_order(self):
        self._as(self.scientist).delete(
            f"/api/lab-tests/{LabTest.objects.get(code='esr').pk}/")
        order = self._order()
        response = self._as(self.doctor).post(
            f"/api/lab-orders/{order.pk}/add-tests/", {"test_codes": ["esr"]}, format="json")
        order.refresh_from_db()
        self.assertEqual(order.items.count(), 0, response.data)

    def test_a_restored_test_can_be_ordered_again(self):
        esr = LabTest.objects.get(code="esr")
        self._as(self.scientist).delete(f"/api/lab-tests/{esr.pk}/")
        self._as(self.scientist).post(f"/api/lab-tests/{esr.pk}/restore/")

        order = self._order("esr")
        self.assertEqual([i.test.code for i in order.items.all()], ["esr"])


class ParameterRetireAndRestoreTests(TestCase):
    """A result line retires the same way, and comes back the same way."""

    def setUp(self):
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.client = APIClient()
        self.client.force_authenticate(self.scientist)
        self.parameter = LabParameter.objects.filter(test__code="fbc", is_active=True).first()

    def test_a_retired_parameter_can_be_fetched_and_restored(self):
        self.assertEqual(
            self.client.delete(f"/api/lab-parameters/{self.parameter.pk}/").status_code, 204)
        self.parameter.refresh_from_db()
        self.assertFalse(self.parameter.is_active)

        self.assertEqual(
            self.client.get(f"/api/lab-parameters/{self.parameter.pk}/").status_code, 200)

        response = self.client.post(f"/api/lab-parameters/{self.parameter.pk}/restore/")
        self.assertEqual(response.status_code, 200, response.data)
        self.parameter.refresh_from_db()
        self.assertTrue(self.parameter.is_active)

    def test_a_retired_parameter_drops_off_its_test_but_the_row_remains(self):
        test_id = self.parameter.test_id
        self.client.delete(f"/api/lab-parameters/{self.parameter.pk}/")
        body = self.client.get(f"/api/lab-tests/{test_id}/").data
        self.assertNotIn(self.parameter.pk, {p["id"] for p in body["parameters"]})
        self.assertTrue(LabParameter.objects.filter(pk=self.parameter.pk).exists())
