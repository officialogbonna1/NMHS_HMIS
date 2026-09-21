"""
Cancel & refund, hardened: the six scenarios the desk actually meets, and every
way the combined operation could leave a balance nobody can explain.

The rule the whole file holds: **Cancel & refund never leaves a patient in
credit.** It returns everything the service is holding or nothing at all; it
refuses rather than guesses; and afterwards the patient's balance has fallen by
exactly what the bill still owed.
"""
import threading
from decimal import Decimal
from unittest import mock, skipUnless

from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import services
from apps.billing.models import (Adjustment, Charge, PatientLedger, Payment, PaymentAllocation,
                                 Refund, RefundAllocation)
from apps.billing.services import (ChargeNotCancellable, FullRefundRequired, LedgerMismatch,
                                   add_charge, apply_amount_discount, apply_percentage_discount,
                                   cancel_and_refund, cancel_charge, record_payment,
                                   refund_payment, waive_charge)
from apps.core.models import AuditLog, Notification
from apps.patients.models import Patient

D = Decimal


class Desk(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier",
                                                first_name="Ada", last_name="Bello")
        self.accountant = User.objects.create_user(username="acc", password="t", role="accountant")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.patient = Patient.objects.create(first_name="Ngozi", last_name="Eze", sex="F",
                                              phone_number="08031234567",
                                              created_by=self.reception)
        self.client = APIClient()

    def charge(self, amount="4000", description="Laboratory: FBC", source="lab_test",
               patient=None):
        return add_charge(patient=patient or self.patient, description=description,
                          amount=amount, created_by=self.reception, source_type=source)

    def pay(self, amount, patient=None):
        return record_payment(patient=patient or self.patient, amount=amount,
                              received_by=self.cashier)

    def outstanding(self, patient=None):
        return PatientLedger.objects.get(patient=patient or self.patient).outstanding_balance

    def post(self, user, charge, path, **body):
        self.client.force_authenticate(user)
        return self.client.post(f"/api/charges/{charge.pk}/{path}/", body, format="json")


# ---------------------------------------------------------------------------
# The six scenarios
# ---------------------------------------------------------------------------

