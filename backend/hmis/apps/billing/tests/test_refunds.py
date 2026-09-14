"""
Refunds: money handed back, recorded as a transaction of its own.

The rule the whole file exists to hold — **the original payment is never
touched**. It stays in the database exactly as written, and the refund sits
beside it:

    Payment   +10,000
    Refund     −3,000
    Net          7,000

Anything that made the payment read as though it had never happened would be a
lie the audit could not detect.
"""
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import (Adjustment, Charge, PatientLedger, Payment,
                                 PaymentAllocation, Refund, RefundAllocation)
from apps.billing.services import add_charge, record_payment, refund_payment
from apps.core.models import AuditLog, Notification
from apps.patients.models import Patient


class RefundServiceTests(TestCase):
    """The accounting, at the service — where the rules actually live."""

    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.charge = add_charge(patient=self.patient, description="Consultation fee",
                                 amount="10000", created_by=self.reception,
                                 source_type="consultation")
        self.payment = record_payment(patient=self.patient, amount="10000",
                                      received_by=self.cashier)

    def _ledger(self):
        return PatientLedger.objects.get(patient=self.patient)

    # --- the headline behaviour -------------------------------------------

    def test_the_original_payment_survives_a_refund_untouched(self):
        before = Payment.objects.get(pk=self.payment.pk)
        refund_payment(payment=self.payment, amount="3000", reason="Overcharged",
                       processed_by=self.cashier)
        after = Payment.objects.get(pk=self.payment.pk)
        self.assertEqual(after.amount, before.amount)
        self.assertEqual(after.method, before.method)
        self.assertEqual(after.received_by_id, before.received_by_id)
        self.assertEqual(after.created_at, before.created_at)
        self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())

    def test_payment_plus_refund_leaves_the_net_the_hospital_kept(self):
        refund_payment(payment=self.payment, amount="3000", reason="Overcharged",
                       processed_by=self.cashier)
        gross = Payment.objects.filter(patient=self.patient).aggregate(v=Sum("amount"))["v"]
        refunded = Refund.objects.filter(patient=self.patient).aggregate(v=Sum("amount"))["v"]
        self.assertEqual(gross, Decimal("10000.00"))
        self.assertEqual(refunded, Decimal("3000.00"))
        self.assertEqual(gross - refunded, Decimal("7000.00"))

    def test_a_refund_records_everything_an_audit_asks_for(self):
        refund = refund_payment(payment=self.payment, amount="3000", reason="Billed twice",
                                processed_by=self.cashier)
        self.assertEqual(refund.payment_id, self.payment.pk)
        self.assertEqual(refund.patient_id, self.patient.pk)
        self.assertEqual(refund.amount, Decimal("3000.00"))
        self.assertEqual(refund.reason, "Billed twice")
        self.assertEqual(refund.processed_by, self.cashier)
        # No separate approver in this workflow: the person who processed it
        # authorised it, and the column says so rather than being left blank.
        self.assertEqual(refund.authorized_by, self.cashier)
        self.assertIsNotNone(refund.created_at)
        self.assertLessEqual(refund.created_at, timezone.now())

    def test_a_refund_names_the_charge_the_money_came_back_off(self):
        refund = refund_payment(payment=self.payment, amount="3000", reason="Overcharged",
                                processed_by=self.cashier)
        allocation = RefundAllocation.objects.get(refund=refund)
        self.assertEqual(allocation.charge_id, self.charge.pk)
        self.assertEqual(allocation.amount, Decimal("3000.00"))

    def test_a_refund_writes_the_adjustment_the_ledger_already_reads(self):
        refund = refund_payment(payment=self.payment, amount="3000", reason="Overcharged",
                                processed_by=self.cashier)
        self.assertIsNotNone(refund.adjustment)
        self.assertEqual(refund.adjustment.kind, "refund")
        self.assertEqual(refund.adjustment.amount, Decimal("3000.00"))
        self.assertEqual(refund.adjustment.charge_id, self.charge.pk)

    def test_a_refund_puts_the_money_back_on_the_patient_balance(self):
        self.assertEqual(self._ledger().outstanding_balance, Decimal("0.00"))
        refund_payment(payment=self.payment, amount="3000", reason="Overcharged",
                       processed_by=self.cashier)
        self.assertEqual(self._ledger().outstanding_balance, Decimal("3000.00"))
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount_paid, Decimal("7000.00"))
        self.assertEqual(self.charge.balance, Decimal("3000.00"))
        self.assertEqual(self.charge.settlement_status, "partial")
        # And the face value of the bill is not rewritten (rule 25).
        self.assertEqual(self.charge.amount, Decimal("10000.00"))

    def test_a_full_refund_reopens_the_bill_completely(self):
        refund_payment(payment=self.payment, amount="10000", reason="Wrong patient",
                       processed_by=self.cashier)
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount_paid, Decimal("0.00"))
        self.assertEqual(self.charge.status, "unpaid")
        self.assertEqual(self._ledger().outstanding_balance, Decimal("10000.00"))

    def test_the_payment_allocation_of_the_original_payment_is_left_alone(self):
        """The allocation is the record that the money did settle that bill."""
        refund_payment(payment=self.payment, amount="3000", reason="Overcharged",
                       processed_by=self.cashier)
        allocation = PaymentAllocation.objects.get(payment=self.payment)
        self.assertEqual(allocation.amount, Decimal("10000.00"))
        self.assertEqual(allocation.charge_id, self.charge.pk)

    # --- partial refunds ---------------------------------------------------

    def test_the_refundable_balance_falls_as_it_is_refunded(self):
        self.assertEqual(self.payment.refundable_balance, Decimal("10000.00"))
        refund_payment(payment=self.payment, amount="3000", reason="a",
                       processed_by=self.cashier)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount_refunded, Decimal("3000.00"))
        self.assertEqual(self.payment.refundable_balance, Decimal("7000.00"))
        self.assertFalse(self.payment.is_fully_refunded)

    def test_several_partial_refunds_add_up_and_then_stop(self):
        for amount in ("3000", "4000", "3000"):
            refund_payment(payment=self.payment, amount=amount, reason="in stages",
                           processed_by=self.cashier)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount_refunded, Decimal("10000.00"))
        self.assertTrue(self.payment.is_fully_refunded)
        self.assertEqual(Refund.objects.filter(payment=self.payment).count(), 3)
        with self.assertRaises(ValueError):
            refund_payment(payment=self.payment, amount="1", reason="one more",
                           processed_by=self.cashier)

    def test_a_refund_beyond_the_refundable_balance_is_refused(self):
        refund_payment(payment=self.payment, amount="7000", reason="most of it",
                       processed_by=self.cashier)
        with self.assertRaises(ValueError) as caught:
            refund_payment(payment=self.payment, amount="4000", reason="too much",
                           processed_by=self.cashier)
        self.assertIn("3000", str(caught.exception))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount_refunded, Decimal("7000.00"))

    def test_refunding_more_than_the_payment_is_refused_outright(self):
        with self.assertRaises(ValueError):
            refund_payment(payment=self.payment, amount="10001", reason="over",
                           processed_by=self.cashier)
        self.assertFalse(Refund.objects.exists())

    def test_a_fully_refunded_payment_cannot_be_refunded_again(self):
        refund_payment(payment=self.payment, amount="10000", reason="all of it",
                       processed_by=self.cashier)
        with self.assertRaises(ValueError) as caught:
            refund_payment(payment=self.payment, amount="500", reason="again",
                           processed_by=self.cashier)
        self.assertIn("already been refunded in full", str(caught.exception))

    # --- invalid input -----------------------------------------------------

    def test_zero_and_negative_amounts_are_refused(self):
        for amount in ("0", "-500", "0.00"):
            with self.subTest(amount):
                with self.assertRaises(ValueError):
                    refund_payment(payment=self.payment, amount=amount, reason="nope",
                                   processed_by=self.cashier)
        self.assertFalse(Refund.objects.exists())

    def test_a_refund_without_a_reason_is_refused(self):
        for reason in ("", "   ", None):
            with self.subTest(repr(reason)):
                with self.assertRaises(ValueError) as caught:
                    refund_payment(payment=self.payment, amount="1000", reason=reason,
                                   processed_by=self.cashier)
                self.assertIn("reason", str(caught.exception).lower())

    def test_a_refused_refund_leaves_nothing_behind(self):
        try:
            refund_payment(payment=self.payment, amount="99999", reason="over",
                           processed_by=self.cashier)
        except ValueError:
            pass
        self.assertFalse(Refund.objects.exists())
        self.assertFalse(RefundAllocation.objects.exists())
        self.assertFalse(Adjustment.objects.filter(kind="refund").exists())
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.amount_paid, Decimal("10000.00"))

    # --- several bills -----------------------------------------------------

    def test_a_refund_comes_off_the_most_recently_settled_bill_first(self):
        card = add_charge(patient=self.patient, description="Card", amount="500",
                          created_by=self.reception, source_type="card")
        lab = add_charge(patient=self.patient, description="Laboratory: FBC", amount="3500",
                         created_by=self.reception, source_type="lab_test")
        payment = record_payment(patient=self.patient, amount="4000", received_by=self.cashier)

        refund = refund_payment(payment=payment, amount="3500", reason="Test not run",
                                processed_by=self.cashier)
        by_charge = {a.charge_id: a.amount for a in refund.allocations.all()}
        self.assertEqual(by_charge.get(lab.pk), Decimal("3500.00"))
        self.assertNotIn(card.pk, by_charge)
        card.refresh_from_db(); lab.refresh_from_db()
        self.assertEqual(card.amount_paid, Decimal("500.00"))
        self.assertEqual(lab.amount_paid, Decimal("0.00"))

    def test_a_refund_spanning_two_bills_names_neither_on_the_adjustment(self):
        add_charge(patient=self.patient, description="Card", amount="500",
                   created_by=self.reception, source_type="card")
        add_charge(patient=self.patient, description="Laboratory: FBC", amount="3500",
                   created_by=self.reception, source_type="lab_test")
        payment = record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        refund = refund_payment(payment=payment, amount="4000", reason="Wrong patient",
                                processed_by=self.cashier)
        self.assertEqual(refund.allocations.count(), 2)
        # Naming one of two would be a claim the allocations contradict.
        self.assertIsNone(refund.adjustment.charge_id)


