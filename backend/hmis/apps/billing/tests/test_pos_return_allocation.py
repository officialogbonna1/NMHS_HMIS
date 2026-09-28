"""
A POS bill whose goods came back owes nothing, and money on the account never
lands on it.

The bug this holds shut: a registered patient bought ₦1,000 at the till,
brought ₦400 back and was refunded. The bill's balance was ₦0, but its stored
`status` still read "partial" — `_settled_status` ignored returned goods — and
`allocate_to_charges`, which picks open bills by that status and worked out
what was due without the returned column either, then put ₦400 of the next
₦500 consultation payment onto the POS bill. The consultation was left owing
₦400 that the patient had paid.

Both now read `billing.services._due`, which counts returned goods, and the
stored status agrees with the derived `settlement_status`.
"""
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.billing import integrity
from apps.billing.models import Charge, PatientLedger, PaymentAllocation
from apps.billing.services import add_charge, ledger_totals, record_payment
from apps.inventory.models import PHARMACY, StockRecord
from apps.inventory.testing import product, stock_the_pharmacy
from apps.patients.models import Patient
from apps.sales import services as pos

D = Decimal


class PosCase(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Michael", last_name="John", sex="M",
                                              created_by=self.reception)
        self.drug = product("Vitamin C 100mg", unit_name="Tablet")
        self.batch = stock_the_pharmacy(item=self.drug, quantity=50, actor=self.cashier,
                                        sale_price="100")
        pos.open_register(operator=self.cashier, opening_float="0")

    def sell(self, quantity):
        sale, _ = pos.complete_sale(operator=self.cashier, customer_type="patient",
                                    patient=self.patient,
                                    lines=[{"item": self.drug.pk, "quantity": quantity}])
        return sale

    def consultation(self, amount="500"):
        return add_charge(patient=self.patient, description="Consultation", amount=D(amount),
                          created_by=self.reception, source_type="consultation", notify=False)

    def outstanding(self):
        return PatientLedger.objects.get(patient=self.patient).outstanding_balance

    def assertBooksBalance(self):
        report = integrity.report()
        for check in ("untraceable_paid_charges", "unallocated_payments", "negative_ledgers",
                      "ledger_drift"):
            self.assertEqual(report[check], [], check)
        ledger = PatientLedger.objects.get(patient=self.patient)
        self.assertEqual((ledger.total_charges, ledger.total_payments, ledger.total_adjustments),
                         ledger_totals(self.patient))



class PosReturnAllocationTests(PosCase):
    def test_a_returned_pos_bill_takes_none_of_the_next_payment(self):
        # 1. A ₦1,000 POS sale for a registered patient, paid at the till.
        sale = self.sell(10)
        bill = Charge.objects.get(pk=sale.charge_id)   # as the till left it
        self.assertEqual((bill.amount, bill.amount_paid, bill.status), (D("1000.00"), D("1000.00"), "paid"))

        # 2. ₦400 of goods come back and the money goes back.
        pos.process_return(sale=sale, operator=self.cashier,
                           lines=[{"sale_item": sale.items.get().pk, "quantity": 4}],
                           reason="Unopened")

        # 3. and 4. Nothing is owed on it, and the stored status says so too.
        bill.refresh_from_db()
        self.assertEqual((bill.amount_paid, bill.amount_returned), (D("600.00"), D("400.00")))
        self.assertEqual(bill.balance, D("0.00"))
        self.assertEqual(bill.status, "paid")                 # was "partial" before the fix
        self.assertEqual(bill.settlement_status, "paid")
        self.assertNotIn(bill.pk, Charge.objects.filter(status__in=["unpaid", "partial"])
                                                .values_list("pk", flat=True))
        self.assertEqual(self.outstanding(), D("0.00"))

        # 5. and 6. A later ₦500 payment on the account goes to the consultation, all of it.
        consult = self.consultation("500")
        payment = record_payment(patient=self.patient, amount=D("500"), received_by=self.cashier)
        self.assertEqual(list(PaymentAllocation.objects.filter(payment=payment)
                              .values_list("charge_id", "amount")),
                         [(consult.pk, D("500.00"))])
        self.assertFalse(PaymentAllocation.objects.filter(payment=payment, charge=bill).exists())

        # 7. Every figure still reads correctly.
        bill.refresh_from_db()
        consult.refresh_from_db()
        self.assertEqual((bill.amount_paid, bill.balance, bill.status), (D("600.00"), D("0.00"), "paid"))
        self.assertEqual((consult.amount_paid, consult.balance, consult.status),
                         (D("500.00"), D("0.00"), "paid"))
        self.assertEqual(self.outstanding(), D("0.00"))
        self.assertBooksBalance()

    def test_the_payment_is_capped_at_what_is_really_owed(self):
        """With the POS bill owing nothing, ₦501 is more than the patient owes."""
        sale = self.sell(10)
        pos.process_return(sale=sale, operator=self.cashier,
                           lines=[{"sale_item": sale.items.get().pk, "quantity": 4}],
                           reason="Unopened")
        self.consultation("500")
        with self.assertRaises(ValueError):
            record_payment(patient=self.patient, amount=D("501"), received_by=self.cashier)

    def test_a_return_of_everything_leaves_nothing_owed_and_nothing_taken(self):
        sale = self.sell(5)
        pos.process_return(sale=sale, operator=self.cashier,
                           lines=[{"sale_item": sale.items.get().pk, "quantity": 5}],
                           reason="Wrong item")
        bill = Charge.objects.get(pk=sale.charge_id)
        self.assertEqual((bill.amount_paid, bill.amount_returned, bill.balance),
                         (D("0.00"), D("500.00"), D("0.00")))
        self.assertNotIn(bill.status, ("unpaid", "partial"))
        consult = self.consultation("300")
        payment = record_payment(patient=self.patient, amount=D("300"), received_by=self.cashier)
        self.assertEqual(list(payment.allocations.values_list("charge_id", flat=True)), [consult.pk])
        self.assertBooksBalance()


class APosSaleWithoutReturnsIsUnchangedTests(PosCase):
    def test_a_registered_sale_is_paid_in_full_and_takes_no_later_payment(self):
        sale = self.sell(3)
        bill = Charge.objects.get(pk=sale.charge_id)
        self.assertEqual((bill.amount, bill.amount_paid, bill.amount_returned, bill.status,
                          bill.balance),
                         (D("300.00"), D("300.00"), D("0.00"), "paid", D("0.00")))
        self.assertEqual(list(sale.payment.allocations.values_list("charge_id", "amount")),
                         [(bill.pk, D("300.00"))])
        self.assertEqual(self.outstanding(), D("0.00"))
        consult = self.consultation("500")
        payment = record_payment(patient=self.patient, amount=D("200"), received_by=self.cashier)
        self.assertEqual(list(payment.allocations.values_list("charge_id", "amount")),
                         [(consult.pk, D("200.00"))])
        consult.refresh_from_db()
        self.assertEqual(consult.status, "partial")
        self.assertEqual(StockRecord.objects.get(batch=self.batch, location__code=PHARMACY)
                         .quantity, 47)
        self.assertBooksBalance()

    def test_a_walk_in_sale_still_raises_no_bill(self):
        sale, _ = pos.complete_sale(operator=self.cashier,
                                    lines=[{"item": self.drug.pk, "quantity": 2}])
        self.assertIsNone(sale.charge)
        self.assertIsNone(sale.payment.patient)
        self.assertFalse(Charge.objects.exists())