class TheScenarios(Desk):
    def test_a_fully_paid_unused_service(self):
        charge = self.charge("4000")
        payment = self.pay("4000")
        Notification.objects.all().delete()

        response = self.post(self.cashier, charge, "cancel-and-refund",
                             reason="Test never run", amount="4000.00")

        self.assertEqual(response.status_code, 200, response.data)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "cancelled")
        self.assertEqual(charge.amount, D("4000.00"), "the original charge is never rewritten")
        self.assertEqual(self.outstanding(), D("0.00"))
        # The payment is still there, untouched…
        payment.refresh_from_db()
        self.assertEqual(payment.amount, D("4000.00"))
        # …and the refund is its own transaction beside it.
        refund = Refund.objects.get()
        self.assertEqual((refund.payment_id, refund.amount), (payment.pk, D("4000.00")))
        self.assertTrue(Notification.objects.filter(title__startswith="Service cancelled & refunded").exists())
        self.assertTrue(AuditLog.objects.filter(action="billing.cancel_and_refund").exists())

    def test_b_an_unpaid_unused_service(self):
        charge = self.charge("4000")
        response = self.post(self.cashier, charge, "cancel", reason="Patient did not attend")

        self.assertEqual(response.status_code, 200, response.data)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "cancelled")
        self.assertFalse(Refund.objects.exists(), "no money moved, so there is no refund")
        self.assertEqual(self.outstanding(), D("0.00"))
        self.assertEqual(D(response.data["outstanding"]), D("0.00"))

    def test_c_a_partly_paid_unused_service(self):
        charge = self.charge("4000")
        self.pay("2000")
        self.assertEqual(self.outstanding(), D("2000.00"))

        response = self.post(self.cashier, charge, "cancel-and-refund",
                             reason="Test never run", amount="2000.00")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["amount_refunded"], "2000.00")
        charge.refresh_from_db()
        self.assertEqual(charge.status, "cancelled")
        self.assertEqual(Refund.objects.get().amount, D("2000.00"))
        self.assertEqual(self.outstanding(), D("0.00"),
                         "the unpaid ₦2,000 is not left owing on a withdrawn bill")

    def test_d_a_delivered_service_refunded_in_part_stays_active(self):
        charge = self.charge("4000")
        payment = self.pay("4000")
        self.client.force_authenticate(self.cashier)

        response = self.client.post(f"/api/payments/{payment.pk}/refund/",
                                    {"amount": "2000", "reason": "Goodwill"}, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "partial", "a refund never cancels the service")
        self.assertIsNone(charge.cancelled_at)
        self.assertEqual(charge.amount_paid, D("2000.00"))
        self.assertEqual(self.outstanding(), D("2000.00"), "the service was given, so it is owed")
        self.assertEqual(Payment.objects.get(pk=payment.pk).amount, D("4000.00"))

    def test_e_one_payment_settling_two_bills_cancels_only_the_one(self):
        consultation = self.charge("4000", "General Consultation", "consultation")
        laboratory = self.charge("6000", "Laboratory: FBC", "lab_test")
        payment = self.pay("10000")
        allocations_before = sorted(PaymentAllocation.objects.values_list("charge_id", "amount"))

        response = self.post(self.cashier, laboratory, "cancel-and-refund",
                             reason="Sample never collected", amount="6000.00")

        self.assertEqual(response.status_code, 200, response.data)
        laboratory.refresh_from_db(); consultation.refresh_from_db()
        self.assertEqual(laboratory.status, "cancelled")
        self.assertEqual(laboratory.amount_paid, D("0.00"))
        # The consultation was delivered: still paid, not reopened, owes nothing.
        self.assertEqual(consultation.status, "paid")
        self.assertEqual(consultation.amount_paid, D("4000.00"))
        self.assertEqual(consultation.outstanding, D("0.00"))
        # ₦6,000 back, from that payment, off the laboratory bill alone.
        refund = Refund.objects.get()
        self.assertEqual((refund.payment_id, refund.amount), (payment.pk, D("6000.00")))
        self.assertEqual(list(RefundAllocation.objects.values_list("charge_id", "amount")),
                         [(laboratory.pk, D("6000.00"))])
        # The payment's own allocations are history and do not move.
        self.assertEqual(sorted(PaymentAllocation.objects.values_list("charge_id", "amount")),
                         allocations_before)
        self.assertEqual(self.outstanding(), D("0.00"))

    def test_e_the_newer_bill_can_be_cancelled_without_touching_the_older_one(self):
        # The mirror: cancelling the *older* bill must not reopen the newer.
        consultation = self.charge("4000", "General Consultation", "consultation")
        laboratory = self.charge("6000", "Laboratory: FBC", "lab_test")
        self.pay("10000")

        cancel_and_refund(charge=consultation, reason="Doctor unavailable", actor=self.cashier)

        laboratory.refresh_from_db()
        self.assertEqual((laboratory.status, laboratory.amount_paid), ("paid", D("6000.00")))
        self.assertEqual(Refund.objects.get().amount, D("4000.00"))
        self.assertEqual(self.outstanding(), D("0.00"))

    def test_f_a_second_cancel_and_refund_is_refused_and_writes_nothing(self):
        charge = self.charge("4000")
        self.pay("4000")
        first = self.post(self.cashier, charge, "cancel-and-refund", reason="Never run",
                          amount="4000.00")
        self.assertEqual(first.status_code, 200)
        notifications = Notification.objects.count()
        audits = AuditLog.objects.count()

        second = self.post(self.accountant, charge, "cancel-and-refund", reason="Never run",
                           amount="4000.00")

        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.data["code"], "not_cancellable")
        self.assertEqual(Refund.objects.count(), 1, "no duplicate refund")
        self.assertEqual(Adjustment.objects.filter(kind="refund").count(), 1)
        self.assertEqual(Notification.objects.count(), notifications)
        self.assertEqual(AuditLog.objects.count(), audits)
        self.assertEqual(self.outstanding(), D("0.00"))

    def test_f_a_plain_cancel_after_cancel_and_refund_is_refused_too(self):
        charge = self.charge("4000")
        self.pay("4000")
        cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)
        response = self.post(self.cashier, charge, "cancel", reason="Again")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "not_cancellable")
        self.assertEqual(Refund.objects.count(), 1)


