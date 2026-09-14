"""
Can a charge hold money that no `PaymentAllocation` accounts for?

Not through the application. This file holds that answer in three ways: every
path that takes a payment writes the allocations in the same transaction; no
module outside `billing/services.py` writes `Charge.amount_paid` at all; and the
checks in `billing.integrity` really do find the state when it is forced into
the database by hand — which is also how the combined Cancel & refund is shown
to refuse it rather than guess.
"""
import ast
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import integrity
from apps.billing.models import Charge, PatientLedger, Payment, PaymentAllocation, Refund
from apps.billing.services import (UntraceablePayment, add_charge, cancel_and_refund,
                                   record_payment, refund_charge, refund_payment)
from apps.patients.models import Patient

D = Decimal
APPS = Path(__file__).resolve().parents[2]


class Base(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.patient = self.new_patient("Ada")

    def new_patient(self, name):
        return Patient.objects.create(first_name=name, last_name="Obi", sex="F",
                                      created_by=self.reception)

    def charge(self, amount, patient=None, description="Laboratory: FBC", source="lab_test"):
        return add_charge(patient=patient or self.patient, description=description,
                          amount=amount, created_by=self.reception, source_type=source)

    def assertClean(self):
        found = integrity.report()
        for key in ("untraceable_paid_charges", "allocation_less_paid_charges",
                    "unallocated_payments", "negative_ledgers", "ledger_drift"):
            self.assertEqual(found[key], [], key)


class EveryPaymentPathAllocates(Base):
    def test_the_front_desk_the_cash_desk_and_the_pharmacy_each_allocate_what_they_take(self):
        client = APIClient()
        for user, channel in ((self.reception, "front_desk"), (self.cashier, "cashier"),
                              (self.pharmacist, "pharmacy")):
            with self.subTest(user.role):
                patient = self.new_patient(user.role)
                first = self.charge("1500", patient)
                second = self.charge("2500", patient, "Medication: Amoxicillin", "prescription")
                client.force_authenticate(user)
                response = client.post("/api/payments/", {"patient": patient.pk, "amount": "3000",
                                                          "method": "cash"}, format="json")
                self.assertEqual(response.status_code, 201, response.data)

                payment = Payment.objects.get(pk=response.data["id"])
                self.assertEqual(payment.channel, channel)
                self.assertEqual(payment.allocations.aggregate(t=Sum("amount"))["t"], D("3000.00"))
                for charge in (first, second):
                    charge.refresh_from_db()
                    allocated = charge.allocations.aggregate(t=Sum("amount"))["t"] or D("0")
                    self.assertEqual(charge.amount_paid, allocated)
        self.assertClean()

    def test_a_payment_never_lands_without_its_allocations(self):
        """If writing the allocations fails, the payment and `amount_paid` go with it."""
        charge = self.charge("4000")
        with mock.patch.object(PaymentAllocation.objects, "bulk_create",
                               side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                record_payment(patient=self.patient, amount="4000", received_by=self.cashier)
        charge.refresh_from_db()
        self.assertEqual(charge.amount_paid, D("0.00"))
        self.assertFalse(Payment.objects.exists())

    def test_refunds_and_cancellations_keep_every_charge_traceable(self):
        lab = self.charge("4000")
        consult = self.charge("2000", description="General Consultation", source="consultation")
        scan = self.charge("3000", description="Ultrasound: Pelvic", source="ultrasound")
        first = record_payment(patient=self.patient, amount="5000", received_by=self.cashier)
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)

        refund_payment(payment=first, amount="1000", reason="Overcharged", processed_by=self.cashier)
        refund_charge(charge=consult, reason="Goodwill", actor=self.cashier, amount="500")
        cancel_and_refund(charge=scan, reason="Scan never done", actor=self.cashier)
        record_payment(patient=self.patient, amount="1500", received_by=self.cashier)
        cancel_and_refund(charge=lab, reason="Sample rejected", actor=self.cashier)

        self.assertClean()
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance, D("0.00"))

    def test_only_the_billing_services_write_amount_paid(self):
        """
        Every assignment to `.amount_paid`, every `amount_paid=` keyword and
        every `update_fields` / `bulk_update` naming it, across the whole
        application outside tests and migrations. One file may appear: the
        one that writes the allocation in the same transaction.
        """
        writers = {}
        for path in sorted(APPS.rglob("*.py")):
            parts = path.relative_to(APPS).parts
            if "tests" in parts or "migrations" in parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
                if _writes_amount_paid(node):
                    writers.setdefault(str(path.relative_to(APPS.parent)), []).append(node.lineno)
        self.assertEqual(set(writers), {"apps/billing/services.py"}, writers)