class RefundEndpointTests(TestCase):
    """`POST /api/payments/<id>/refund/` — who may, and what it answers."""

    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.accountant = User.objects.create_user(username="acc", password="t", role="accountant")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        add_charge(patient=self.patient, description="Consultation fee", amount="10000",
                   created_by=self.reception, source_type="consultation")
        self.payment = record_payment(patient=self.patient, amount="10000",
                                      received_by=self.cashier)
        self.client = APIClient()

    def _refund(self, user, **body):
        self.client.force_authenticate(user)
        return self.client.post(f"/api/payments/{self.payment.pk}/refund/",
                                {"amount": "3000", "reason": "Overcharged", **body},
                                format="json")

    # --- authorisation -----------------------------------------------------

    def test_the_cash_desk_and_accounts_and_admin_may_refund(self):
        for user in (self.cashier, self.accountant, self.admin):
            with self.subTest(user.role):
                Refund.objects.all().delete()
                self.assertEqual(self._refund(user).status_code, 201)

    def test_reception_the_pharmacy_and_the_wards_may_not(self):
        for user in (self.reception, self.pharmacist, self.doctor, self.nurse):
            with self.subTest(user.role):
                self.assertEqual(self._refund(user).status_code, 403)
        self.assertFalse(Refund.objects.exists())

    def test_an_anonymous_caller_may_not(self):
        client = APIClient()
        response = client.post(f"/api/payments/{self.payment.pk}/refund/",
                               {"amount": "1000", "reason": "x"}, format="json")
        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(Refund.objects.exists())

    # --- what it answers ---------------------------------------------------

    def test_a_successful_refund_answers_with_the_refund_and_the_payment(self):
        data = self._refund(self.cashier).data
        self.assertEqual(Decimal(data["refund"]["amount"]), Decimal("3000.00"))
        self.assertEqual(data["refund"]["patient_number"], self.patient.patient_number)
        self.assertEqual(data["refund"]["processed_by_name"], "cash")
        self.assertEqual(len(data["refund"]["allocations"]), 1)
        # The payment comes back with what is still refundable, not a stale
        # figure from before the refund.
        self.assertEqual(Decimal(data["payment"]["amount"]), Decimal("10000.00"))
        self.assertEqual(Decimal(data["payment"]["refundable_balance"]), Decimal("7000.00"))
        self.assertEqual(Decimal(data["payment"]["amount_refunded"]), Decimal("3000.00"))

    def test_a_missing_reason_is_a_400(self):
        response = self._refund(self.cashier, reason="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("reason", response.data)
        self.assertFalse(Refund.objects.exists())

    def test_an_invalid_amount_is_a_400(self):
        for amount in ("0", "-100", "abc"):
            with self.subTest(amount):
                self.assertEqual(self._refund(self.cashier, amount=amount).status_code, 400)
        self.assertFalse(Refund.objects.exists())

    def test_an_over_refund_is_a_400_naming_what_is_left(self):
        self._refund(self.cashier, amount="9000")
        response = self._refund(self.cashier, amount="5000")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "refund_refused")
        self.assertIn("1000", response.data["detail"])

    def test_a_refund_is_audited(self):
        self._refund(self.cashier)
        entry = AuditLog.objects.filter(action="billing.refund").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.cashier)
        self.assertEqual(entry.details["amount"], "3000.00")

    def test_a_refund_notifies_the_money_desks(self):
        self._refund(self.cashier)
        note = Notification.objects.filter(recipient=self.accountant, category="billing").first()
        self.assertIsNotNone(note)
        self.assertIn("REFUND", note.title)

    def test_the_register_is_readable_and_never_writable(self):
        self._refund(self.cashier)
        self.client.force_authenticate(self.reception)
        listing = self.client.get("/api/refunds/")
        self.assertEqual(listing.status_code, 200)
        rows = listing.data.get("results", listing.data)
        self.assertEqual(len(rows), 1)
        # Reception reads the register so a statement adds up, but cannot post
        # to it — a refund is made against a payment, through the service.
        self.assertIn(self.client.post("/api/refunds/", {}, format="json").status_code,
                      (403, 405))