# ---------------------------------------------------------------------------
# All of it or nothing
# ---------------------------------------------------------------------------

class CancelAndRefundReturnsEverything(Desk):
    def test_the_refund_is_the_full_refundable_amount_when_none_is_given(self):
        charge = self.charge("4000")
        self.pay("4000")
        result = cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)
        self.assertEqual(result["amount_refunded"], D("4000.00"))

    def test_a_smaller_amount_is_refused_before_anything_is_written(self):
        charge = self.charge("4000")
        self.pay("4000")
        response = self.post(self.cashier, charge, "cancel-and-refund",
                             reason="Part used", amount="1000")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "full_refund_required")
        self.assertEqual(response.data["refundable"], "4000.00")
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid")
        self.assertIsNone(charge.cancelled_at)
        self.assertFalse(Refund.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action="billing.cancel_and_refund").exists())
        self.assertEqual(self.outstanding(), D("0.00"))

    def test_a_larger_amount_is_refused(self):
        charge = self.charge("4000")
        self.pay("2000")
        response = self.post(self.cashier, charge, "cancel-and-refund", reason="x", amount="4000")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "full_refund_required")
        self.assertFalse(Refund.objects.exists())

    def test_a_figure_that_changed_after_the_page_loaded_is_refused_then_accepted(self):
        """
        The screen said ₦4,000. Meanwhile another cashier refunded ₦1,000 of the
        payment, so the service now holds ₦3,000. The stale figure is refused,
        and the fresh one goes through and still lands on zero.
        """
        charge = self.charge("4000")
        payment = self.pay("4000")
        refund_payment(payment=payment, amount="1000", reason="Overcharged",
                       processed_by=self.accountant)

        stale = self.post(self.cashier, charge, "cancel-and-refund", reason="Never run",
                          amount="4000.00")
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(stale.data["code"], "full_refund_required")
        self.assertEqual(stale.data["refundable"], "3000.00")

        fresh = self.post(self.cashier, charge, "cancel-and-refund", reason="Never run",
                          amount="3000.00")
        self.assertEqual(fresh.status_code, 200, fresh.data)
        from django.db.models import Sum
        self.assertEqual(Refund.objects.aggregate(t=Sum("amount"))["t"], D("4000.00"),
                         "₦1,000 then ₦3,000 — the whole payment, never more")
        self.assertEqual(self.outstanding(), D("0.00"))

    def test_a_non_number_is_a_400(self):
        charge = self.charge("4000")
        self.pay("4000")
        response = self.post(self.cashier, charge, "cancel-and-refund", reason="x", amount="lots")
        self.assertEqual(response.status_code, 400)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid")

    def test_no_combination_leaves_the_patient_in_credit(self):
        """Every shape of bill a desk can meet, cancelled and refunded: zero every time."""
        def fully_paid(c): self.pay("4000")
        def part_paid(c): self.pay("2500")
        def discounted_then_paid(c):
            apply_amount_discount(charge=c, amount="1000", reason="Staff", approved_by=self.cashier)
            self.pay("3000")
        def part_waived_then_paid(c):
            waive_charge(charge=c, amount="500", reason="Hardship", approved_by=self.cashier)
            self.pay("1000")
        def discounted_in_full(c):
            apply_percentage_discount(charge=c, percent="100", reason="Staff",
                                      approved_by=self.cashier)
        def two_payments(c):
            self.pay("1500"); self.pay("2500")
        def paid_then_partly_refunded(c):
            refund_payment(payment=self.pay("4000"), amount="1500", reason="Overcharged",
                           processed_by=self.cashier)

        for shape in (fully_paid, part_paid, discounted_then_paid, part_waived_then_paid,
                      discounted_in_full, two_payments, paid_then_partly_refunded):
            with self.subTest(shape.__name__):
                patient = Patient.objects.create(first_name=shape.__name__, last_name="Case",
                                                 sex="F", created_by=self.reception)
                self.patient = patient
                charge = self.charge("4000", patient=patient)
                shape(charge)

                cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

                charge.refresh_from_db()
                self.assertEqual(charge.status, "cancelled")
                self.assertEqual(charge.amount_paid, D("0.00"), "the bill holds nothing")
                self.assertEqual(charge.amount, D("4000.00"))
                self.assertEqual(self.outstanding(patient), D("0.00"))


