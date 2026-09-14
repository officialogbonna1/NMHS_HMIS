"""
Cancelling a service, refunding money, and doing both — and the fact that they
are three different things.

The distinction the whole file exists to hold:

  * **Refund** — money goes back. The bill stands, so the patient owes it again.
  * **Cancel** — the bill is withdrawn. The obligation ends; no money moves.
  * **Cancel & refund** — an unused service that was paid for: both, atomically.

The failure this prevents is a patient owing ₦4,000 for a laboratory test that
was never run, because a refund reversed the payment and nobody withdrew the
bill.
"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import (Adjustment, Charge, PatientLedger, Payment, Refund,
                                 RefundAllocation)
from apps.billing.services import (RefundRequired, add_charge, cancel_and_refund,
                                   cancel_charge, record_payment, refund_payment)
from apps.core.models import AuditLog, Notification
from apps.patients.models import Patient


class Money(TestCase):
    """A patient, a laboratory bill, and the desks that work the money."""

    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.accountant = User.objects.create_user(username="acc", password="t", role="accountant")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.client = APIClient()

    def charge(self, amount="4000", description="Laboratory: FBC", source="lab_test"):
        return add_charge(patient=self.patient, description=description, amount=amount,
                          created_by=self.reception, source_type=source)

    def outstanding(self):
        return PatientLedger.objects.get(patient=self.patient).outstanding_balance


# ---------------------------------------------------------------------------
# Cancellation on its own
# ---------------------------------------------------------------------------

class CancellingAService(Money):
    def test_an_unpaid_service_is_cancelled_and_owes_nothing(self):
        """Test 3: billed ₦4,000, never paid, never used."""
        charge = self.charge()
        self.assertEqual(self.outstanding(), Decimal("4000.00"))

        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Test never run")

        charge.refresh_from_db()
        self.assertEqual(charge.status, "cancelled")
        self.assertEqual(self.outstanding(), Decimal("0.00"))
        # No money moved, so there is no refund and no adjustment.
        self.assertFalse(Refund.objects.exists())
        self.assertFalse(Adjustment.objects.exists())

    def test_the_original_charge_survives_cancellation_intact(self):
        charge = self.charge()
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Test never run")
        charge.refresh_from_db()

        self.assertEqual(charge.amount, Decimal("4000.00"), "the face value is never rewritten")
        self.assertEqual(charge.description, "Laboratory: FBC")
        self.assertEqual(charge.department.code, "laboratory", "attribution survives")
        self.assertEqual(charge.source_type, "lab_test")
        self.assertTrue(Charge.objects.filter(pk=charge.pk).exists(), "never deleted")

    def test_cancelling_records_who_when_and_why(self):
        charge = self.charge()
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Sample rejected")
        charge.refresh_from_db()
        self.assertEqual(charge.cancelled_by, self.cashier)
        self.assertEqual(charge.cancellation_reason, "Sample rejected")
        self.assertIsNotNone(charge.cancelled_at)

    def test_a_reason_is_required(self):
        charge = self.charge()
        for reason in ("", "   ", None):
            with self.subTest(repr(reason)):
                with self.assertRaises(ValueError):
                    cancel_charge(charge=charge, cancelled_by=self.cashier, reason=reason)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "unpaid")

    def test_an_already_cancelled_charge_cannot_be_cancelled_again(self):
        charge = self.charge()
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Not run")
        with self.assertRaises(ValueError) as caught:
            cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Again")
        self.assertIn("already cancelled", str(caught.exception).lower())

    def test_cancelling_a_paid_charge_alone_is_refused_rather_than_crediting_the_patient(self):
        """
        The bug this guard exists for: cancelling a bill that is still holding
        money leaves `total_payments` ahead of `total_charges`, and the patient
        reads as ₦4,000 in credit.
        """
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)

        with self.assertRaises(RefundRequired) as caught:
            cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Never used")
        self.assertEqual(caught.exception.amount, Decimal("4000.00"))

        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid", "nothing was cancelled")
        self.assertEqual(self.outstanding(), Decimal("0.00"), "and nobody is in credit")

    def test_a_cancelled_charge_reports_zero_outstanding_but_keeps_its_balance(self):
        charge = self.charge()
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Not run")
        charge.refresh_from_db()
        # What the patient owes, versus the charge's own arithmetic. Both true,
        # and an audit needs to be able to read the second.
        self.assertEqual(charge.outstanding, Decimal("0.00"))
        self.assertEqual(charge.balance, Decimal("4000.00"))
        self.assertEqual(charge.settlement_status, "cancelled")

    def test_cancelling_closes_a_pay_later_on_the_withdrawn_bill(self):
        from apps.billing.services import defer_charge
        charge = self.charge()
        deferral = defer_charge(charge=charge, reason="Will pay Friday", approved_by=self.reception)
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Not run")
        deferral.refresh_from_db()
        self.assertIsNotNone(deferral.released_at,
                             "an authorisation to pay a withdrawn bill is spent")


# ---------------------------------------------------------------------------
# A refund is not a cancellation
# ---------------------------------------------------------------------------

class ARefundIsNotACancellation(Money):
    def test_refunding_a_delivered_service_leaves_the_bill_standing(self):
        """
        Test 4: the service *was* provided and the money is being returned
        anyway. The charge must stay active and the patient must owe it again —
        this is the existing refund semantics and it must not change.
        """
        charge = self.charge()
        payment = record_payment(patient=self.patient, amount="4000", received_by=self.cashier)

        refund_payment(payment=payment, amount="4000", reason="Goodwill",
                       processed_by=self.cashier)

        charge.refresh_from_db()
        self.assertNotEqual(charge.status, "cancelled", "a refund never cancels a charge")
        self.assertEqual(charge.status, "unpaid")
        self.assertEqual(self.outstanding(), Decimal("4000.00"),
                         "the service was delivered, so it is owed again")
        self.assertEqual(Payment.objects.get(pk=payment.pk).amount, Decimal("4000.00"))

    def test_a_partial_refund_leaves_the_bill_part_paid_and_active(self):
        charge = self.charge()
        payment = record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        refund_payment(payment=payment, amount="1500", reason="Overcharged",
                       processed_by=self.cashier)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "partial")
        self.assertEqual(charge.amount_paid, Decimal("2500.00"))
        self.assertEqual(self.outstanding(), Decimal("1500.00"))

    def test_a_cancellation_is_not_recorded_as_a_refund(self):
        charge = self.charge()
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Not run")
        self.assertFalse(Adjustment.objects.filter(kind="refund").exists())
        self.assertFalse(Refund.objects.exists())


# ---------------------------------------------------------------------------
# Cancel & refund
# ---------------------------------------------------------------------------

class CancelAndRefund(Money):
    def test_a_fully_paid_unused_service(self):
        """Test 1: ₦4,000 billed, ₦4,000 paid, never used → outstanding ₦0."""
        charge = self.charge()
        payment = record_payment(patient=self.patient, amount="4000", received_by=self.cashier)

        result = cancel_and_refund(charge=charge, reason="Test never run", actor=self.cashier)

        charge.refresh_from_db()
        self.assertEqual(charge.status, "cancelled")
        self.assertEqual(result["amount_refunded"], Decimal("4000.00"))
        self.assertEqual(self.outstanding(), Decimal("0.00"),
                         "the patient must not owe for a test that never happened")

        # The original payment is untouched and still readable.
        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("4000.00"))
        self.assertEqual(payment.received_by, self.cashier)
        # The refund is its own transaction, linked to that payment.
        refund = Refund.objects.get()
        self.assertEqual(refund.amount, Decimal("4000.00"))
        self.assertEqual(refund.payment_id, payment.pk)
        self.assertEqual(refund.reason, "Test never run")
        # …with an allocation naming the bill it came off.
        allocation = RefundAllocation.objects.get()
        self.assertEqual(allocation.charge_id, charge.pk)
        self.assertEqual(allocation.amount, Decimal("4000.00"))
        # …and the ledger adjustment every other write-off already uses.
        self.assertEqual(Adjustment.objects.get(kind="refund").amount, Decimal("4000.00"))

    def test_a_partly_paid_unused_service(self):
        """
        Test 2: ₦4,000 billed, ₦2,000 paid, never used. Refund the ₦2,000 —
        and the *unpaid* ₦2,000 must not linger as outstanding.
        """
        charge = self.charge()
        record_payment(patient=self.patient, amount="2000", received_by=self.cashier)
        self.assertEqual(self.outstanding(), Decimal("2000.00"))

        result = cancel_and_refund(charge=charge, reason="Test never run", actor=self.cashier)

        charge.refresh_from_db()
        self.assertEqual(charge.status, "cancelled")
        self.assertEqual(charge.amount, Decimal("4000.00"), "original amount preserved")
        self.assertEqual(result["amount_refunded"], Decimal("2000.00"))
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_an_unpaid_service_needs_no_refund(self):
        """The caller does not have to know which case it is in."""
        charge = self.charge()
        result = cancel_and_refund(charge=charge, reason="Test never run", actor=self.cashier)

        charge.refresh_from_db()
        self.assertEqual(charge.status, "cancelled")
        self.assertEqual(result["amount_refunded"], Decimal("0.00"))
        self.assertEqual(result["refunds"], [])
        self.assertFalse(Refund.objects.exists())
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_a_bill_settled_by_two_payments_answers_both(self):
        charge = self.charge()
        first = record_payment(patient=self.patient, amount="1500", received_by=self.cashier)
        second = record_payment(patient=self.patient, amount="2500", received_by=self.cashier)

        result = cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

        self.assertEqual(result["amount_refunded"], Decimal("4000.00"))
        by_payment = {r.payment_id: r.amount for r in Refund.objects.all()}
        self.assertEqual(by_payment, {first.pk: Decimal("1500.00"), second.pk: Decimal("2500.00")})
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_it_refunds_only_the_cancelled_service_not_the_others_the_payment_settled(self):
        """
        One ₦5,000 payment settling a ₦4,000 scan and a ₦1,000 consultation.
        Cancelling the scan must leave the consultation paid.
        """
        scan = self.charge("4000", "Ultrasound: Abdominal", "ultrasound")
        consult = self.charge("1000", "Consultation fee", "consultation")
        record_payment(patient=self.patient, amount="5000", received_by=self.cashier)

        cancel_and_refund(charge=scan, reason="Scan never done", actor=self.cashier)

        scan.refresh_from_db(); consult.refresh_from_db()
        self.assertEqual(scan.status, "cancelled")
        self.assertEqual(consult.status, "paid", "the consultation was delivered and stays paid")
        self.assertEqual(consult.amount_paid, Decimal("1000.00"))
        self.assertEqual(Refund.objects.get().amount, Decimal("4000.00"))
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_a_partial_refund_of_the_cancelled_service_is_refused(self):
        """
        This used to be allowed, and left the patient ₦3,000 in credit against
        a bill nobody owed. Cancel & refund returns everything or nothing; part
        of a payment going back is `refund_payment`, and the service stays.
        """
        from apps.billing.services import FullRefundRequired
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        with self.assertRaises(FullRefundRequired):
            cancel_and_refund(charge=charge, reason="Part used", actor=self.cashier,
                              expected_amount="1000")
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid", "nothing was cancelled")
        self.assertFalse(Refund.objects.exists(), "and nothing went back")
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_refunding_more_than_the_charge_holds_is_refused(self):
        from apps.billing.services import FullRefundRequired
        charge = self.charge()
        record_payment(patient=self.patient, amount="2000", received_by=self.cashier)
        with self.assertRaises(FullRefundRequired) as caught:
            cancel_and_refund(charge=charge, reason="x", actor=self.cashier,
                              expected_amount="4000")
        self.assertIn("2000", str(caught.exception))

    def test_a_reason_is_required(self):
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        with self.assertRaises(ValueError):
            cancel_and_refund(charge=charge, reason="  ", actor=self.cashier)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid")

    def test_an_already_cancelled_charge_is_refused(self):
        charge = self.charge()
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Not run")
        with self.assertRaises(ValueError):
            cancel_and_refund(charge=charge, reason="Again", actor=self.cashier)

    def test_a_failure_half_way_rolls_the_whole_thing_back(self):
        """
        The cancellation and the refund are one operation. If the refund throws
        — a payment refunded out from under us a moment ago — the charge must
        not be left cancelled with the money still held.
        """
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)

        with mock.patch("apps.billing.services.refund_payment",
                        side_effect=ValueError("payment already refunded")):
            with self.assertRaises(ValueError):
                cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid", "the cancellation rolled back with the refund")
        self.assertIsNone(charge.cancelled_at)
        self.assertEqual(charge.cancellation_reason, "")
        self.assertFalse(Refund.objects.exists())
        self.assertEqual(self.outstanding(), Decimal("0.00"))

    def test_department_attribution_survives(self):
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)
        charge.refresh_from_db()
        self.assertEqual(charge.department.code, "laboratory")
        self.assertEqual(charge.source_type, "lab_test")

    def test_one_decision_raises_one_notification_per_desk(self):
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        Notification.objects.all().delete()

        cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

        notes = Notification.objects.filter(recipient=self.accountant, category="billing")
        self.assertEqual(notes.count(), 1,
                         "the cancellation and the refund are one event, not two bells")
        self.assertIn("Service cancelled & refunded", notes.first().title)

    def test_cancelling_an_unpaid_service_announces_a_cancellation_not_a_refund(self):
        charge = self.charge()
        Notification.objects.all().delete()
        cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)
        note = Notification.objects.filter(recipient=self.accountant).first()
        self.assertIn("Service cancelled", note.title)
        self.assertNotIn("refunded", note.title)


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------

class TheEndpoints(Money):
    def setUp(self):
        super().setUp()
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")

    def post(self, user, charge, path="cancel", **body):
        self.client.force_authenticate(user)
        return self.client.post(f"/api/charges/{charge.pk}/{path}/", body, format="json")

    def test_the_cash_desk_and_admin_may_cancel_a_service(self):
        for user in (self.cashier, self.accountant, self.admin):
            with self.subTest(user.role):
                charge = self.charge()
                self.assertEqual(self.post(user, charge, reason="Not run").status_code, 200)

    def test_reception_the_pharmacy_and_the_wards_may_not_cancel(self):
        for user in (self.reception, self.pharmacist, self.doctor, self.nurse):
            with self.subTest(user.role):
                charge = self.charge()
                self.assertEqual(self.post(user, charge, reason="Not run").status_code, 403)
                charge.refresh_from_db()
                self.assertNotEqual(charge.status, "cancelled")

    def test_an_anonymous_caller_may_not(self):
        charge = self.charge()
        for path in ("cancel", "cancel-and-refund"):
            response = APIClient().post(f"/api/charges/{charge.pk}/{path}/",
                                        {"reason": "x"}, format="json")
            self.assertIn(response.status_code, (401, 403))

    def test_cancelling_without_a_reason_is_a_400(self):
        charge = self.charge()
        response = self.post(self.cashier, charge, reason="")
        self.assertEqual(response.status_code, 400)
        charge.refresh_from_db()
        self.assertNotEqual(charge.status, "cancelled")

    def test_cancelling_a_paid_charge_answers_refund_required_with_the_amount(self):
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        response = self.post(self.cashier, charge, reason="Never used")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "refund_required")
        self.assertEqual(response.data["refundable"], "4000.00")

    def test_cancel_and_refund_over_the_api(self):
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        response = self.post(self.cashier, charge, "cancel-and-refund", reason="Never run")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["amount_refunded"], "4000.00")
        self.assertEqual(response.data["outstanding"], "0.00")
        self.assertEqual(response.data["charge"]["status"], "cancelled")
        self.assertEqual(Decimal(response.data["charge"]["outstanding"]), Decimal("0.00"))
        self.assertEqual(Decimal(response.data["charge"]["amount"]), Decimal("4000.00"))
        self.assertEqual(len(response.data["refunds"]), 1)

    def test_only_refund_roles_may_cancel_and_refund(self):
        for user in (self.cashier, self.accountant, self.admin):
            with self.subTest(user.role):
                charge = self.charge()
                record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
                self.assertEqual(
                    self.post(user, charge, "cancel-and-refund", reason="x").status_code, 200)
        for user in (self.reception, self.pharmacist, self.doctor, self.nurse):
            with self.subTest(user.role):
                charge = self.charge()
                self.assertEqual(
                    self.post(user, charge, "cancel-and-refund", reason="x").status_code, 403)

    def test_both_actions_are_audited_with_the_whole_story(self):
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        self.post(self.cashier, charge, "cancel-and-refund", reason="Test never run")

        entry = AuditLog.objects.filter(action="billing.cancel_and_refund").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.cashier)
        self.assertEqual(entry.details["service"], "Laboratory: FBC")
        self.assertEqual(entry.details["department"], "Laboratory")
        self.assertEqual(entry.details["amount"], "4000.00")
        self.assertEqual(entry.details["amount_refunded"], "4000.00")
        self.assertEqual(entry.details["reason"], "Test never run")
        self.assertEqual(entry.details["outstanding_after"], "0.00")

    def test_a_charge_row_carries_what_the_refunds_screen_needs(self):
        charge = self.charge()
        record_payment(patient=self.patient, amount="2500", received_by=self.cashier)
        self.client.force_authenticate(self.cashier)
        rows = self.client.get("/api/charges/", {"patient": self.patient.pk}).data
        row = (rows.get("results", rows))[0]

        self.assertEqual(Decimal(row["amount"]), Decimal("4000.00"))
        self.assertEqual(Decimal(row["amount_paid"]), Decimal("2500.00"))
        self.assertEqual(Decimal(row["amount_refunded"]), Decimal("0.00"))
        self.assertEqual(Decimal(row["refundable_amount"]), Decimal("2500.00"))
        self.assertEqual(Decimal(row["outstanding"]), Decimal("1500.00"))
        self.assertEqual(row["settlement_status"], "partial")
        self.assertEqual(row["department_name"], "Laboratory")
        self.assertFalse(row["is_cancelled"])

    def test_the_charge_list_costs_no_query_per_row(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def cost():
            self.client.force_authenticate(self.cashier)
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get("/api/charges/").status_code, 200)
            return len(captured.captured_queries)

        self.charge()
        small = cost()
        for index in range(10):
            self.charge("500", f"Item {index}", "card")
        self.assertEqual(small, cost())


# ---------------------------------------------------------------------------
# What the report says afterwards
# ---------------------------------------------------------------------------

class TheReportAfterwards(Money):
    def report(self):
        self.client.force_authenticate(self.cashier)
        response = self.client.get("/api/finance/report/")
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_gross_stays_gross_refunds_are_named_and_net_is_what_was_kept(self):
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

        data = self.report()
        collections = data["collections"]
        self.assertEqual(collections["total"], Decimal("4000.00"),
                         "the money did arrive and the gross must still say so")
        self.assertEqual(collections["refunds"], Decimal("4000.00"))
        self.assertEqual(collections["net"], Decimal("0.00"))
        self.assertEqual(data["outstanding_now"], Decimal("0.00"))

    def test_a_cancelled_charge_leaves_the_cohort_and_takes_its_outstanding_with_it(self):
        self.charge("4000", "Laboratory: FBC", "lab_test")
        kept = self.charge("1000", "Consultation fee", "consultation")
        cancelled = Charge.objects.get(description="Laboratory: FBC")
        cancel_charge(charge=cancelled, cancelled_by=self.cashier, reason="Not run")

        data = self.report()
        self.assertEqual(data["charges"]["gross"], Decimal("1000.00"),
                         "a withdrawn bill is not revenue that was billed")
        self.assertEqual(data["charges"]["outstanding"], Decimal("1000.00"))
        self.assertTrue(data["reconciliation"]["balances"])
        # The consultation is untouched.
        kept.refresh_from_db()
        self.assertEqual(kept.status, "unpaid")

    def test_the_refund_is_attributed_to_the_department_that_took_the_money(self):
        charge = self.charge()
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

        rows = {row["key"]: row for row in self.report()["departments"]}
        lab = rows["laboratory"]
        self.assertEqual(lab["received"], Decimal("4000.00"))
        self.assertEqual(lab["refunded"], Decimal("4000.00"))
        self.assertEqual(lab["net_received"], Decimal("0.00"))

    def test_a_cancellation_is_never_counted_as_a_refund(self):
        charge = self.charge()
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Not run")
        data = self.report()
        self.assertEqual(data["adjustments"]["refunds"], Decimal("0.00"))
        self.assertEqual(data["collections"]["refunds"], Decimal("0.00"))