class RefundReportingTests(TestCase):
    """Refunds in the financial report — their own category, never revenue."""

    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

    def _report(self, **params):
        response = self.client.get("/api/finance/report/", params)
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_gross_less_refunds_is_the_net_the_hospital_retained(self):
        add_charge(patient=self.patient, description="Consultation fee", amount="10000",
                   created_by=self.reception, source_type="consultation")
        payment = record_payment(patient=self.patient, amount="10000", received_by=self.cashier)
        refund_payment(payment=payment, amount="3000", reason="Overcharged",
                       processed_by=self.cashier)

        report = self._report()
        collections = report["collections"]
        # The gross the drawer took is unchanged — the payment is still there.
        self.assertEqual(collections["total"], Decimal("10000.00"))
        self.assertEqual(collections["refunds"], Decimal("3000.00"))
        self.assertEqual(collections["refunds_count"], 1)
        self.assertEqual(collections["net"], Decimal("7000.00"))
        self.assertEqual(report["refunds"]["net_revenue"], Decimal("7000.00"))
        self.assertEqual(report["reconciliation"]["gross_collected"], Decimal("10000.00"))
        self.assertEqual(report["reconciliation"]["less_refunds"], Decimal("3000.00"))
        self.assertEqual(report["reconciliation"]["net_collected"], Decimal("7000.00"))

    def test_a_refund_is_never_counted_as_revenue(self):
        add_charge(patient=self.patient, description="Card", amount="5000",
                   created_by=self.reception, source_type="card")
        payment = record_payment(patient=self.patient, amount="5000", received_by=self.cashier)
        before = self._report()["collections"]["total"]
        refund_payment(payment=payment, amount="5000", reason="Wrong patient",
                       processed_by=self.cashier)
        after = self._report()["collections"]
        self.assertEqual(after["total"], before, "a refund must not change gross takings")
        self.assertEqual(after["net"], Decimal("0.00"))

    def test_a_refund_is_attributed_to_the_department_that_took_the_money(self):
        add_charge(patient=self.patient, description="Laboratory: FBC", amount="3500",
                   created_by=self.reception, source_type="lab_test")
        payment = record_payment(patient=self.patient, amount="3500", received_by=self.cashier)
        refund_payment(payment=payment, amount="1500", reason="Sample rejected",
                       processed_by=self.cashier)

        rows = {row["key"]: row for row in self._report()["departments"]}
        lab = rows["laboratory"]
        self.assertEqual(lab["received"], Decimal("3500.00"))
        self.assertEqual(lab["refunded"], Decimal("1500.00"))
        self.assertEqual(lab["net_received"], Decimal("2000.00"))

    def test_the_cohort_reconciliation_still_closes_after_a_refund(self):
        add_charge(patient=self.patient, description="Consultation fee", amount="10000",
                   created_by=self.reception, source_type="consultation")
        payment = record_payment(patient=self.patient, amount="10000", received_by=self.cashier)
        refund_payment(payment=payment, amount="4000", reason="Overcharged",
                       processed_by=self.cashier)

        report = self._report()
        charges = report["charges"]
        self.assertEqual(charges["gross"], Decimal("10000.00"))
        self.assertEqual(charges["collected"], Decimal("6000.00"))
        self.assertEqual(charges["outstanding"], Decimal("4000.00"))
        self.assertTrue(report["reconciliation"]["balances"])
        for row in report["departments"]:
            self.assertEqual(row["net_due"] - row["collected"], row["outstanding"], row["label"])

    def test_the_write_off_register_still_reports_the_refund(self):
        """The adjustments block was reading refunds before; it still does."""
        add_charge(patient=self.patient, description="Card", amount="5000",
                   created_by=self.reception, source_type="card")
        payment = record_payment(patient=self.patient, amount="5000", received_by=self.cashier)
        refund_payment(payment=payment, amount="2000", reason="Overcharged",
                       processed_by=self.cashier)
        adjustments = self._report()["adjustments"]
        self.assertEqual(adjustments["refunds"], Decimal("2000.00"))
        self.assertEqual(adjustments["refunds_count"], 1)
        # And it is linked to its charge, so it is not reported as unplaceable.
        self.assertEqual(adjustments["unlinked"], Decimal("0.00"))

    def test_history_without_refunds_reports_exactly_as_it_did(self):
        """
        The figures a period already had must not move because the refund
        columns were added beside them.
        """
        add_charge(patient=self.patient, description="Consultation fee", amount="10000",
                   created_by=self.reception, source_type="consultation")
        record_payment(patient=self.patient, amount="6000", received_by=self.cashier)
        report = self._report()
        self.assertEqual(report["collections"]["total"], Decimal("6000.00"))
        self.assertEqual(report["collections"]["refunds"], Decimal("0.00"))
        self.assertEqual(report["collections"]["net"], Decimal("6000.00"))
        self.assertEqual(report["charges"]["outstanding"], Decimal("4000.00"))
        self.assertTrue(report["reconciliation"]["balances"])

    def test_the_report_still_costs_the_same_whatever_the_history(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def cost():
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get("/api/finance/report/").status_code, 200)
            return len(captured.captured_queries)

        add_charge(patient=self.patient, description="Card", amount="5000",
                   created_by=self.reception, source_type="card")
        payment = record_payment(patient=self.patient, amount="5000", received_by=self.cashier)
        refund_payment(payment=payment, amount="1000", reason="a", processed_by=self.cashier)
        small = cost()

        for index in range(20):
            patient = Patient.objects.create(first_name=f"P{index}", last_name="Test", sex="F",
                                             created_by=self.reception)
            add_charge(patient=patient, description="Laboratory: FBC", amount="3500",
                       created_by=self.reception, source_type="lab_test")
            other = record_payment(patient=patient, amount="3500", received_by=self.cashier)
            refund_payment(payment=other, amount="500", reason="b", processed_by=self.cashier)
        self.assertEqual(small, cost())