# ---------------------------------------------------------------------------
# Discounts and waivers on a bill that is then withdrawn
# ---------------------------------------------------------------------------

class ForgivenMoneyOnAWithdrawnBill(Desk):
    def test_cancelling_a_discounted_bill_does_not_credit_the_discount_twice(self):
        """₦4,000, ₦1,000 off, then cancelled: this read −₦1,000 before the fix."""
        charge = self.charge("4000")
        apply_amount_discount(charge=charge, amount="1000", reason="Staff", approved_by=self.cashier)
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Never run")

        self.assertEqual(self.outstanding(), D("0.00"))
        # Nothing was hidden to get there: the discount is still on the record.
        charge.refresh_from_db()
        self.assertEqual(charge.amount_discounted, D("1000.00"))
        self.assertTrue(Adjustment.objects.filter(kind="discount", charge=charge).exists())

    def test_cancelling_a_part_waived_bill(self):
        charge = self.charge("4000")
        waive_charge(charge=charge, amount="1500", reason="Hardship", approved_by=self.cashier)
        cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Never run")
        self.assertEqual(self.outstanding(), D("0.00"))

    def test_a_discount_on_another_bill_still_counts(self):
        cancelled = self.charge("4000")
        kept = self.charge("2000", "General Consultation", "consultation")
        apply_amount_discount(charge=kept, amount="500", reason="Staff", approved_by=self.cashier)
        cancel_charge(charge=cancelled, cancelled_by=self.cashier, reason="Never run")
        self.assertEqual(self.outstanding(), D("1500.00"))

    def test_an_unlinked_historical_discount_still_counts(self):
        """Rows with no charge behind them have no charge to be cancelled."""
        self.charge("4000")
        Adjustment.objects.create(patient=self.patient, charge=None, kind="discount",
                                  amount=D("500"), reason="Loyalty", approved_by=self.cashier)
        self.assertEqual(services.refresh_ledger(self.patient).outstanding_balance, D("3500.00"))


# ---------------------------------------------------------------------------
# Atomicity and concurrency
# ---------------------------------------------------------------------------

