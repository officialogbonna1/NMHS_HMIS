"""
The pharmacy POS till, held to what it promises:

* a walk-in customer is sold to without becoming a patient;
* stock leaves the dispensing shelf only, earliest expiry first, never expired,
  through movements that name the sale — the same shelf and the same FEFO that
  prescription dispensing uses;
* the money is an ordinary `Payment` (a registered patient's also a Pharmacy
  charge), so every financial figure sees it without being told;
* discounts are approvals — a reason, an authorised role, and never a total at
  or below zero;
* a return quarantines the medicine and refunds through `refund_payment`,
  leaving the sale and its payment exactly as they were;
* and every refusal writes nothing at all.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import reporting
from apps.billing.models import (Adjustment, Charge, PatientLedger, Payment, PaymentAllocation,
                                 Refund)
from apps.billing.services import add_charge, record_payment, refund_payment
from apps.core.models import AuditLog
from apps.inventory.models import (PHARMACY, QUARANTINE, Batch, Item, ItemCategory, StockMovement,
                                   StockRecord)
from apps.inventory.testing import product, stock_the_pharmacy, stock_the_store
from apps.patients.models import Patient
from apps.pharmacy.services import available_quantity, create_prescription, dispense_prescription
from apps.sales import services
from apps.sales.models import Sale, SaleItem, SaleReturn

D = Decimal


class PosTestCase(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.pharmacist = User.objects.create_user(username="pharm", password="t", role="pharmacist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.paracetamol = product("Paracetamol 500mg", unit_name="tablet", category_name="Analgesics",
                                   sku="PH-PARA-500", barcode="6001234500012")
        self.amoxil = product("Amoxicillin 500mg", unit_name="capsule", category_name="Antibiotics")
        # Two lots of paracetamol at two prices, the earlier expiry cheaper, so a
        # sale that crosses them shows FEFO in its price as well as its stock.
        self.para_early = stock_the_pharmacy(item=self.paracetamol, quantity=4, actor=self.pharmacist,
                                             batch_no="P-EARLY", sale_price="50", expiry_days=30)
        self.para_late = stock_the_pharmacy(item=self.paracetamol, quantity=100, actor=self.pharmacist,
                                            batch_no="P-LATE", sale_price="60", expiry_days=300)
        self.amox = stock_the_pharmacy(item=self.amoxil, quantity=50, actor=self.pharmacist,
                                       batch_no="A-1", sale_price="200", expiry_days=200)
        self.register = services.open_register(operator=self.cashier, opening_float="1000")

    def held(self, batch, code=PHARMACY):
        record = StockRecord.objects.filter(batch=batch, location__code=code).first()
        return record.quantity if record else 0

    def sell(self, lines, operator=None, **kwargs):
        sale, _ = services.complete_sale(operator=operator or self.cashier, lines=lines, **kwargs)
        return sale

    def footprint(self):
        """Everything a sale writes — so a refusal can be shown to have written none of it."""
        return (Sale.objects.filter(status="completed").count(), Payment.objects.count(),
                Charge.objects.count(), StockMovement.objects.count(),
                sorted(StockRecord.objects.values_list("pk", "quantity")))


# ----------------------------------------------------------------- walk-in


class WalkInSaleTests(PosTestCase):
    def test_a_walk_in_customer_is_sold_to_without_becoming_a_patient(self):
        patients = Patient.objects.count()
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 2}],
                         customer_name="Musa Bello", customer_phone="08031234567")
        self.assertEqual((sale.status, sale.customer_type), ("completed", "walk_in"))
        self.assertIsNone(sale.patient)
        self.assertEqual(sale.customer_label, "Musa Bello")
        self.assertEqual(Patient.objects.count(), patients)
        self.assertIsNone(sale.charge)
        self.assertFalse(Charge.objects.exists())
        self.assertEqual(sale.total_amount, D("400"))
        self.assertIsNone(sale.payment.patient)
        self.assertEqual((sale.payment.amount, sale.payment.channel, sale.payment.reference),
                         (D("400"), "pharmacy", sale.reference))
        self.assertTrue(sale.reference.startswith("POS-"))

    def test_a_nameless_walk_in_reads_as_walk_in_customer(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 1}])
        self.assertEqual(sale.customer_label, "Walk-in customer")

    def test_several_products_each_priced_off_their_own_batches(self):
        sale = self.sell([{"item": self.paracetamol.pk, "quantity": 2},
                          {"item": self.amoxil.pk, "quantity": 3}])
        self.assertEqual(sale.lines.count(), 2)
        self.assertEqual((sale.subtotal, sale.total_amount, sale.payment.amount),
                         (D("700"), D("700"), D("700")))   # 2 × 50 + 3 × 200

    def test_the_same_product_twice_becomes_one_line(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 1},
                          {"item": self.amoxil.pk, "quantity": 2}])
        self.assertEqual(sale.lines.get().quantity, 3)
        self.assertEqual(self.held(self.amox), 47)

    def test_a_quantity_crosses_batches_earliest_expiry_first_at_each_batch_price(self):
        sale = self.sell([{"item": self.paracetamol.pk, "quantity": 6}])
        self.assertEqual((self.held(self.para_early), self.held(self.para_late)), (0, 98))
        pieces = list(sale.items.order_by("batch__expiry_date")
                      .values_list("batch__batch_no", "quantity", "unit_price"))
        self.assertEqual(pieces, [("P-EARLY", 4, D("50.00")), ("P-LATE", 2, D("60.00"))])
        self.assertEqual(sale.subtotal, D("320"))

    def test_every_unit_leaves_through_a_movement_that_names_the_sale(self):
        sale = self.sell([{"item": self.paracetamol.pk, "quantity": 6}])
        movements = StockMovement.objects.filter(reference=sale.reference)
        self.assertEqual(movements.count(), 2)
        self.assertEqual(set(movements.values_list("reason", flat=True)), {"sale"})
        self.assertEqual(set(movements.values_list("location__code", flat=True)), {PHARMACY})
        self.assertEqual(movements.aggregate(v=Sum("change"))["v"], -6)

    def test_an_expired_batch_is_never_sold(self):
        syrup = product("Cough Syrup", unit_name="bottle")
        expired = stock_the_pharmacy(item=syrup, quantity=10, actor=self.pharmacist, batch_no="OLD",
                                     expiry_days=10)
        stock_the_pharmacy(item=syrup, quantity=2, actor=self.pharmacist, batch_no="NEW", expiry_days=90)
        Batch.objects.filter(pk=expired.pk).update(expiry_date=timezone.localdate() - timedelta(days=1))

        sale = self.sell([{"item": syrup.pk, "quantity": 2}])
        self.assertEqual(list(sale.items.values_list("batch__batch_no", flat=True)), ["NEW"])
        with self.assertRaises(ValidationError):
            self.sell([{"item": syrup.pk, "quantity": 1}])
        self.assertEqual(self.held(expired), 10)

    def test_short_stock_refuses_the_whole_sale_and_writes_nothing(self):
        before = self.footprint()
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.paracetamol.pk, "quantity": 2},
                       {"item": self.amoxil.pk, "quantity": 51}])
        self.assertEqual(self.footprint(), before)

    def test_stock_in_the_main_store_cannot_be_sold_at_the_counter(self):
        cream = product("Hydrocortisone cream")
        stock_the_store(item=cream, quantity=40, actor=self.pharmacist, batch_no="HC-1")
        with self.assertRaises(ValidationError):
            self.sell([{"item": cream.pk, "quantity": 1}])

    def test_cash_change_is_what_was_handed_over_less_the_total(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 1}], payment_method="cash",
                         amount_tendered="500")
        self.assertEqual((sale.total_amount, sale.amount_tendered, sale.change_due),
                         (D("200"), D("500"), D("300")))
        # The payment is what the hospital kept, never the note it was handed.
        self.assertEqual(sale.payment.amount, D("200"))

    def test_cash_short_of_the_total_is_refused(self):
        before = self.footprint()
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.amoxil.pk, "quantity": 1}], payment_method="cash",
                      amount_tendered="150")
        self.assertEqual(self.footprint(), before)

    def test_a_card_payment_has_no_change(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 1}], payment_method="card",
                         amount_tendered="999")
        self.assertEqual((sale.payment.method, sale.change_due, sale.amount_tendered),
                         ("card", D("0"), D("200")))

    def test_a_double_tapped_complete_sells_once(self):
        token = str(uuid4())
        lines = [{"item": self.amoxil.pk, "quantity": 1}]
        first, created = services.complete_sale(operator=self.cashier, lines=lines, client_token=token)
        again, created_again = services.complete_sale(operator=self.cashier, lines=lines,
                                                      client_token=token)
        self.assertEqual((created, created_again, first.pk), (True, False, again.pk))
        self.assertEqual(self.held(self.amox), 49)
        self.assertEqual(Payment.objects.count(), 1)

    def test_a_held_sale_moves_no_stock_and_takes_no_money_until_completed(self):
        held = services.hold_sale(operator=self.cashier, lines=[{"item": self.amoxil.pk, "quantity": 3}],
                                  customer_name="Ada")
        self.assertEqual(held.status, "held")
        self.assertEqual(self.held(self.amox), 50)
        self.assertFalse(Payment.objects.exists())
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 3}], held_sale=held, customer_name="Ada")
        self.assertEqual((sale.pk, sale.reference), (held.pk, held.reference))
        self.assertEqual(self.held(self.amox), 47)

    def test_a_discarded_held_sale_cannot_be_completed(self):
        held = services.hold_sale(operator=self.cashier, lines=[{"item": self.amoxil.pk, "quantity": 1}])
        services.discard_held_sale(sale=held, actor=self.cashier)
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.amoxil.pk, "quantity": 1}], held_sale=held)
        self.assertEqual(self.held(self.amox), 50)

    def test_a_retired_product_is_not_sold(self):
        self.amoxil.is_active = False
        self.amoxil.save()
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.amoxil.pk, "quantity": 1}])

    def test_no_sale_without_an_open_register(self):
        services.close_register(register=self.register, actor=self.cashier, counted_cash="1000")
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.amoxil.pk, "quantity": 1}])


# --------------------------------------------------------------- discounts


class DiscountTests(PosTestCase):
    def test_a_percentage_discount(self):
        # ₦5,000 of amoxicillin, 10% off: ₦500 off, ₦4,500 paid.
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 25}],
                         discount={"type": "percent", "value": "10", "reason": "Staff purchase"})
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.total_amount),
                         (D("5000"), D("500"), D("4500")))
        self.assertEqual((sale.discount_type, sale.discount_value, sale.discount_reason),
                         ("percent", D("10"), "Staff purchase"))
        self.assertEqual(sale.discount_by, self.cashier)
        self.assertEqual(sale.payment.amount, D("4500"))
        self.assertTrue(AuditLog.objects.filter(action="pos.discount_applied", object_id=sale.pk).exists())

    def test_a_fixed_amount_discount(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 25}],
                         discount={"type": "amount", "value": "750", "reason": "Loyal customer"})
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.total_amount),
                         (D("5000"), D("750"), D("4250")))

    def test_a_discount_never_changes_the_product_price(self):
        self.sell([{"item": self.amoxil.pk, "quantity": 1}],
                  discount={"type": "percent", "value": "50", "reason": "Damaged box"})
        self.amox.refresh_from_db()
        self.assertEqual(self.amox.sale_price, D("200"))

    def test_line_discounts_and_a_sale_discount_combine_without_a_negative_line(self):
        sale = self.sell(
            [{"item": self.paracetamol.pk, "quantity": 2},
             {"item": self.amoxil.pk, "quantity": 25, "discount": {"type": "percent", "value": "10"}}],
            discount={"type": "amount", "value": "100"}, discount_reason="Bulk purchase")
        amox, para = sale.lines.order_by("item__name")
        self.assertEqual((amox.gross, amox.line_discount, amox.line_discount_type),
                         (D("5000"), D("500"), "percent"))
        self.assertEqual(para.line_discount, D("0"))
        # 5,100 − 500 on the line − 100 across the sale = 4,500.
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.total_amount),
                         (D("5100"), D("600"), D("4500")))
        self.assertEqual(amox.discount + para.discount, sale.discount_amount)
        self.assertEqual(amox.net + para.net, sale.total_amount)
        self.assertTrue(amox.net >= 0 and para.net >= 0)
        self.assertEqual(sale.discount_reason, "Bulk purchase")

    def test_rounding_on_a_near_total_discount_never_takes_a_line_below_zero(self):
        gauze, plaster = product("Gauze swab"), product("Plaster")
        stock_the_pharmacy(item=gauze, quantity=5, actor=self.pharmacist, batch_no="G-1", sale_price="0.01")
        stock_the_pharmacy(item=plaster, quantity=5, actor=self.pharmacist, batch_no="PL-1", sale_price="0.01")
        sale = self.sell([{"item": gauze.pk, "quantity": 1}, {"item": plaster.pk, "quantity": 1},
                          {"item": self.amoxil.pk, "quantity": 1}],
                         discount={"type": "amount", "value": "200.01", "reason": "Rounding check"})
        lines = list(sale.lines.all())
        self.assertEqual(sale.total_amount, D("0.01"))
        self.assertTrue(all(line.net >= 0 for line in lines))
        self.assertEqual(sum(line.discount for line in lines), D("200.01"))
        self.assertEqual(sum(line.net for line in lines), sale.total_amount)

    def test_a_line_discount_cannot_be_more_than_its_line(self):
        before = self.footprint()
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.paracetamol.pk, "quantity": 1,
                        "discount": {"type": "amount", "value": "60"}},
                       {"item": self.amoxil.pk, "quantity": 1}], discount_reason="Promo")
        self.assertEqual(self.footprint(), before)

    def test_a_discount_that_covers_the_whole_sale_is_refused(self):
        before = self.footprint()
        for discount in ({"type": "percent", "value": "100", "reason": "Free"},
                         {"type": "amount", "value": "200", "reason": "Free"},
                         {"type": "amount", "value": "250", "reason": "More than the sale"}):
            with self.subTest(discount=discount), self.assertRaises(ValidationError):
                self.sell([{"item": self.amoxil.pk, "quantity": 1}], discount=discount)
        self.assertEqual(self.footprint(), before)

    def test_malformed_discounts_are_refused(self):
        before = self.footprint()
        for discount in ({"type": "percent", "value": "101", "reason": "r"},
                         {"type": "percent", "value": "0", "reason": "r"},
                         {"type": "amount", "value": "-5", "reason": "r"},
                         {"type": "coupon", "value": "5", "reason": "r"},
                         {"type": "percent", "value": "ten", "reason": "r"}):
            with self.subTest(discount=discount), self.assertRaises(ValidationError):
                self.sell([{"item": self.amoxil.pk, "quantity": 1}], discount=discount)
        self.assertEqual(self.footprint(), before)

    def test_a_discount_needs_a_reason(self):
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.amoxil.pk, "quantity": 1}], discount={"type": "percent", "value": "5"})
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.amoxil.pk, "quantity": 1,
                        "discount": {"type": "percent", "value": "5"}}])
        self.assertEqual(self.held(self.amox), 50)

    def test_a_pharmacist_sells_but_cannot_discount(self):
        services.open_register(operator=self.pharmacist)
        before = self.footprint()
        with self.assertRaises(PermissionDenied):
            self.sell([{"item": self.amoxil.pk, "quantity": 1}], operator=self.pharmacist,
                      discount={"type": "percent", "value": "5", "reason": "Friend"})
        with self.assertRaises(PermissionDenied):
            self.sell([{"item": self.amoxil.pk, "quantity": 1, "discount": {"type": "amount", "value": "5"}}],
                      operator=self.pharmacist, discount_reason="Friend")
        self.assertEqual(self.footprint(), before)
        self.assertEqual(self.sell([{"item": self.amoxil.pk, "quantity": 1}],
                                   operator=self.pharmacist).total_amount, D("200"))


# ------------------------------------------------------ registered patients


class RegisteredPatientSaleTests(PosTestCase):
    def setUp(self):
        super().setUp()
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)

    def test_the_statement_shows_a_pharmacy_charge_its_discount_and_the_payment(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 5}], customer_type="patient",
                         patient=self.patient,
                         discount={"type": "percent", "value": "10", "reason": "Staff family"})
        charge = Charge.objects.get(pk=sale.charge_id)
        self.assertEqual((charge.amount, charge.amount_discounted, charge.amount_paid, charge.status),
                         (D("1000"), D("100"), D("900"), "paid"))
        self.assertEqual((charge.source_type, charge.department.code), ("pos_sale", "pharmacy"))
        self.assertTrue(Adjustment.objects.filter(charge=charge, kind="discount", amount=D("100")).exists())
        self.assertEqual(PaymentAllocation.objects.get(payment=sale.payment).charge, charge)
        self.assertEqual(sale.payment.patient, self.patient)
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance, D("0"))

    def test_the_payment_settles_the_medicine_and_never_an_older_bill(self):
        consult = add_charge(patient=self.patient, description="Consultation", amount="3000",
                             created_by=self.reception, source_type="consultation")
        self.sell([{"item": self.amoxil.pk, "quantity": 1}], customer_type="patient", patient=self.patient)
        consult.refresh_from_db()
        self.assertEqual((consult.amount_paid, consult.status), (D("0"), "unpaid"))
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance, D("3000"))

    def test_a_patient_sale_needs_the_patient(self):
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.amoxil.pk, "quantity": 1}], customer_type="patient", patient=None)

    def test_cash_above_the_total_is_change_and_never_puts_the_patient_in_credit(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 5}], customer_type="patient",
                         patient=self.patient, payment_method="cash", amount_tendered="1000",
                         discount={"type": "percent", "value": "10", "reason": "Staff family"})
        # 1,000 gross, 100 off: 900 due, 1,000 handed over, 100 handed back.
        self.assertEqual((sale.total_amount, sale.amount_tendered, sale.change_due),
                         (D("900"), D("1000"), D("100")))
        charge = Charge.objects.get(pk=sale.charge_id)
        self.assertEqual((sale.payment.amount, charge.amount_paid, charge.status),
                         (D("900"), D("900"), "paid"))
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance, D("0"))

    def test_cash_short_of_a_patients_discounted_total_bills_nothing(self):
        written = lambda: (self.footprint(), Adjustment.objects.count(),
                           PaymentAllocation.objects.count())
        before = written()
        with self.assertRaises(ValidationError):
            # 900 is due after the discount; 899.99 is short of it.
            self.sell([{"item": self.amoxil.pk, "quantity": 5}], customer_type="patient",
                      patient=self.patient, payment_method="cash", amount_tendered="899.99",
                      discount={"type": "percent", "value": "10", "reason": "Staff family"})
        self.assertEqual(written(), before)


# ------------------------------------------------------------------ returns


class ReturnTests(PosTestCase):
    def test_a_walk_in_return_refunds_the_money_and_quarantines_the_medicine(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 5}])
        piece = sale.items.get()
        ret = services.process_return(sale=sale, operator=self.cashier,
                                      lines=[{"sale_item": piece.pk, "quantity": 2}], reason="Wrong strength")
        self.assertEqual((ret.amount, ret.refund.amount), (D("400"), D("400")))
        self.assertIsNone(ret.refund.patient)
        self.assertEqual(ret.refund.payment, sale.payment)
        # The shelf is not restocked: the medicine waits in quarantine.
        self.assertEqual((self.held(self.amox), self.held(self.amox, QUARANTINE)), (45, 2))
        movement = StockMovement.objects.get(reference=ret.reference)
        self.assertEqual((movement.reason, movement.change, movement.location.code),
                         ("returned", 2, QUARANTINE))
        # The sale and its payment stay exactly as they were.
        self.assertEqual(Payment.objects.get(pk=sale.payment_id).amount, D("1000"))
        self.assertEqual(Sale.objects.get(pk=sale.pk).total_amount, D("1000"))
        self.assertEqual(sale.items.get().returnable_quantity, 3)

    def test_quarantined_medicine_cannot_be_sold(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 50}])
        services.process_return(sale=sale, operator=self.cashier,
                                lines=[{"sale_item": sale.items.get().pk, "quantity": 5}], reason="Recall")
        self.assertEqual(available_quantity(self.amoxil), 0)
        with self.assertRaises(ValidationError):
            self.sell([{"item": self.amoxil.pk, "quantity": 1}])

    def test_a_return_is_valued_at_what_was_paid_after_the_discount(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 10}],
                         discount={"type": "percent", "value": "10", "reason": "Promo"})
        ret = services.process_return(sale=sale, operator=self.cashier,
                                      lines=[{"sale_item": sale.items.get().pk, "quantity": 5}],
                                      reason="Changed mind")
        self.assertEqual(ret.amount, D("900"))

    def test_returning_everything_gives_back_exactly_what_was_paid(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 3}],
                         discount={"type": "amount", "value": "100", "reason": "Promo"})
        piece = sale.items.get()
        amounts = [services.process_return(sale=sale, operator=self.cashier,
                                           lines=[{"sale_item": piece.pk, "quantity": 1}],
                                           reason="Returned").amount for _ in range(3)]
        self.assertEqual(sum(amounts), D("500"))
        self.assertEqual(Payment.objects.get(pk=sale.payment_id).refundable_balance, D("0"))

    def test_more_than_was_sold_cannot_come_back(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 2}])
        with self.assertRaises(ValidationError):
            services.process_return(sale=sale, operator=self.cashier,
                                    lines=[{"sale_item": sale.items.get().pk, "quantity": 3}], reason="r")
        self.assertFalse(SaleReturn.objects.exists())
        self.assertFalse(Refund.objects.exists())
        self.assertEqual(self.held(self.amox, QUARANTINE), 0)

    def test_a_pharmacist_cannot_pay_money_out_as_a_return(self):
        services.open_register(operator=self.pharmacist)
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 2}])
        with self.assertRaises(PermissionDenied):
            services.process_return(sale=sale, operator=self.pharmacist,
                                    lines=[{"sale_item": sale.items.get().pk, "quantity": 1}], reason="r")
        self.assertFalse(Refund.objects.exists())

    def test_the_refunds_desk_cannot_refund_a_pos_payment_around_the_return(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 2}])
        with self.assertRaises(ValueError):
            refund_payment(payment=sale.payment, amount="100", reason="Around the till",
                           processed_by=self.cashier)
        self.assertFalse(Refund.objects.exists())

    def test_a_registered_patients_return_leaves_their_statement_balanced(self):
        patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F", created_by=self.doctor)
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 4}], customer_type="patient", patient=patient,
                         discount={"type": "percent", "value": "10", "reason": "Promo"})   # 800 − 80 = 720
        ret = services.process_return(sale=sale, operator=self.cashier,
                                      lines=[{"sale_item": sale.items.get().pk, "quantity": 2}],
                                      reason="Allergy")
        self.assertEqual(ret.amount, D("360"))
        charge = Charge.objects.get(pk=sale.charge_id)
        self.assertEqual((charge.amount, charge.amount_paid, charge.amount_returned), (D("800"), D("360"), D("360")))
        self.assertEqual(charge.balance, D("0"))
        self.assertTrue(Adjustment.objects.filter(charge=charge, kind="return", amount=D("360")).exists())
        self.assertEqual(PatientLedger.objects.get(patient=patient).outstanding_balance, D("0"))


# ----------------------------------------------------- one shelf, two ways


class OneShelfTests(PosTestCase):
    def test_prescription_dispensing_and_the_till_draw_on_the_same_stock(self):
        ibuprofen = product("Ibuprofen 400mg", unit_name="tablet")
        batch = stock_the_pharmacy(item=ibuprofen, quantity=100, actor=self.pharmacist,
                                   batch_no="IBU-1", sale_price="10")
        patient = Patient.objects.create(first_name="Chi", last_name="Eze", sex="M", created_by=self.doctor)

        prescription = create_prescription(patient=patient, doctor=self.doctor, item=ibuprofen, quantity=5,
                                           dosage_instructions="1 tablet", frequency="Three times daily",
                                           duration="5 days", route="oral")
        self.assertEqual(self.held(batch), 100)           # prescribing moves nothing
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        self.assertEqual(self.held(batch), 95)            # Paracetamol = 100 → 95
        self.sell([{"item": ibuprofen.pk, "quantity": 3}])
        self.assertEqual(self.held(batch), 92)            # → 92
        self.assertEqual(ibuprofen.total_quantity, 92)

        moves = StockMovement.objects.filter(batch=batch).exclude(reason="received")
        self.assertEqual(sorted(moves.values_list("reason", "change")), [("prescription", -5), ("sale", -3)])
        prescription.refresh_from_db()
        self.assertEqual((prescription.status, prescription.route), ("dispensed", "oral"))
        self.assertTrue(Charge.objects.filter(patient=patient, source_type="prescription",
                                              amount=D("50")).exists())


# -------------------------------------------------------- the money, whole


class FinancialReportTests(PosTestCase):
    def setUp(self):
        super().setUp()
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F", created_by=self.doctor)

    def today(self):
        return reporting.resolve_period("today", None, None)

    def test_pos_sales_dispensing_and_returns_reconcile_across_the_report(self):
        walk_in = self.sell([{"item": self.amoxil.pk, "quantity": 5}],
                            discount={"type": "percent", "value": "10", "reason": "Promo"})   # 1,000 − 100 = 900
        self.sell([{"item": self.paracetamol.pk, "quantity": 2}], customer_type="patient",
                  patient=self.patient)                                                    # 100
        prescription = create_prescription(patient=self.patient, doctor=self.doctor, item=self.amoxil, quantity=1)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)       # 200 charged
        record_payment(patient=self.patient, amount="200", received_by=self.cashier)       # 200 paid
        services.process_return(sale=walk_in, operator=self.cashier,
                                lines=[{"sale_item": walk_in.items.get().pk, "quantity": 1}],
                                reason="Unopened")                                          # 180 back

        report = reporting.financial_report(period=self.today())
        cash = report["collections"]
        self.assertEqual((cash["total"], cash["refunds"], cash["net"]), (D("1200"), D("180"), D("1020")))
        self.assertEqual(report["facility_revenue"]["total"], D("1020"))

        pharmacy_key = reporting.department_for("prescription")[0]
        row = next(r for r in report["departments"] if r["key"] == pharmacy_key)
        self.assertEqual((row["received"], row["refunded"], row["net_received"]),
                         (D("1200"), D("180"), D("1020")))
        self.assertEqual(sum(r["received"] for r in report["departments"]), cash["total"])

        pos = report["pos_sales"]
        self.assertEqual((pos["count"], pos["gross"], pos["discounts"], pos["paid"], pos["returned"], pos["net"]),
                         (2, D("1100"), D("100"), D("1000"), D("180"), D("820")))
        self.assertEqual(pos["payments"], D("1000"))
        self.assertTrue(pos["reconciles"])
        self.assertEqual((pos["walk_in"]["gross"], pos["walk_in"]["discounts"], pos["walk_in"]["paid"],
                          pos["walk_in"]["returned"]), (D("1000"), D("100"), D("900"), D("180")))
        self.assertEqual(pos["registered"]["paid"], D("100"))

        channels = {c["key"]: c for c in reporting.pharmacy_sales(self.today())["channels"]}
        self.assertEqual(channels["dispensing"]["received"], D("200"))
        self.assertEqual(channels["pos_registered"]["received"], D("100"))
        self.assertEqual((channels["pos_walk_in"]["received"], channels["pos_walk_in"]["refunded"]),
                         (D("900"), D("180")))
        self.assertTrue(report["reconciliation"]["balances"])

    def test_a_registered_patients_return_keeps_the_cohort_identity_closed(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 4}], customer_type="patient", patient=self.patient,
                         discount={"type": "percent", "value": "10", "reason": "Promo"})
        services.process_return(sale=sale, operator=self.cashier,
                                lines=[{"sale_item": sale.items.get().pk, "quantity": 2}], reason="Allergy")
        report = reporting.financial_report(period=self.today())
        # gross 800 − discount 80 − returns 360 − collected 360 = outstanding 0.
        self.assertEqual((report["charges"]["returns"], report["reconciliation"]["less_returns"]),
                         (D("360"), D("360")))
        self.assertEqual(report["charges"]["outstanding"], D("0"))
        self.assertTrue(report["reconciliation"]["balances"])


# ---------------------------------------------------------------- the API


class PosApiTests(PosTestCase):
    def setUp(self):
        super().setUp()
        self.accountant = User.objects.create_user(username="acc", password="t", role="accountant")
        services.open_register(operator=self.pharmacist)
        self.api = APIClient()

    def as_(self, user):
        self.api.force_authenticate(user)
        return self.api

    def results(self, response):
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data

    def test_only_pharmacists_and_cashiers_ring_up_a_sale(self):
        body = {"lines": [{"item": self.amoxil.pk, "quantity": 1}], "payment_method": "cash"}
        for role in ("accountant", "reception", "doctor", "nurse", "inventory_manager", "laboratory"):
            user = User.objects.create_user(username=f"u-{role}", password="t", role=role)
            with self.subTest(role=role):
                self.assertEqual(self.as_(user).post("/api/sales/complete/", body, format="json").status_code, 403)
        self.assertEqual(self.as_(self.pharmacist).post("/api/sales/complete/", body, format="json").status_code, 201)
        self.assertEqual(self.as_(self.cashier).post("/api/sales/complete/", body, format="json").status_code, 201)
        self.assertEqual(self.held(self.amox), 48)

    def test_a_pharmacists_discount_is_refused_and_moves_nothing(self):
        response = self.as_(self.pharmacist).post("/api/sales/complete/", {
            "lines": [{"item": self.amoxil.pk, "quantity": 1}], "payment_method": "cash",
            "discount": {"type": "percent", "value": "10", "reason": "Friend"}}, format="json")
        self.assertEqual((response.status_code, response.data["code"]), (403, "forbidden"))
        self.assertEqual(self.held(self.amox), 50)

    # ------------------------------------------------- the amount received
    # `services._tender` is the one check on it, held here at the API where the
    # till's disabled button protects nothing: cash short of the *discounted*
    # total is refused and writes nothing; cash above it is change, never money
    # kept; a non-cash payment is always exactly the total.

    def discounted_cash(self, tendered):
        """Paracetamol ×6 is 320.00 across its two lots; 20.00 off leaves 300.00 due."""
        return self.as_(self.cashier).post("/api/sales/complete/", {
            "lines": [{"item": self.paracetamol.pk, "quantity": 6}],
            "payment_method": "cash", "amount_tendered": tendered,
            "discount": {"type": "amount", "value": "20", "reason": "Promo"}}, format="json")

    def test_cash_short_of_the_discounted_total_is_refused_and_writes_nothing(self):
        written = lambda: (
            self.footprint(), Sale.objects.count(), SaleItem.objects.count(),
            PaymentAllocation.objects.count(), Adjustment.objects.count(),
            AuditLog.objects.filter(action__in=("pos.sale_completed", "pos.discount_applied")).count())
        before = written()
        response = self.discounted_cash("299.99")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("short of the 300.00 due", response.data["detail"])
        self.assertEqual(written(), before)
        # No receipt to read back.
        self.assertEqual(self.results(self.as_(self.accountant).get("/api/sales/")), [])

    def test_exactly_the_discounted_total_completes_with_no_change(self):
        response = self.discounted_cash("300")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["subtotal"], response.data["total_amount"],
                          response.data["amount_tendered"], response.data["change_due"]),
                         ("320.00", "300.00", "300.00", "0.00"))
        self.assertEqual(Payment.objects.get(pk=response.data["payment"]).amount, D("300"))

    def test_the_undiscounted_subtotal_handed_over_is_change_not_a_bigger_payment(self):
        response = self.discounted_cash("320")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["total_amount"], response.data["amount_tendered"],
                          response.data["change_due"]), ("300.00", "320.00", "20.00"))
        # The hospital keeps what was due, and the drawer expects exactly that.
        self.assertEqual(Payment.objects.get(pk=response.data["payment"]).amount, D("300"))
        self.assertEqual(D(services.register_summary(self.register)["cash_received"]), D("300"))

    def test_a_non_cash_payment_ignores_any_amount_received(self):
        for method in [key for key, _ in Payment.METHOD if key != "cash"]:
            for tendered in ("50", "999"):
                with self.subTest(method=method, tendered=tendered):
                    response = self.as_(self.cashier).post("/api/sales/complete/", {
                        "lines": [{"item": self.amoxil.pk, "quantity": 1}],
                        "payment_method": method, "amount_tendered": tendered}, format="json")
                    self.assertEqual(response.status_code, 201, response.data)
                    self.assertEqual((response.data["payment_method"], response.data["total_amount"],
                                      response.data["amount_tendered"], response.data["change_due"]),
                                     (method, "200.00", "200.00", "0.00"))
                    self.assertEqual(Payment.objects.get(pk=response.data["payment"]).amount, D("200"))

    def test_a_cashier_gives_a_line_discount_through_the_api(self):
        response = self.as_(self.cashier).post("/api/sales/complete/", {
            "lines": [{"item": self.amoxil.pk, "quantity": 2, "discount": {"type": "amount", "value": "50"}}],
            "payment_method": "transfer", "discount_reason": "Damaged pack"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["total_amount"], response.data["sale_discount_amount"]), ("350.00", "0.00"))
        self.assertEqual(response.data["lines"][0]["line_discount"], "50.00")

    def test_the_receipt_carries_everything_a_receipt_prints(self):
        created = self.as_(self.cashier).post("/api/sales/complete/", {
            "lines": [{"item": self.paracetamol.pk, "quantity": 6}], "customer_name": "Musa",
            "payment_method": "cash", "amount_tendered": "500",
            "discount": {"type": "amount", "value": "20", "reason": "Promo"}}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        receipt = self.as_(self.accountant).get(f"/api/sales/{created.data['id']}/").data
        self.assertTrue(receipt["reference"].startswith("POS-"))
        self.assertIsNotNone(receipt["completed_at"])
        self.assertEqual((receipt["customer_label"], receipt["sold_by_name"], receipt["payment_method_label"]),
                         ("Musa", "cash", "Cash"))
        self.assertEqual((receipt["subtotal"], receipt["discount_amount"], receipt["total_amount"],
                          receipt["amount_tendered"], receipt["change_due"]),
                         ("320.00", "20.00", "300.00", "500.00", "200.00"))
        self.assertEqual(receipt["register_reference"], self.register.reference)
        self.assertEqual([(p["batch_no"], p["quantity"], p["unit_price"]) for p in receipt["lines"][0]["pieces"]],
                         [("P-EARLY", 4, "50.00"), ("P-LATE", 2, "60.00")])

    def test_returns_are_taken_by_a_cashier_and_listed_in_the_register(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 3}], customer_name="Bisi")
        body = {"lines": [{"sale_item": sale.items.get().pk, "quantity": 1}], "reason": "Unopened"}
        self.assertEqual(self.as_(self.pharmacist).post(f"/api/sales/{sale.pk}/return/", body, format="json").status_code, 403)
        response = self.as_(self.cashier).post(f"/api/sales/{sale.pk}/return/", body, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        rows = self.results(self.as_(self.accountant).get("/api/sales/returns/"))
        self.assertEqual([(r["sale_reference"], r["customer_label"], r["amount"]) for r in rows],
                         [(sale.reference, "Bisi", "200.00")])
        self.assertEqual(self.as_(User.objects.create_user(username="r2", password="t", role="reception"))
                         .get("/api/sales/returns/").status_code, 403)

    def test_sale_history_filters(self):
        patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F", created_by=self.doctor)
        walk_in = self.sell([{"item": self.amoxil.pk, "quantity": 1}])
        registered = self.sell([{"item": self.paracetamol.pk, "quantity": 1}], customer_type="patient",
                               patient=patient)
        api = self.as_(self.accountant)
        ids = lambda params: [row["id"] for row in self.results(api.get("/api/sales/", params))]
        self.assertEqual(ids({"customer_type": "walk_in"}), [walk_in.pk])
        self.assertEqual(ids({"search": walk_in.reference}), [walk_in.pk])
        self.assertEqual(ids({"search": "Paracetamol"}), [registered.pk])
        self.assertEqual(ids({"search": "PH-PARA-500"}), [registered.pk])
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(ids({"date_from": tomorrow}), [])

    def test_the_summary_reconciles_with_the_payments(self):
        self.sell([{"item": self.amoxil.pk, "quantity": 2}],
                  discount={"type": "percent", "value": "25", "reason": "Promo"})
        summary = self.as_(self.accountant).get("/api/sales/summary/", {"preset": "today"}).data
        self.assertTrue(summary["reconciles"])
        self.assertEqual((summary["sales"]["gross"], summary["sales"]["discounts"], summary["sales"]["total"]),
                         ("400.00", "100.00", "300.00"))

    def test_closing_a_register_reconciles_the_drawer(self):
        sale = self.sell([{"item": self.amoxil.pk, "quantity": 2}], payment_method="cash", amount_tendered="1000")
        self.sell([{"item": self.amoxil.pk, "quantity": 1}], payment_method="card")
        services.process_return(sale=sale, operator=self.cashier, refund_method="cash",
                                lines=[{"sale_item": sale.items.get().pk, "quantity": 1}], reason="r")
        with self.assertRaises(PermissionDenied):
            services.close_register(register=self.register, actor=self.pharmacist, counted_cash="1200")
        closed = services.close_register(register=self.register, actor=self.cashier, counted_cash="1150")
        self.assertEqual(closed.expected_cash, D("1200"))     # float 1,000 + cash 400 − cash refund 200
        self.assertEqual(closed.variance, D("-50"))
        self.assertEqual((closed.closing_summary["takings"], closed.closing_summary["refunds"]),
                         ("600.00", "200.00"))
        self.assertTrue(AuditLog.objects.filter(action="pos.register_variance").exists())


# ------------------------------------------------------------ POS categories


class PosCategoryTests(PosTestCase):
    """
    The till browses by category — and a category is a way of finding a
    product, never a way of paying for one.

    The chips, the filter and the search are all narrowing the same product
    list `sell()` sells from, so a sale reached through a category chip moves
    exactly the stock and exactly the money a sale reached through the search
    box does.
    """

    def products(self, user=None, **params):
        client = APIClient()
        client.force_authenticate(user or self.cashier)
        response = client.get("/api/sales/products/", params)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return response.data

    def test_the_till_offers_the_categories_it_sells(self):
        data = self.products()
        names = [row["name"] for row in data["categories"]]
        self.assertIn("Analgesics", names)
        self.assertIn("Antibiotics", names)
        # A category nobody has filed a product under is not a chip.
        ItemCategory.objects.create(name="Nothing Filed Here")
        self.assertNotIn("Nothing Filed Here",
                         [row["name"] for row in self.products()["categories"]])

    def test_the_chips_do_not_collapse_when_one_is_picked(self):
        # The strip is built from the whole catalogue, so filtering to one
        # category still offers the way back to the others.
        analgesics = ItemCategory.objects.get(name="Analgesics")
        data = self.products(category=analgesics.pk)
        self.assertEqual([row["name"] for row in data["results"]], ["Paracetamol 500mg"])
        self.assertGreaterEqual(len(data["categories"]), 2)

    def test_a_category_filters_the_products_without_replacing_the_search(self):
        antibiotics = ItemCategory.objects.get(name="Antibiotics")
        analgesics = ItemCategory.objects.get(name="Analgesics")

        # Search alone.
        by_name = self.products(search="paracetamol")
        self.assertEqual([row["name"] for row in by_name["results"]], ["Paracetamol 500mg"])

        # Search and category together: the category narrows the result, so a
        # search that matches nothing in it answers nothing rather than
        # ignoring one of the two.
        both = self.products(search="paracetamol", category=analgesics.pk)
        self.assertEqual([row["name"] for row in both["results"]], ["Paracetamol 500mg"])
        neither = self.products(search="paracetamol", category=antibiotics.pk)
        self.assertEqual(neither["results"], [])

        # And searching the category's own name still finds its products.
        by_category_name = self.products(search="Antibiotics")
        self.assertEqual([row["name"] for row in by_category_name["results"]],
                         ["Amoxicillin 500mg"])

    def test_every_product_row_carries_its_category(self):
        row = next(row for row in self.products()["results"]
                   if row["name"] == "Paracetamol 500mg")
        self.assertEqual(row["category_name"], "Analgesics")
        self.assertEqual(row["category"], ItemCategory.objects.get(name="Analgesics").pk)
        self.assertEqual(row["sku"], "PH-PARA-500")

    def test_a_sale_made_through_a_category_moves_the_same_stock_and_money(self):
        analgesics = ItemCategory.objects.get(name="Analgesics")
        picked = self.products(category=analgesics.pk)["results"][0]

        before = self.footprint()
        sale = self.sell([{"item": picked["id"], "quantity": 5}], customer_name="Musa")

        # FEFO off the dispensing shelf, unchanged: 4 from the early lot at ₦50,
        # 1 from the later one at ₦60.
        self.assertEqual(self.held(self.para_early), 0)
        self.assertEqual(self.held(self.para_late), 99)
        self.assertEqual(sale.total_amount, D("260"))
        self.assertEqual(sale.payment.amount, D("260"))
        self.assertEqual(sale.payment.channel, "pharmacy")
        self.assertNotEqual(before, self.footprint())

    def test_the_summary_breaks_the_till_down_by_category_and_still_reconciles(self):
        self.sell([{"item": self.paracetamol.pk, "quantity": 5}], customer_name="Musa")
        self.sell([{"item": self.amoxil.pk, "quantity": 2}], customer_name="Ngozi")

        client = APIClient()
        client.force_authenticate(self.cashier)
        response = client.get("/api/sales/summary/", {"preset": "today"})
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data

        by_label = {row["label"]: row for row in data["categories"]}
        self.assertEqual(by_label["Analgesics"]["units"], 5)
        self.assertEqual(D(str(by_label["Analgesics"]["net"])), D("260"))
        self.assertEqual(by_label["Antibiotics"]["units"], 2)
        self.assertEqual(D(str(by_label["Antibiotics"]["net"])), D("400"))

        # A breakdown of money already reported, never a second count of it.
        self.assertEqual(sum(D(str(row["net"])) for row in data["categories"]),
                         D(str(data["sales"]["total"])))
        self.assertEqual(sum(D(str(row["gross"])) for row in data["categories"]),
                         D(str(data["sales"]["gross"])))
        self.assertTrue(data["reconciles"])

    def test_a_product_with_no_category_is_reported_not_dropped(self):
        loose = Item.objects.create(name="Unclassified item")
        stock_the_pharmacy(item=loose, quantity=10, actor=self.pharmacist,
                           batch_no="U-1", sale_price="100")
        self.sell([{"item": loose.pk, "quantity": 1}], customer_name="Walk in")

        client = APIClient()
        client.force_authenticate(self.cashier)
        data = client.get("/api/sales/summary/", {"preset": "today"}).data
        by_label = {row["label"]: row for row in data["categories"]}
        self.assertEqual(D(str(by_label["Uncategorised"]["net"])), D("100"))
        self.assertEqual(sum(D(str(row["net"])) for row in data["categories"]),
                         D(str(data["sales"]["total"])))