class RefundEdgeCases(TestCase):
    """The awkward states a real desk gets into."""

    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)

    def test_refunding_against_a_cancelled_bill_leaves_it_cancelled(self):
        """
        Reversing an allocation recomputes the charge's status, and that
        recomputation must not resurrect a bill the desk has withdrawn.

        Reached through `cancel_and_refund`, which is the only supported way a
        cancelled charge still holds money mid-transaction: cancelling a
        part-paid bill on its own is refused outright, precisely so the patient
        cannot be left in credit.
        """
        from apps.billing.services import cancel_and_refund
        charge = add_charge(patient=self.patient, description="Card", amount="5000",
                            created_by=self.reception, source_type="card")
        record_payment(patient=self.patient, amount="2000", received_by=self.cashier)

        cancel_and_refund(charge=charge, reason="Bill withdrawn", actor=self.cashier)

        charge.refresh_from_db()
        self.assertEqual(charge.status, "cancelled", "a withdrawn bill must stay withdrawn")
        self.assertEqual(charge.amount_paid, Decimal("0.00"))
        self.assertEqual(charge.amount, Decimal("5000.00"), "the face value is preserved")
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         Decimal("0.00"))

    def test_a_refund_after_a_waiver_does_not_push_the_patient_into_credit(self):
        from apps.billing.services import waive_charge
        add_charge(patient=self.patient, description="Consultation fee", amount="5000",
                   created_by=self.reception, source_type="consultation")
        payment = record_payment(patient=self.patient, amount="3000", received_by=self.cashier)
        charge = Charge.objects.get(patient=self.patient)
        waive_charge(charge=charge, reason="Hardship", approved_by=self.cashier)

        refund_payment(payment=payment, amount="3000", reason="Paid by mistake",
                       processed_by=self.cashier)
        ledger = PatientLedger.objects.get(patient=self.patient)
        self.assertGreaterEqual(ledger.outstanding_balance, Decimal("0.00"))

    def test_two_refunds_on_one_payment_never_exceed_what_it_settled(self):
        add_charge(patient=self.patient, description="Card", amount="4000",
                   created_by=self.reception, source_type="card")
        payment = record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        refund_payment(payment=payment, amount="1500", reason="a", processed_by=self.cashier)
        refund_payment(payment=payment, amount="2500", reason="b", processed_by=self.cashier)
        charge = Charge.objects.get(patient=self.patient)
        self.assertEqual(charge.amount_paid, Decimal("0.00"))
        self.assertEqual(
            RefundAllocation.objects.filter(charge=charge).aggregate(v=Sum("amount"))["v"],
            Decimal("4000.00"))