class AllOrNothing(Desk):
    def test_a_broken_ledger_check_rolls_back_the_cancellation_and_the_refund(self):
        charge = self.charge("4000")
        self.pay("4000")
        with mock.patch("apps.billing.services._check_withdrawal",
                        side_effect=LedgerMismatch(D("0"), D("-1"))):
            with self.assertRaises(LedgerMismatch):
                cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

        charge.refresh_from_db()
        self.assertEqual((charge.status, charge.amount_paid), ("paid", D("4000.00")))
        self.assertIsNone(charge.cancelled_at)
        self.assertFalse(Refund.objects.exists())
        self.assertFalse(RefundAllocation.objects.exists())
        self.assertFalse(Adjustment.objects.filter(kind="refund").exists())
        self.assertEqual(self.outstanding(), D("0.00"))

    def test_a_ledger_that_would_not_close_is_refused_over_the_api(self):
        charge = self.charge("4000")
        with mock.patch("apps.billing.services._check_withdrawal",
                        side_effect=LedgerMismatch(D("-4000"), D("-5000"))):
            response = self.post(self.cashier, charge, "cancel", reason="Never run")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "ledger_mismatch")
        charge.refresh_from_db()
        self.assertEqual(charge.status, "unpaid")

    def test_a_stale_copy_of_the_charge_cannot_cancel_it_twice(self):
        charge = self.charge("4000")
        self.pay("4000")
        stale = Charge.objects.get(pk=charge.pk)       # read before either cashier acts
        cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

        with self.assertRaises(ChargeNotCancellable):
            cancel_and_refund(charge=stale, reason="Never run", actor=self.accountant)
        self.assertEqual(Refund.objects.count(), 1)

    def _cancel_during(self, charge, on_call, **change):
        """Make another desk change the charge at the `on_call`-th ledger read."""
        real = services.refresh_ledger
        calls = {"n": 0}

        def racing(patient):
            calls["n"] += 1
            if calls["n"] == on_call:
                Charge.objects.filter(pk=charge.pk).update(**change)
            return real(patient)
        return mock.patch("apps.billing.services.refresh_ledger", side_effect=racing)

    def test_the_status_is_compare_and_set_so_a_lost_lock_still_cancels_once(self):
        """
        `select_for_update` does nothing on SQLite. Simulate the other cashier
        winning between the read and the write: the update finds nothing to
        change and the second caller is refused.
        """
        charge = self.charge("4000")
        with self._cancel_during(charge, on_call=1, status="cancelled"):
            with self.assertRaises(ChargeNotCancellable):
                cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Never run")

    def test_cancel_and_refund_loses_the_race_before_any_money_moves(self):
        charge = self.charge("4000")
        self.pay("4000")
        # Call 1 is cancel_and_refund's own "before"; call 2 is inside
        # cancel_charge, after it has checked the status and before it writes.
        with self._cancel_during(charge, on_call=2, status="cancelled"), \
                mock.patch("apps.billing.services.refund_payment") as refund:
            with self.assertRaises(ChargeNotCancellable):
                cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)
        refund.assert_not_called()
        self.assertFalse(Refund.objects.exists())

    def test_a_payment_landing_mid_cancellation_is_refused(self):
        charge = self.charge("4000")
        with self._cancel_during(charge, on_call=1, amount_paid=D("1000"), status="partial"):
            with self.assertRaises(ValueError) as caught:
                cancel_charge(charge=charge, cancelled_by=self.cashier, reason="Never run")
        self.assertIn("changed while it was being cancelled", str(caught.exception))


@skipUnless(connection.vendor == "postgresql",
            "Real row locks: SQLite serialises the whole database instead, and the "
            "compare-and-set guard is held deterministically in AllOrNothing above.")
class TwoCashiersAtOnce(TransactionTestCase):
    def test_only_one_of_two_simultaneous_cancel_and_refunds_succeeds(self):
        cashier = User.objects.create_user(username="c1", password="t", role="cashier")
        accountant = User.objects.create_user(username="c2", password="t", role="accountant")
        patient = Patient.objects.create(first_name="Race", last_name="Case", sex="F",
                                         created_by=cashier)
        charge = add_charge(patient=patient, description="Laboratory: FBC", amount="4000",
                            created_by=cashier, source_type="lab_test")
        record_payment(patient=patient, amount="4000", received_by=cashier)

        barrier = threading.Barrier(2)
        outcomes = []

        def attempt(actor):
            from django.db import connection as thread_connection
            try:
                barrier.wait()
                cancel_and_refund(charge=Charge.objects.get(pk=charge.pk), reason="Never run",
                                  actor=actor)
                outcomes.append("ok")
            except Exception as exc:        # the loser's refusal, whatever shape it takes
                outcomes.append(type(exc).__name__)
            finally:
                thread_connection.close()

        threads = [threading.Thread(target=attempt, args=(u,)) for u in (cashier, accountant)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=30)

        self.assertEqual(outcomes.count("ok"), 1, outcomes)
        self.assertEqual(Refund.objects.count(), 1)
        self.assertEqual(PatientLedger.objects.get(patient=patient).outstanding_balance, D("0.00"))


