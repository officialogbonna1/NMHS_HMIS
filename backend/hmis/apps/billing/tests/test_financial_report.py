"""
The financial report: what it counts as revenue, what it refuses to, and the
identity it has to keep.

Two things this file exists to hold.

**A discount is not money.** The single easiest way to overstate a hospital's
takings is to let a write-off through the revenue figure. Total Revenue here
is `Payment` rows in the period and nothing else — a waived bill and a
discounted one both raise the collected figure by zero.

**The reconciliation closes.** On the charges raised in a period:

    gross − discounts − waivers            = net due
    net due − collected against those bills = outstanding

It only closes on one cohort, which is why the report keeps the cash figure
and the charge figures in separate blocks rather than subtracting one from the
other. `test_the_reconciliation_closes_after_every_kind_of_settlement` is the
one that fails loudest if somebody blends them.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import reporting
from apps.billing.models import Charge, Payment, PaymentAllocation
from apps.billing.services import (add_charge, apply_percentage_discount, record_payment,
                                   waive_charge)
from apps.patients.models import Patient


def _backdate(instance, days):
    """Move a row into the past. `created_at` is auto_now_add, so it takes an update()."""
    when = timezone.now() - timedelta(days=days)
    type(instance).objects.filter(pk=instance.pk).update(created_at=when)
    instance.refresh_from_db()
    return instance


class FinancialReportTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.other = Patient.objects.create(first_name="Bola", last_name="Eze", sex="M",
                                            created_by=self.reception)
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.today = reporting.resolve_period("today")

    def bill(self, amount, *, source_type="card", patient=None, description="Card"):
        return add_charge(patient=patient or self.patient, description=description,
                          amount=Decimal(amount), created_by=self.cashier,
                          source_type=source_type)

    # -- what counts as revenue --------------------------------------------

    def test_total_revenue_is_payments_received_and_nothing_else(self):
        self.bill("5000", source_type="consultation")
        record_payment(patient=self.patient, amount="2000", received_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        self.assertEqual(report["collections"]["total"], Decimal("2000.00"))
        self.assertEqual(report["collections"]["transactions"], 1)
        self.assertEqual(report["charges"]["gross"], Decimal("5000.00"))

    def test_a_discount_is_not_counted_as_money_received(self):
        charge = self.bill("4000", source_type="laboratory")
        apply_percentage_discount(charge=charge, percent="25", reason="Staff",
                                  approved_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        self.assertEqual(report["collections"]["total"], Decimal("0.00"))
        self.assertEqual(report["charges"]["discounts"], Decimal("1000.00"))
        self.assertEqual(report["charges"]["net_due"], Decimal("3000.00"))
        self.assertEqual(report["charges"]["outstanding"], Decimal("3000.00"))

    def test_a_waiver_is_not_counted_as_money_received(self):
        charge = self.bill("3000", source_type="procedure")
        waive_charge(charge=charge, reason="Hardship", approved_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        self.assertEqual(report["collections"]["total"], Decimal("0.00"))
        self.assertEqual(report["charges"]["waivers"], Decimal("3000.00"))
        self.assertEqual(report["charges"]["net_due"], Decimal("0.00"))
        self.assertEqual(report["charges"]["outstanding"], Decimal("0.00"))

    def test_a_part_payment_is_collected_money_and_is_named_as_part_paid(self):
        self.bill("10000", source_type="laboratory")
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        # The money is real, so it is in the takings...
        self.assertEqual(report["collections"]["total"], Decimal("4000.00"))
        # ...and separately identified, because the bill is still open.
        self.assertEqual(report["collections"]["part_payments"], Decimal("4000.00"))
        self.assertEqual(report["collections"]["part_paid_charges"], 1)
        self.assertEqual(report["charges"]["outstanding"], Decimal("6000.00"))

    def test_settling_in_full_stops_the_money_reading_as_a_part_payment(self):
        self.bill("10000", source_type="laboratory")
        record_payment(patient=self.patient, amount="10000", received_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        self.assertEqual(report["collections"]["total"], Decimal("10000.00"))
        self.assertEqual(report["collections"]["part_payments"], Decimal("0.00"))
        self.assertEqual(report["charges"]["outstanding"], Decimal("0.00"))

    # -- the reconciliation -------------------------------------------------

    def test_the_reconciliation_closes_after_every_kind_of_settlement(self):
        paid = self.bill("5000", source_type="card")
        discounted = self.bill("4000", source_type="laboratory")
        waived = self.bill("3000", source_type="consultation")
        self.bill("2000", source_type="ultrasound")          # untouched
        self.bill("6000", source_type="prescription", patient=self.other)

        apply_percentage_discount(charge=discounted, percent="50", reason="Staff",
                                  approved_by=self.cashier)
        waive_charge(charge=waived, amount="1000", reason="Hardship", approved_by=self.cashier)
        record_payment(patient=self.patient, amount="5000", received_by=self.cashier)
        record_payment(patient=self.other, amount="1500", received_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        charges = report["charges"]

        self.assertEqual(charges["gross"], Decimal("20000.00"))
        self.assertEqual(charges["discounts"], Decimal("2000.00"))
        self.assertEqual(charges["waivers"], Decimal("1000.00"))
        self.assertEqual(charges["net_due"], Decimal("17000.00"))
        self.assertEqual(charges["collected"], Decimal("6500.00"))
        self.assertEqual(charges["outstanding"], Decimal("10500.00"))

        self.assertEqual(charges["gross"] - charges["discounts"] - charges["waivers"],
                         charges["net_due"])
        self.assertEqual(charges["net_due"] - charges["collected"], charges["outstanding"])
        self.assertTrue(report["reconciliation"]["balances"])

    def test_every_department_row_reconciles_and_the_total_row_is_their_sum(self):
        self.bill("5000", source_type="card")
        lab = self.bill("4000", source_type="lab_test")
        self.bill("2000", source_type="prescription")
        apply_percentage_discount(charge=lab, percent="25", reason="Staff",
                                  approved_by=self.cashier)
        record_payment(patient=self.patient, amount="6000", received_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        rows = report["departments"]
        total = report["totals"]

        for row in rows:
            self.assertEqual(row["gross"] - row["discounts"] - row["waivers"], row["net_due"],
                             f"{row['label']} net due does not follow from its own figures")
            self.assertEqual(row["net_due"] - row["collected"], row["outstanding"],
                             f"{row['label']} outstanding does not follow from its own figures")

        for field in ("gross", "discounts", "waivers", "net_due", "collected", "outstanding"):
            self.assertEqual(sum((row[field] for row in rows), Decimal("0")), total[field],
                             f"the Total row disagrees with the departments on {field}")

    # -- departments --------------------------------------------------------

    def test_a_department_comes_from_the_charge_not_from_a_guess(self):
        self.bill("5000", source_type="card")
        self.bill("4000", source_type="lab_test", description="Laboratory: FBC")
        self.bill("2000", source_type="prescription", description="Medication: Paracetamol")
        self.bill("7000", source_type="ultrasound")
        self.bill("1500", source_type="eye")
        self.bill("9000", source_type="procedure")
        self.bill("800", source_type="consultation")

        by_key = {row["key"]: row for row in reporting.financial_report(period=self.today)["departments"]}
        self.assertEqual(by_key["reception"]["gross"], Decimal("5000.00"))
        self.assertEqual(by_key["laboratory"]["gross"], Decimal("4000.00"))
        self.assertEqual(by_key["pharmacy"]["gross"], Decimal("2000.00"))
        self.assertEqual(by_key["radiology"]["gross"], Decimal("7000.00"))
        self.assertEqual(by_key["eye"]["gross"], Decimal("1500.00"))
        self.assertEqual(by_key["theatre"]["gross"], Decimal("9000.00"))
        self.assertEqual(by_key["consultation"]["gross"], Decimal("800.00"))

    def test_a_charge_with_no_source_type_is_shown_rather_than_dropped(self):
        self.bill("1200", source_type="")
        report = reporting.financial_report(period=self.today)
        by_key = {row["key"]: row for row in report["departments"]}
        self.assertEqual(by_key["other"]["gross"], Decimal("1200.00"))
        self.assertEqual(report["totals"]["gross"], Decimal("1200.00"))

    def test_department_revenue_follows_the_money_to_the_charge_it_settled(self):
        """
        Cash collected is attributed through the allocation, so paying an old
        laboratory bill today is laboratory revenue today — not reception's,
        and not last month's.
        """
        lab = self.bill("4000", source_type="lab_test")
        _backdate(lab, 40)
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        by_key = {row["key"]: row for row in report["departments"]}
        self.assertEqual(by_key["laboratory"]["received"], Decimal("4000.00"))
        # The bill itself belongs to the month it was raised, so this period's
        # charge cohort is empty even though its cash is not.
        self.assertEqual(report["charges"]["gross"], Decimal("0.00"))
        self.assertEqual(report["collections"]["total"], Decimal("4000.00"))

    def test_money_with_no_charge_to_point_at_is_shown_as_unattributed(self):
        """
        Payments taken before allocations were recorded have no charge behind
        them. Dropping them would leave the chart's bars short of the headline
        revenue figure with nothing saying why, so the difference gets a row
        and a name.
        """
        self.bill("5000", source_type="card")
        payment = record_payment(patient=self.patient, amount="5000", received_by=self.cashier)
        # Stand in for history: the money is there, the allocation is not.
        PaymentAllocation.objects.filter(payment=payment).delete()

        report = reporting.financial_report(period=self.today)
        by_key = {row["key"]: row for row in report["departments"]}
        self.assertEqual(by_key["unattributed"]["received"], Decimal("5000.00"))
        self.assertEqual(sum((row["received"] for row in report["departments"]), Decimal("0")),
                         report["collections"]["total"])
        # It is cash with no bill behind it, so it adds nothing to any charge column.
        self.assertEqual(by_key["unattributed"]["gross"], Decimal("0.00"))
        self.assertEqual(by_key["unattributed"]["net_due"], Decimal("0.00"))

    def test_nothing_is_unattributed_when_every_payment_allocates(self):
        self.bill("5000", source_type="card")
        record_payment(patient=self.patient, amount="5000", received_by=self.cashier)
        keys = {row["key"] for row in reporting.financial_report(period=self.today)["departments"]}
        self.assertNotIn("unattributed", keys)

    def test_the_chart_figures_add_up_to_the_money_actually_collected(self):
        self.bill("5000", source_type="card")
        self.bill("4000", source_type="lab_test")
        record_payment(patient=self.patient, amount="7000", received_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        self.assertEqual(sum((row["received"] for row in report["departments"]), Decimal("0")),
                         report["collections"]["total"])

    # -- payment methods ----------------------------------------------------

    def test_the_method_breakdown_reconciles_with_the_collected_total(self):
        self.bill("10000", source_type="card")
        record_payment(patient=self.patient, amount="3000", received_by=self.cashier, method="cash")
        record_payment(patient=self.patient, amount="2500", received_by=self.cashier, method="card")
        record_payment(patient=self.patient, amount="1500", received_by=self.cashier, method="transfer")

        report = reporting.financial_report(period=self.today)
        methods = {row["key"]: row["amount"] for row in report["collections"]["by_method"]}
        self.assertEqual(methods["cash"], Decimal("3000.00"))
        self.assertEqual(methods["card"], Decimal("2500.00"))
        self.assertEqual(methods["transfer"], Decimal("1500.00"))
        self.assertEqual(sum(methods.values()), report["collections"]["total"])

    # -- the write-off register vs the charge cohort ------------------------

    def test_an_adjustment_with_no_charge_behind_it_is_reported_as_unlinked(self):
        """
        An unlinked adjustment credits a patient's ledger without touching any
        `amount_discounted`, so no charge column and no department column can
        ever show it. Reporting the amount is what stops the register and the
        cohort disagreeing with nobody able to say why.

        The generic endpoint now refuses every way of raising one — an unlinked
        discount or waiver, and any refund at all (see
        `test_adjustment_validation.py`) — so such a row is history. It is
        written straight to the model here, the way the rows already in the
        database were; the model still allows it, so history stays readable
        and keeps being reported exactly like this.
        """
        from apps.billing.models import Adjustment

        self.bill("5000", source_type="card")
        Adjustment.objects.create(patient=self.patient, kind="refund", amount=Decimal("500"),
                                  reason="Overpaid at the counter", approved_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        self.assertEqual(report["adjustments"]["refunds"], Decimal("500.00"))
        self.assertEqual(report["adjustments"]["unlinked"], Decimal("500.00"))
        self.assertEqual(report["adjustments"]["unlinked_count"], 1)
        # It never reaches a charge, so the cohort and its reconciliation are
        # untouched by it.
        self.assertEqual(report["charges"]["discounts"], Decimal("0.00"))
        self.assertTrue(report["reconciliation"]["balances"])

    def test_a_historic_unlinked_discount_is_still_reported(self):
        """
        The rule is a serializer rule, so rows written before it existed stay
        readable and keep appearing in the register — including as `unlinked`.
        Nothing rewrites them.
        """
        from apps.billing.models import Adjustment

        self.bill("5000", source_type="card")
        Adjustment.objects.create(patient=self.patient, kind="discount",
                                  amount=Decimal("500"), reason="Written before the rule",
                                  approved_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        self.assertEqual(report["adjustments"]["discounts"], Decimal("500.00"))
        self.assertEqual(report["adjustments"]["unlinked"], Decimal("500.00"))
        self.assertEqual(report["charges"]["discounts"], Decimal("0.00"))

    def test_a_discount_through_the_service_reaches_both_the_register_and_the_charge(self):
        charge = self.bill("4000", source_type="lab_test")
        apply_percentage_discount(charge=charge, percent="25", reason="Staff",
                                  approved_by=self.cashier)
        report = reporting.financial_report(period=self.today)
        self.assertEqual(report["adjustments"]["discounts"], Decimal("1000.00"))
        self.assertEqual(report["charges"]["discounts"], Decimal("1000.00"))
        self.assertEqual(report["adjustments"]["unlinked"], Decimal("0.00"))

    def test_a_write_off_granted_on_an_older_bill_counts_by_its_approval_date(self):
        charge = self.bill("6000", source_type="card")
        _backdate(charge, 40)
        waive_charge(charge=charge, amount="2000", reason="Hardship", approved_by=self.cashier)

        report = reporting.financial_report(period=self.today)
        # Approved today...
        self.assertEqual(report["adjustments"]["waivers"], Decimal("2000.00"))
        # ...against a bill raised last month, so this period's cohort is nil.
        self.assertEqual(report["charges"]["waivers"], Decimal("0.00"))
        self.assertEqual(report["charges"]["gross"], Decimal("0.00"))

    # -- the date range -----------------------------------------------------

    def test_each_preset_asks_a_different_question_of_the_same_rows(self):
        today_charge = self.bill("1000", source_type="card")
        record_payment(patient=self.patient, amount="1000", received_by=self.cashier)

        old = self.bill("8000", source_type="card", patient=self.other)
        _backdate(old, 40)

        self.assertEqual(reporting.financial_report(
            period=reporting.resolve_period("today"))["charges"]["gross"], Decimal("1000.00"))
        year = reporting.financial_report(period=reporting.resolve_period("year"))["charges"]["gross"]
        self.assertIn(year, (Decimal("9000.00"), Decimal("1000.00")))  # 40 days back may cross the year

    def test_yesterday_excludes_today(self):
        self.bill("1000", source_type="card")
        report = reporting.financial_report(period=reporting.resolve_period("yesterday"))
        self.assertEqual(report["charges"]["gross"], Decimal("0.00"))
        self.assertEqual(report["collections"]["total"], Decimal("0.00"))

    def test_a_custom_range_covers_both_of_its_end_days(self):
        old = self.bill("2000", source_type="card")
        _backdate(old, 5)
        day = (timezone.localdate() - timedelta(days=5)).isoformat()
        period = reporting.resolve_period("custom", day, day)
        self.assertEqual(reporting.financial_report(period=period)["charges"]["gross"],
                         Decimal("2000.00"))

    def test_a_custom_range_needs_both_dates(self):
        with self.assertRaises(ValueError):
            reporting.resolve_period("custom", "2026-01-01", None)

    def test_an_unknown_preset_narrows_to_today_rather_than_widening(self):
        period = reporting.resolve_period("everything")
        self.assertEqual(period.preset, "today")
        self.assertEqual(period.start, timezone.localdate())

    # -- cancelled charges --------------------------------------------------

    def test_a_cancelled_charge_is_out_of_the_report_as_it_is_out_of_the_ledger(self):
        charge = self.bill("5000", source_type="card")
        charge.status = "cancelled"
        charge.save(update_fields=["status"])
        report = reporting.financial_report(period=self.today)
        self.assertEqual(report["charges"]["gross"], Decimal("0.00"))
        self.assertEqual(report["charges"]["count"], 0)

    # -- allocations --------------------------------------------------------

    def test_an_allocation_is_written_for_every_charge_a_payment_settles(self):
        first = self.bill("1000", source_type="card")
        second = self.bill("4000", source_type="lab_test")
        payment = record_payment(patient=self.patient, amount="2500", received_by=self.cashier)

        allocations = {a.charge_id: a.amount for a in payment.allocations.all()}
        self.assertEqual(allocations[first.pk], Decimal("1000.00"))
        self.assertEqual(allocations[second.pk], Decimal("1500.00"))
        self.assertEqual(sum(allocations.values()), payment.amount)

    def test_allocations_never_exceed_the_charge_they_settle(self):
        charge = self.bill("3000", source_type="card")
        record_payment(patient=self.patient, amount="1000", received_by=self.cashier)
        record_payment(patient=self.patient, amount="2000", received_by=self.cashier)
        charge.refresh_from_db()
        allocated = sum((a.amount for a in charge.allocations.all()), Decimal("0"))
        self.assertEqual(allocated, charge.amount_paid)


class FinancialReportEndpointTests(TestCase):
    """Who may read it, what it does not leak, and how many queries it costs."""

    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        add_charge(patient=self.patient, description="Card", amount=Decimal("5000"),
                   created_by=self.cashier, source_type="card")
        record_payment(patient=self.patient, amount="2000", received_by=self.cashier)
        self.client = APIClient()

    def get(self, user, **params):
        self.client.force_authenticate(user)
        return self.client.get("/api/finance/report/", params)

    def test_the_cash_desk_and_the_accounts_office_read_it(self):
        self.assertEqual(self.get(self.cashier).status_code, 200)
        self.assertEqual(self.get(self.admin).status_code, 200)

    def test_reception_and_the_ward_do_not(self):
        self.assertEqual(self.get(self.reception).status_code, 403)
        self.assertEqual(self.get(self.doctor).status_code, 403)

    def test_it_is_closed_to_an_anonymous_caller(self):
        self.assertIn(APIClient().get("/api/finance/report/").status_code, (401, 403))

    def test_a_transaction_row_identifies_a_patient_without_a_primary_key(self):
        row = self.get(self.cashier).data["transactions"][0]
        self.assertEqual(row["patient_number"], self.patient.patient_number)
        self.assertEqual(row["patient_uuid"], str(self.patient.uuid))
        self.assertNotIn("patient", [key for key in row if key == "patient_id"])
        self.assertNotIn("patient_id", row)

    def test_a_cashier_sees_what_their_own_desk_took(self):
        data = self.get(self.cashier).data
        self.assertEqual(data["collections"]["my_desk"], Decimal("2000.00"))
        self.assertEqual(data["collections"]["my_desk_transactions"], 1)
        # The admin did not take it, so their desk figure is nil while the
        # hospital's is not.
        admin = self.get(self.admin).data
        self.assertEqual(admin["collections"]["my_desk"], Decimal("0.00"))
        self.assertEqual(admin["collections"]["total"], Decimal("2000.00"))

    def test_a_bad_custom_range_is_a_400_not_a_silent_whole_history(self):
        response = self.get(self.cashier, preset="custom", **{"from": "not-a-date", "to": "2026-01-01"})
        self.assertEqual(response.status_code, 400)

    def test_the_cost_does_not_grow_with_the_transaction_history(self):
        """
        A dashboard that costs one query per charge is a dashboard that dies
        the first busy month. Both runs below must take the same number of
        queries — the aggregation is in the database, and the transaction
        table is prefetched rather than walked.
        """
        small = self._count_queries()

        for index in range(25):
            patient = Patient.objects.create(first_name=f"P{index}", last_name="Test", sex="F",
                                             created_by=self.reception)
            add_charge(patient=patient, description="Laboratory: FBC", amount=Decimal("3500"),
                       created_by=self.cashier, source_type="lab_test")
            record_payment(patient=patient, amount="1500", received_by=self.cashier,
                           method="cash" if index % 2 else "transfer")

        large = self._count_queries()
        self.assertEqual(small, large,
                         f"the report cost {small} queries on 1 charge and {large} on 26")

    def _count_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.force_authenticate(self.cashier)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/api/finance/report/")
            self.assertEqual(response.status_code, 200)
        return len(captured.captured_queries)