class PaymentListingTests(TestCase):
    """
    The payments list carries the refund figures, and still in date order.

    Annotating the refunds onto the queryset drops `Payment.Meta.ordering`
    unless it is restated — and an unordered queryset makes a paginated list
    repeat and drop rows.
    """

    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        add_charge(patient=self.patient, description="Consultation fee", amount="9000",
                   created_by=self.reception, source_type="consultation")
        self.first = record_payment(patient=self.patient, amount="3000", received_by=self.cashier)
        self.second = record_payment(patient=self.patient, amount="3000", received_by=self.cashier)
        self.third = record_payment(patient=self.patient, amount="3000", received_by=self.cashier)
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

    def test_payments_come_back_newest_first(self):
        rows = self.client.get("/api/payments/").data
        rows = rows.get("results", rows)
        self.assertEqual([row["id"] for row in rows],
                         [self.third.pk, self.second.pk, self.first.pk])

    def test_each_row_says_what_is_still_refundable(self):
        refund_payment(payment=self.second, amount="1000", reason="Overcharged",
                       processed_by=self.cashier)
        rows = self.client.get("/api/payments/").data
        rows = {row["id"]: row for row in rows.get("results", rows)}
        self.assertEqual(Decimal(rows[self.second.pk]["amount_refunded"]), Decimal("1000.00"))
        self.assertEqual(Decimal(rows[self.second.pk]["refundable_balance"]), Decimal("2000.00"))
        self.assertFalse(rows[self.second.pk]["is_fully_refunded"])
        # An untouched payment is fully refundable and says so.
        self.assertEqual(Decimal(rows[self.first.pk]["amount_refunded"]), Decimal("0.00"))
        self.assertEqual(Decimal(rows[self.first.pk]["refundable_balance"]), Decimal("3000.00"))

    def test_the_listing_costs_no_query_per_payment(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def cost():
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get("/api/payments/").status_code, 200)
            return len(captured.captured_queries)

        small = cost()
        for _ in range(5):
            add_charge(patient=self.patient, description="Card", amount="500",
                       created_by=self.reception, source_type="card")
            record_payment(patient=self.patient, amount="500", received_by=self.cashier)
        self.assertEqual(small, cost())
