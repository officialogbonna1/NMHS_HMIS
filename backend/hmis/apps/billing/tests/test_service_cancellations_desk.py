"""
The Service Cancellations desk's view of `/api/charges/`: finding a bill the way
a patient describes themselves at the window, narrowing by date and state,
leading with the services a department withdrew, and the figures heading it.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge
from apps.billing.services import add_charge, cancel_charge, record_payment
from apps.laboratory.models import LabOrder, LabOrderTest, LabTest
from apps.patients.models import Patient

D = Decimal


class DeskList(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.ngozi = Patient.objects.create(first_name="Ngozi", last_name="Eze", sex="F",
                                            phone_number="08031234567", created_by=self.reception)
        self.tunde = Patient.objects.create(first_name="Tunde", last_name="Bakare", sex="M",
                                            phone_number="07059876543", created_by=self.reception)
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

    def charge(self, patient, amount="4000", description="Laboratory: FBC", source="lab_test",
               source_id=None):
        return add_charge(patient=patient, description=description, amount=amount,
                          created_by=self.reception, source_type=source, source_id=source_id)

    def ids(self, **params):
        response = self.client.get("/api/charges/", params)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return [row["id"] for row in response.data["results"]]

    def lab_line(self, patient, status="pending", order_status="requested"):
        order = LabOrder.objects.create(patient=patient, created_by=self.reception,
                                        status=order_status)
        return LabOrderTest.objects.create(order=order, test=LabTest.objects.first(),
                                           status=status, unit_price=D("4000"))


class FindingABill(DeskList):
    def test_by_hospital_number(self):
        mine = self.charge(self.ngozi)
        self.charge(self.tunde)
        self.assertEqual(self.ids(search=self.ngozi.patient_number), [mine.pk])

    def test_by_name(self):
        self.charge(self.ngozi)
        his = self.charge(self.tunde)
        self.assertEqual(self.ids(search="Bakare"), [his.pk])
        self.assertEqual(self.ids(search="tunde"), [his.pk])

    def test_by_phone_number(self):
        self.charge(self.ngozi)
        his = self.charge(self.tunde)
        self.assertEqual(self.ids(search="0705987"), [his.pk])

    def test_by_the_service(self):
        self.charge(self.ngozi)
        scan = self.charge(self.ngozi, description="Ultrasound: Abdominal", source="ultrasound")
        self.assertEqual(self.ids(search="Ultrasound"), [scan.pk])

    def test_by_billed_date_with_the_end_date_inclusive(self):
        old = self.charge(self.ngozi)
        new = self.charge(self.ngozi)
        ten_days_ago = timezone.now() - timedelta(days=10)
        Charge.objects.filter(pk=old.pk).update(created_at=ten_days_ago)
        today = timezone.localdate().isoformat()
        that_day = timezone.localtime(ten_days_ago).date().isoformat()

        self.assertEqual(self.ids(created_from=today), [new.pk])
        self.assertEqual(self.ids(created_to=that_day), [old.pk])
        self.assertEqual(self.ids(created_from=that_day, created_to=that_day), [old.pk])

    def test_a_date_that_is_not_a_date_is_a_400(self):
        response = self.client.get("/api/charges/", {"created_from": "last tuesday"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("created_from", response.data)

    def test_by_payment_state(self):
        unpaid = self.charge(self.ngozi, "1000", "Card", "card")
        partial = self.charge(self.tunde, "4000")
        record_payment(patient=self.tunde, amount="1000", received_by=self.cashier)
        cancelled = self.charge(self.ngozi, "2000", "Consultation", "consultation")
        cancel_charge(charge=cancelled, cancelled_by=self.cashier, reason="Not seen")

        self.assertEqual(self.ids(status="unpaid"), [unpaid.pk])
        self.assertEqual(self.ids(status="partial"), [partial.pk])
        self.assertEqual(self.ids(status="cancelled"), [cancelled.pk])

    def test_a_row_carries_who_and_what_the_desk_needs(self):
        self.charge(self.ngozi)
        row = self.client.get("/api/charges/").data["results"][0]
        self.assertEqual(row["patient_number"], self.ngozi.patient_number)
        self.assertEqual(row["patient_uuid"], str(self.ngozi.uuid))
        self.assertEqual(row["department_name"], "Laboratory")
        self.assertFalse(row["service_withdrawn"])
        self.assertEqual(row["untraceable_amount"], "0.00")


class WithdrawnServices(DeskList):
    def test_what_counts_as_withdrawn_is_read_off_the_service(self):
        still_ordered = self.charge(self.ngozi, source_id=self.lab_line(self.ngozi).pk)
        line_cancelled = self.charge(self.ngozi,
                                     source_id=self.lab_line(self.ngozi, status="cancelled").pk)
        order_cancelled = self.charge(
            self.ngozi, source_id=self.lab_line(self.ngozi, order_status="cancelled").pk)
        deleted = self.lab_line(self.ngozi)
        line_deleted = self.charge(self.ngozi, source_id=deleted.pk)
        deleted.delete()
        typed_at_counter = self.charge(self.ngozi, description="Laboratory: MP", source="laboratory")

        self.assertCountEqual(self.ids(awaiting=1),
                              [line_cancelled.pk, order_cancelled.pk, line_deleted.pk])
        flags = {row["id"]: row["service_withdrawn"]
                 for row in self.client.get("/api/charges/").data["results"]}
        self.assertFalse(flags[still_ordered.pk])
        self.assertFalse(flags[typed_at_counter.pk], "a write-in has no service to read — never guessed")

    def test_a_withdrawn_service_stops_awaiting_once_cancelled(self):
        line = self.lab_line(self.ngozi, status="cancelled")
        charge = self.charge(self.ngozi, source_id=line.pk)
        self.assertEqual(self.ids(awaiting=1), [charge.pk])
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Test removed")
        self.assertEqual(self.ids(awaiting=1), [])

    def test_withdrawn_services_come_first_when_asked(self):
        withdrawn = self.charge(self.ngozi, source_id=self.lab_line(self.ngozi, status="cancelled").pk)
        Charge.objects.filter(pk=withdrawn.pk).update(created_at=timezone.now() - timedelta(days=30))
        newer = self.charge(self.tunde, "1000", "Card", "card")

        self.assertEqual(self.ids(), [newer.pk, withdrawn.pk], "the default order is untouched")
        self.assertEqual(self.ids(awaiting_first=1), [withdrawn.pk, newer.pk])

    def test_list_filters_never_hide_a_charge_from_its_own_detail_route(self):
        """The desk's own filters are list-only (rule 21): a page's query string
        riding along on a detail request must not 404 the charge it names."""
        charge = self.charge(self.ngozi)
        narrowing = {"awaiting": 1, "created_from": "2099-01-01", "search": "nobody by this name"}
        self.assertEqual(self.client.get(f"/api/charges/{charge.pk}/", narrowing).status_code, 200)
        response = self.client.post(
            f"/api/charges/{charge.pk}/cancel/?awaiting=1&created_to=2000-01-01&search=nobody",
            {"reason": "Not run"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)


class TheFiguresHeadingTheDesk(DeskList):
    def test_the_summary(self):
        withdrawn = self.charge(self.ngozi, source_id=self.lab_line(self.ngozi, status="cancelled").pk)
        record_payment(patient=self.ngozi, amount="4000", received_by=self.cashier)
        self.charge(self.tunde, "1000", "Card", "card")
        gone = self.charge(self.tunde, "2500", "Consultation", "consultation")
        cancel_charge(charge=gone, cancelled_by=self.cashier, reason="Not seen")

        data = self.client.get("/api/charges/cancellation-summary/").data
        self.assertEqual(data["awaiting"], {"count": 1, "held": "4000.00"})
        self.assertEqual(data["by_status"]["paid"], 1)
        self.assertEqual(data["by_status"]["unpaid"], 1)
        self.assertEqual(data["by_status"]["cancelled"], 1)
        self.assertEqual(data["open"], 2)
        self.assertEqual(data["cancelled_this_month"], {"count": 1, "value": "2500.00"})
        withdrawn.refresh_from_db()
        self.assertEqual(withdrawn.status, "paid")

    def test_neither_the_summary_nor_the_list_costs_a_query_per_row(self):
        def cost(url, params=None):
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get(url, params or {}).status_code, 200)
            return len(captured.captured_queries)

        self.charge(self.ngozi, source_id=self.lab_line(self.ngozi, status="cancelled").pk)
        params = {"awaiting_first": 1, "search": "Laboratory"}
        small = (cost("/api/charges/cancellation-summary/"), cost("/api/charges/", params))
        for index in range(12):
            self.charge(self.tunde, "500", f"Laboratory: item {index}",
                        source_id=self.lab_line(self.tunde, status="cancelled").pk)
        self.assertEqual(small, (cost("/api/charges/cancellation-summary/"),
                                 cost("/api/charges/", params)))