# ---------------------------------------------------------------------------
# Attribution, audit and notification
# ---------------------------------------------------------------------------

class TheRecordAfterwards(Desk):
    def test_the_department_is_the_service_s_not_the_cashier_s(self):
        # The cashier works the front desk; the money was the laboratory's.
        self.cashier.department = "Reception"
        self.cashier.save(update_fields=["department"])

        charge = self.charge("4000", "Laboratory: FBC", "lab_test")
        self.pay("4000")
        self.post(self.cashier, charge, "cancel-and-refund", reason="Never run", amount="4000.00")

        charge.refresh_from_db()
        self.assertEqual(charge.department.code, "laboratory")
        self.assertEqual(RefundAllocation.objects.get().charge.department.code, "laboratory")
        entry = AuditLog.objects.get(action="billing.cancel_and_refund")
        self.assertEqual(entry.details["department"], "Laboratory")

        self.client.force_authenticate(self.cashier)
        refund = self.client.get("/api/refunds/").data["results"][0]
        self.assertEqual(refund["allocations"][0]["department_name"], "Laboratory")

    def test_the_audit_row_tells_the_whole_story(self):
        charge = self.charge("4000")
        self.pay("4000")
        self.post(self.cashier, charge, "cancel-and-refund", reason="Test never run",
                  amount="4000.00")

        entry = AuditLog.objects.get(action="billing.cancel_and_refund")
        details = entry.details
        self.assertEqual(entry.actor, self.cashier)
        self.assertIsNotNone(entry.created_at)
        self.assertEqual(entry.object_id, charge.pk)
        self.assertEqual(details["patient"], self.patient.display_name)
        self.assertEqual(details["patient_number"], self.patient.patient_number)
        self.assertEqual(details["service"], "Laboratory: FBC")
        self.assertEqual(details["department"], "Laboratory")
        self.assertEqual(details["charge"], charge.pk)
        self.assertEqual(details["original_charge"], "4000.00")
        self.assertEqual(details["amount"], "4000.00")
        self.assertEqual(details["amount_paid"], "4000.00")
        self.assertEqual(details["amount_refunded"], "4000.00")
        self.assertEqual(details["cancellation_reason"], "Test never run")
        self.assertEqual(details["reason"], "Test never run")
        self.assertEqual(details["refund_reason"], "Test never run")
        self.assertEqual(details["resulting_status"], "cancelled")
        self.assertEqual(details["outstanding_after"], "0.00")
        self.assertEqual(len(details["refunds"]), 1)
        self.assertEqual(details["refunds"][0]["amount"], "4000.00")
        self.assertIn("not used", details["summary"])
        self.assertIn("refunded", details["summary"])

    def test_a_plain_cancellation_is_audited_as_moving_no_money(self):
        charge = self.charge("4000")
        self.post(self.cashier, charge, "cancel", reason="Patient did not attend")
        details = AuditLog.objects.get(action="billing.charge_cancelled").details
        self.assertEqual(details["amount_refunded"], "0.00")
        self.assertEqual(details["resulting_status"], "cancelled")
        self.assertEqual(details["patient_number"], self.patient.patient_number)
        self.assertIn("no money", details["summary"])

    def test_the_refund_register_says_which_refunds_came_from_a_cancellation(self):
        cancelled = self.charge("4000")
        kept = self.charge("2000", "General Consultation", "consultation")
        payment = self.pay("6000")
        cancel_and_refund(charge=cancelled, reason="Never run", actor=self.cashier)
        refund_payment(payment=payment, amount="500", reason="Goodwill", processed_by=self.cashier)

        self.client.force_authenticate(self.cashier)
        rows = {r["amount"]: r for r in self.client.get("/api/refunds/").data["results"]}
        self.assertTrue(rows["4000.00"]["from_cancellation"])
        self.assertFalse(rows["500.00"]["from_cancellation"])
        kept.refresh_from_db()
        self.assertEqual(kept.status, "partial")

    def test_one_decision_is_one_notification_per_desk_and_nothing_else(self):
        charge = self.charge("4000")
        self.pay("4000")
        Notification.objects.all().delete()

        self.post(self.cashier, charge, "cancel-and-refund", reason="Never run", amount="4000.00")

        notes = Notification.objects.all()
        # The desks that work money, minus the cashier who did it.
        self.assertEqual(sorted(n.recipient.username for n in notes), ["acc", "boss", "rec"])
        self.assertEqual({n.title.split(":")[0] for n in notes}, {"Service cancelled & refunded"})
        for other in ("REFUND", "Bill cancelled", "Paid in full", "Part payment"):
            self.assertFalse(notes.filter(title__startswith=other).exists(), other)
        # Every recipient — reception too — can open where it points.
        self.assertEqual({n.action_url for n in notes}, {"/transactions"})

    def test_a_plain_cancellation_is_one_notification_per_desk(self):
        charge = self.charge("4000")
        Notification.objects.all().delete()
        self.post(self.cashier, charge, "cancel", reason="Never run")
        self.assertEqual(Notification.objects.count(), 3)
        self.assertEqual({n.title.split(":")[0] for n in Notification.objects.all()},
                         {"Bill cancelled"})