#: Calls that write a row when handed `amount_paid=`. A filter or an ordinary
#: function taking a keyword of that name (an audit helper) reads it and is not
#: a write.
_ORM_WRITES = {"create", "update", "get_or_create", "update_or_create"}


def _names_amount_paid(node):
    return any(isinstance(n, ast.Constant) and n.value == "amount_paid" for n in ast.walk(node))


def _is_orm_write(call):
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in _ORM_WRITES:
        return True
    return isinstance(func, ast.Name) and func.id == "Charge"     # Charge(amount_paid=…)


def _writes_amount_paid(node):
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        targets = [node.target]
    else:
        targets = []
    if any(isinstance(t, ast.Attribute) and t.attr == "amount_paid" for t in targets):
        return True
    if isinstance(node, ast.Call):
        for keyword in node.keywords:
            if keyword.arg == "amount_paid" and _is_orm_write(node):
                return True
            if keyword.arg == "update_fields" and _names_amount_paid(keyword.value):
                return True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "bulk_update":
            return any(_names_amount_paid(arg) for arg in node.args)
    return False


class TheChecksFindWhatTheyLookFor(Base):
    """The state is forced by hand here — nothing in the application can make it."""

    def test_a_paid_charge_with_no_allocation_is_found_and_refused(self):
        charge = self.charge("4000")
        Charge.objects.filter(pk=charge.pk).update(amount_paid=D("1500"), status="partial")

        found = integrity.untraceable_paid_charges()
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["charge"], found[0]["untraceable"], found[0]["has_allocation"]),
                         (charge.pk, D("1500.00"), False))
        self.assertEqual(len(integrity.report()["allocation_less_paid_charges"]), 1)

        client = APIClient()
        client.force_authenticate(self.cashier)
        response = client.post(f"/api/charges/{charge.pk}/cancel-and-refund/",
                               {"reason": "Never run", "amount": "1500.00"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "untraceable_payment")
        charge.refresh_from_db()
        self.assertEqual((charge.status, charge.amount_paid), ("partial", D("1500.00")))
        self.assertFalse(Refund.objects.exists(), "no payment was guessed at")

    def test_a_partly_traceable_charge_is_found_and_refused(self):
        charge = self.charge("4000")
        record_payment(patient=self.patient, amount="1000", received_by=self.cashier)
        Charge.objects.filter(pk=charge.pk).update(amount_paid=D("1500"))

        found = integrity.untraceable_paid_charges()
        self.assertEqual((found[0]["traceable"], found[0]["untraceable"], found[0]["has_allocation"]),
                         (D("1000.00"), D("500.00"), True))
        with self.assertRaises(UntraceablePayment):
            cancel_and_refund(charge=charge, reason="Never run", actor=self.cashier)
        with self.assertRaises(UntraceablePayment):
            refund_charge(charge=charge, reason="Goodwill", actor=self.cashier, amount="1500")
        self.assertFalse(Refund.objects.exists())

    def test_the_charge_row_warns_the_screen_before_anybody_presses(self):
        charge = self.charge("4000")
        Charge.objects.filter(pk=charge.pk).update(amount_paid=D("1500"), status="partial")
        client = APIClient()
        client.force_authenticate(self.cashier)
        row = client.get("/api/charges/").data["results"][0]
        self.assertEqual(row["untraceable_amount"], "1500.00")

    def test_an_unallocated_payment_a_patient_in_credit_and_a_drifted_ledger_are_found(self):
        self.charge("1000")
        Payment.objects.create(patient=self.patient, amount=D("2500"), received_by=self.cashier)
        self.assertEqual([row["unallocated"] for row in integrity.unallocated_payments()],
                         [D("2500.00")])

        PatientLedger.objects.filter(patient=self.patient).update(total_payments=D("2500"))
        self.assertEqual(integrity.negative_ledgers(),
                         [{"patient": self.patient.pk, "outstanding": D("-1500.00")}])
        self.assertEqual(len(integrity.ledger_drift()), 0,
                         "stored matches rows once the payment row really exists")

        PatientLedger.objects.filter(patient=self.patient).update(total_charges=D("9000"))
        self.assertEqual([row["patient"] for row in integrity.ledger_drift()], [self.patient.pk])

    def test_the_command_reports_and_can_fail_a_deployment(self):
        out = StringIO()
        call_command("billing_integrity", stdout=out)
        self.assertIn("Paid charges with no PaymentAllocation: 0", out.getvalue())
        call_command("billing_integrity", "--strict", stdout=StringIO())   # clean: no exit

        charge = self.charge("4000")
        Charge.objects.filter(pk=charge.pk).update(amount_paid=D("1000"), status="partial")
        out = StringIO()
        with self.assertRaises(SystemExit):
            call_command("billing_integrity", "--strict", stdout=out)
        self.assertIn("Paid charges with no PaymentAllocation: 1", out.getvalue())
