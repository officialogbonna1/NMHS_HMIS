"""
`POST /api/payments/` with `charge` — money for one bill — as an attack surface.

The parameter lets the desk say which bill a payment settles (rule 58). It must
not become a way to move money onto somebody else's bill, past a role that may
not collect, or — for the pharmacy counter — onto a bill that is not the
pharmacy's. And everything downstream — allocations, the ledger, refunds, the
audit trail and `billing.integrity` — must read exactly as it does for any
other payment.

Every refusal here is checked for its footprint as well as its status: no
`Payment`, no allocation, no audit row, no ledger movement.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import integrity
from apps.billing.models import (Charge, PatientLedger, Payment, PaymentAllocation, Refund,
                                 RefundAllocation)
from apps.billing.services import (add_charge, cancel_charge, credit_returned_goods,
                                   ledger_totals, waive_charge)
from apps.core.models import AuditLog
from apps.inventory.testing import product, stock_the_pharmacy
from apps.patients.models import Patient
from apps.pharmacy.services import (PaymentRequired, create_prescription,
                                    dispense_prescription)

D = Decimal


class ChargeScopedPaymentSecurity(TestCase):
    def setUp(self):
        make = User.objects.create_user
        self.users = {role: make(username=role, password="t", role=role) for role in (
            "cashier", "accountant", "reception", "pharmacist", "admin", "hospital_admin",
            "doctor", "nurse", "laboratory", "radiology", "inventory_manager", "ward_manager")}
        self.cashier, self.pharmacist = self.users["cashier"], self.users["pharmacist"]
        self.mine = Patient.objects.create(first_name="Michael", last_name="John", sex="M",
                                           created_by=self.users["reception"])
        self.theirs = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                             created_by=self.users["reception"])

        drug = product("Paracetamol 500mg", unit_name="Tablet")
        stock_the_pharmacy(item=drug, quantity=100, actor=self.pharmacist, sale_price="20")
        self.script = create_prescription(patient=self.mine, doctor=self.users["doctor"],
                                          item=drug, quantity=10)            # ₦200
        self.my_rx = self.script.charge
        self.my_lab = self.charge(self.mine, "Laboratory: FBC", "3500", "lab_test")
        self.my_consult = self.charge(self.mine, "Consultation", "5000", "consultation")
        self.their_rx = create_prescription(patient=self.theirs, doctor=self.users["doctor"],
                                            item=drug, quantity=5).charge   # ₦100
        self.their_lab = self.charge(self.theirs, "Laboratory: MP", "2500", "lab_test")

    # -------------------------------------------------------------- helpers

    def charge(self, patient, description, amount, source_type):
        return add_charge(patient=patient, description=description, amount=D(amount),
                          created_by=self.users["reception"], source_type=source_type,
                          notify=False)

    def pay(self, user, *, patient=None, charge=None, amount="100", **extra):
        client = APIClient()
        client.force_authenticate(user)
        body = {"patient": (patient or self.mine).pk, "amount": amount, "method": "cash", **extra}
        if charge is not None:
            body["charge"] = charge if isinstance(charge, (int, str)) else charge.pk
        return client.post("/api/payments/", body, format="json")

    def footprint(self):
        """Everything a payment writes. A refusal must leave all of it alone."""
        return (Payment.objects.count(), PaymentAllocation.objects.count(),
                AuditLog.objects.filter(action="billing.payment_recorded").count(),
                sorted(Charge.objects.values_list("pk", "amount_paid", "status")),
                sorted(PatientLedger.objects.values_list("patient_id", "total_payments")))

    def assertRefused(self, response, status, *, before):
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(self.footprint(), before)

    def assertBooksBalance(self):
        """`billing.integrity` — the same checks `manage.py billing_integrity` runs."""
        report = integrity.report()
        for check in ("untraceable_paid_charges", "unallocated_payments", "negative_ledgers",
                      "ledger_drift"):
            self.assertEqual(report[check], [], check)
        for patient in (self.mine, self.theirs):
            ledger = PatientLedger.objects.get(patient=patient)
            self.assertEqual((ledger.total_charges, ledger.total_payments,
                              ledger.total_adjustments), ledger_totals(patient))


class TheChargeMustBeThisPatientsTests(ChargeScopedPaymentSecurity):
    def test_another_patients_charge_is_refused_and_nothing_is_written(self):
        before = self.footprint()
        response = self.pay(self.cashier, patient=self.mine, charge=self.their_lab, amount="100")
        self.assertRefused(response, 400, before=before)
        self.assertIn("another patient", response.data["detail"])

    def test_naming_them_as_the_patient_does_not_reach_my_bill_either(self):
        before = self.footprint()
        self.assertRefused(self.pay(self.cashier, patient=self.theirs, charge=self.my_rx,
                                    amount="100"), 400, before=before)

    def test_a_charge_that_does_not_exist_is_refused(self):
        before = self.footprint()
        self.assertRefused(self.pay(self.cashier, charge=999999), 400, before=before)
        self.assertRefused(self.pay(self.cashier, charge="not-an-id"), 400, before=before)

    def test_another_patients_charge_is_refused_for_every_collecting_role(self):
        for role in ("cashier", "accountant", "reception", "admin", "hospital_admin",
                     "pharmacist"):
            with self.subTest(role=role):
                before = self.footprint()
                target = self.their_rx       # a pharmacy bill, so even the counter may name it
                response = self.pay(self.users[role], patient=self.mine, charge=target,
                                    amount="50")
                self.assertIn(response.status_code, (400, 403), response.data)
                self.assertEqual(self.footprint(), before)
        self.assertBooksBalance()

    def test_a_bill_that_owes_nothing_cannot_take_money(self):
        waive_charge(charge=self.my_consult, reason="Staff", approved_by=self.cashier)
        cancel_charge(charge=self.my_lab, cancelled_by=self.cashier, reason="Not run")
        for charge in (self.my_consult, self.my_lab):
            with self.subTest(charge=charge.description):
                before = self.footprint()
                self.assertRefused(self.pay(self.cashier, charge=charge, amount="10"), 400,
                                   before=before)

    def test_it_cannot_overpay_the_bill_it_names(self):
        before = self.footprint()
        response = self.pay(self.cashier, charge=self.my_rx, amount="200.01")
        self.assertRefused(response, 400, before=before)
        self.assertIn("would exceed", response.data["detail"])

    def test_goods_returned_come_off_what_may_be_paid(self):
        """A POS charge with goods brought back owes less, and cannot be paid its face value."""
        sale = self.charge(self.mine, "POS sale", "1000", "pos_sale")
        credit_returned_goods(charge=sale, amount=D("400"), reason="Unopened",
                              approved_by=self.cashier)
        before = self.footprint()
        self.assertRefused(self.pay(self.cashier, charge=sale, amount="1000"), 400, before=before)
        self.assertEqual(self.pay(self.cashier, charge=sale, amount="600").status_code, 201)
        sale.refresh_from_db()
        # Nothing left on it — and the stored status agrees with the derived
        # one now that `_settled_status` counts returned goods.
        self.assertEqual((sale.balance, sale.status, sale.settlement_status),
                         (D("0.00"), "paid", "paid"))
        self.assertBooksBalance()


class WhoMayCollectIsUnchangedTests(ChargeScopedPaymentSecurity):
    def test_roles_that_do_not_collect_are_refused_with_or_without_a_charge(self):
        for role in ("doctor", "nurse", "laboratory", "radiology", "inventory_manager",
                     "ward_manager"):
            for charge in (None, self.my_rx):
                with self.subTest(role=role, charge=bool(charge)):
                    before = self.footprint()
                    self.assertRefused(self.pay(self.users[role], charge=charge), 403,
                                       before=before)

    def test_unauthenticated_is_refused(self):
        before = self.footprint()
        response = APIClient().post("/api/payments/", {"patient": self.mine.pk, "amount": "10",
                                                       "charge": self.my_rx.pk}, format="json")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.footprint(), before)

    def test_the_desks_may_name_any_of_the_patients_bills(self):
        for role, charge in (("cashier", self.my_lab), ("accountant", self.my_consult),
                             ("reception", self.my_lab), ("admin", self.my_consult),
                             ("hospital_admin", self.my_rx)):
            with self.subTest(role=role):
                response = self.pay(self.users[role], charge=charge, amount="10")
                self.assertEqual(response.status_code, 201, response.data)
        self.assertBooksBalance()

    def test_the_channel_is_still_stamped_from_the_role_not_the_body(self):
        response = self.pay(self.cashier, charge=self.my_rx, amount="10", channel="pharmacy")
        self.assertEqual(response.data["channel"], "cashier")
        response = self.pay(self.pharmacist, charge=self.my_rx, amount="10", channel="cashier")
        self.assertEqual(response.data["channel"], "pharmacy")
        self.assertEqual(Payment.objects.get(pk=response.data["id"]).received_by, self.pharmacist)


class ThePharmacyCounterNamesOnlyPharmacyBillsTests(ChargeScopedPaymentSecurity):
    def test_the_pharmacist_pays_a_prescription_bill(self):
        response = self.pay(self.pharmacist, charge=self.my_rx, amount="200")
        self.assertEqual(response.status_code, 201, response.data)
        self.my_rx.refresh_from_db()
        self.assertEqual(self.my_rx.status, "paid")

    def test_the_pharmacist_cannot_steer_money_onto_a_laboratory_or_consultation_bill(self):
        for charge in (self.my_lab, self.my_consult):
            with self.subTest(charge=charge.description):
                before = self.footprint()
                response = self.pay(self.pharmacist, charge=charge, amount="100")
                self.assertRefused(response, 403, before=before)
                self.assertEqual(response.data["code"], "not_a_pharmacy_charge")

    def test_the_pharmacists_ordinary_payment_is_unchanged_oldest_first(self):
        """No `charge`: on the account, oldest bill first — exactly as before."""
        response = self.pay(self.pharmacist, amount="200")
        self.assertEqual(response.status_code, 201, response.data)
        allocated = list(PaymentAllocation.objects.filter(payment_id=response.data["id"])
                         .values_list("charge_id", "amount"))
        self.assertEqual(allocated, [(self.my_rx.pk, D("200.00"))])   # the oldest open bill
        self.assertBooksBalance()


class TheBooksStillReadCorrectlyTests(ChargeScopedPaymentSecurity):
    def test_the_allocation_and_the_ledger(self):
        ledger_before = PatientLedger.objects.get(patient=self.mine).outstanding_balance
        response = self.pay(self.cashier, charge=self.my_lab, amount="1500")
        self.assertEqual(response.status_code, 201, response.data)
        payment = Payment.objects.get(pk=response.data["id"])
        self.assertEqual(list(payment.allocations.values_list("charge_id", "amount")),
                         [(self.my_lab.pk, D("1500.00"))])
        for charge, paid, status in ((self.my_lab, "1500.00", "partial"),
                                     (self.my_rx, "0.00", "unpaid"),
                                     (self.my_consult, "0.00", "unpaid"),
                                     (self.their_lab, "0.00", "unpaid")):
            charge.refresh_from_db()
            self.assertEqual((charge.amount_paid, charge.status), (D(paid), status),
                             charge.description)
        self.assertEqual(PatientLedger.objects.get(patient=self.mine).outstanding_balance,
                         ledger_before - D("1500"))
        self.assertBooksBalance()

    def test_the_audit_row_names_the_bill(self):
        response = self.pay(self.cashier, charge=self.my_rx, amount="200")
        row = AuditLog.objects.get(action="billing.payment_recorded", object_id=response.data["id"])
        self.assertEqual((row.actor, row.details["charge"]), (self.cashier, self.my_rx.pk))
        plain = self.pay(self.cashier, amount="100")
        row = AuditLog.objects.get(action="billing.payment_recorded", object_id=plain.data["id"])
        self.assertIsNone(row.details["charge"])

    def test_a_refund_comes_back_off_that_bill_and_locks_the_script_again(self):
        paid = self.pay(self.cashier, charge=self.my_rx, amount="200")
        payment_id = paid.data["id"]
        client = APIClient()
        client.force_authenticate(self.cashier)
        refunded = client.post(f"/api/payments/{payment_id}/refund/",
                               {"amount": "200", "reason": "Patient declined"}, format="json")
        self.assertIn(refunded.status_code, (200, 201), refunded.data)

        refund = Refund.objects.get(payment_id=payment_id)
        self.assertEqual(list(RefundAllocation.objects.filter(refund=refund)
                              .values_list("charge_id", "amount")), [(self.my_rx.pk, D("200.00"))])
        self.my_rx.refresh_from_db()
        self.assertEqual((self.my_rx.amount_paid, self.my_rx.status), (D("0.00"), "unpaid"))
        self.assertTrue(AuditLog.objects.filter(action="billing.refund").exists())
        # The money went back, so the script is owed again and stays on the shelf.
        with self.assertRaises(PaymentRequired):
            dispense_prescription(prescription=self.script, pharmacist=self.pharmacist)
        self.assertBooksBalance()

    def test_the_pharmacist_still_cannot_refund(self):
        paid = self.pay(self.pharmacist, charge=self.my_rx, amount="200")
        client = APIClient()
        client.force_authenticate(self.pharmacist)
        response = client.post(f"/api/payments/{paid.data['id']}/refund/",
                               {"amount": "200", "reason": "x"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Refund.objects.exists())