# ---------------------------------------------------------------------------
# Who may do which
# ---------------------------------------------------------------------------

class TwoDecisionsTwoGroups(Desk):
    def test_being_able_to_cancel_is_not_being_able_to_refund(self):
        """
        Today CANCEL_ROLES and REFUND_ROLES hold the same people. Pull them
        apart and the boundary must follow: a cashier who may cancel but not
        refund can withdraw an unpaid bill, and cannot take a paid one's money
        out of the drawer by cancelling it.
        """
        unpaid = self.charge("1000", "Card", "card")
        with mock.patch("apps.billing.views.REFUND_ROLES", ["accountant"]):
            self.assertEqual(self.post(self.cashier, unpaid, "cancel", reason="x").status_code, 200)

            paid = self.charge("4000")
            self.pay("4000")
            response = self.post(self.cashier, paid, "cancel-and-refund", reason="x",
                                 amount="4000.00")
            self.assertEqual(response.status_code, 403)
            paid.refresh_from_db()
            self.assertEqual(paid.status, "paid")
            self.assertFalse(Refund.objects.exists())

            self.assertEqual(self.post(self.accountant, paid, "cancel-and-refund", reason="x",
                                       amount="4000.00").status_code, 200)

    def test_being_able_to_refund_is_not_being_able_to_cancel(self):
        charge = self.charge("4000")
        self.pay("4000")
        with mock.patch("apps.billing.views.CANCEL_ROLES", ["accountant"]):
            self.assertEqual(self.post(self.cashier, charge, "cancel-and-refund", reason="x",
                                       amount="4000.00").status_code, 403)

    def test_the_counter_the_pharmacy_and_the_wards_reach_neither(self):
        others = [User.objects.create_user(username=role, password="t", role=role)
                  for role in ("pharmacist", "doctor", "nurse", "laboratory")] + [self.reception]
        charge = self.charge("4000")
        self.pay("4000")
        for user in others:
            with self.subTest(user.role):
                self.client.force_authenticate(user)
                for path in ("cancel", "cancel-and-refund"):
                    self.assertEqual(self.post(user, charge, path, reason="x").status_code, 403)
                self.assertEqual(self.client.get("/api/charges/cancellation-summary/").status_code, 403)
                self.assertEqual(self.client.get("/api/refunds/summary/").status_code, 403)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid")

    def test_admin_reaches_both_and_anonymous_reaches_neither(self):
        charge = self.charge("4000")
        self.pay("4000")
        self.assertEqual(self.post(self.admin, charge, "cancel-and-refund", reason="x",
                                   amount="4000.00").status_code, 200)
        anonymous = APIClient()
        for url in ("/api/charges/cancellation-summary/", "/api/refunds/summary/"):
            self.assertIn(anonymous.get(url).status_code, (401, 403))


# ---------------------------------------------------------------------------
# The report, the ledger and the history afterwards
# ---------------------------------------------------------------------------

class TheFiguresAfterwards(Desk):
    def report(self):
        self.client.force_authenticate(self.cashier)
        response = self.client.get("/api/finance/report/")
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_scenario_a_gross_refund_net_outstanding_and_the_cancellation(self):
        charge = self.charge("4000")
        payment = self.pay("4000")
        cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

        data = self.report()
        self.assertEqual(data["collections"]["total"], D("4000.00"), "gross payment")
        self.assertEqual(data["collections"]["refunds"], D("4000.00"), "refund")
        self.assertEqual(data["collections"]["net"], D("0.00"), "net")
        self.assertEqual(data["outstanding_now"], D("0.00"), "outstanding")
        self.assertEqual(data["cancellations"], {"count": 1, "value": D("4000.00")})
        self.assertEqual(data["charges"]["gross"], D("0.00"), "a withdrawn bill is not billed revenue")
        self.assertTrue(data["reconciliation"]["balances"])

        lab = {row["key"]: row for row in data["departments"]}["laboratory"]
        self.assertEqual((lab["received"], lab["refunded"], lab["net_received"]),
                         (D("4000.00"), D("4000.00"), D("0.00")))

        # All three halves of the story are still there to be read.
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.get("/api/payments/", {"patient": self.patient.pk}).data["results"][0]["id"], payment.pk)
        self.assertEqual(self.client.get("/api/refunds/", {"patient": self.patient.pk}).data["count"], 1)
        cancelled = self.client.get("/api/charges/", {"patient": self.patient.pk, "status": "cancelled"}).data
        self.assertEqual(cancelled["results"][0]["id"], charge.pk)

    def test_scenario_e_by_department(self):
        consultation = self.charge("4000", "General Consultation", "consultation")
        laboratory = self.charge("6000", "Laboratory: FBC", "lab_test")
        self.pay("10000")
        cancel_and_refund(charge=laboratory, reason="Never run", actor=self.cashier)

        data = self.report()
        rows = {row["key"]: row for row in data["departments"]}
        self.assertEqual((rows["consultation"]["received"], rows["consultation"]["refunded"],
                          rows["consultation"]["net_received"]),
                         (D("4000.00"), D("0.00"), D("4000.00")))
        self.assertEqual((rows["laboratory"]["received"], rows["laboratory"]["refunded"],
                          rows["laboratory"]["net_received"]),
                         (D("6000.00"), D("6000.00"), D("0.00")))
        self.assertEqual(data["totals"]["refunded"], D("6000.00"))
        self.assertEqual(data["collections"]["net"], D("4000.00"))
        self.assertEqual(data["charges"]["gross"], D("4000.00"))
        self.assertEqual(data["charges"]["outstanding"], D("0.00"))
        self.assertEqual(data["outstanding_now"], D("0.00"))
        self.assertTrue(data["reconciliation"]["balances"])
        consultation.refresh_from_db()
        self.assertEqual(consultation.status, "paid")

    def test_the_patient_leaves_the_debtors_list_and_is_not_in_credit(self):
        charge = self.charge("4000")
        self.pay("2000")
        self.client.force_authenticate(self.cashier)
        owing = [row["patient"] for row in self.client.get("/api/ledgers/", {"owing": "true"}).data["results"]]
        self.assertIn(self.patient.pk, owing)

        cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)

        owing = [row["patient"] for row in self.client.get("/api/ledgers/", {"owing": "true"}).data["results"]]
        self.assertNotIn(self.patient.pk, owing)
        ledger = self.client.get("/api/ledgers/", {"patient": self.patient.pk}).data["results"][0]
        self.assertEqual(D(ledger["outstanding_balance"]), D("0.00"))
