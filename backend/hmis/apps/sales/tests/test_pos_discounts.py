"""
What the pharmacy till may take off, and who has to say so.

The POS could always discount (rule 39) — a line's own, a sale-wide one on
top, `POS_DISCOUNT_ROLES` only, a mandatory reason, and never a change to the
product's price. What it could not say was *how much*: the only ceilings were
100% and "something has to be paid". This file holds the policy that fixes
that (`sales/discount_policy.py`) and, just as importantly, holds everything
around it still.

Four things it is here to prove:

* **The limit is judged on the money, not on the wording.** A 10% limit that
  only read `type="percent"` would be walked past with a fixed sum — ₦500 off
  a ₦1,000 line is 50% whatever it was typed as.
* **An approver is authenticated, never asserted.** The till sends a
  supervisor's credentials; a user id in a request body would be a discount
  every cashier could approve for themselves.
* **The product's price never moves.** A discount belongs to the line, and the
  shelf price is the same after the sale as before it.
* **The money and the shelf are untouched by any of it.** Gross, discount,
  net, payment, allocation and ledger keep the arithmetic rule 25 and rule 12
  already had, and the stock comes off FEFO by the quantity sold — never by
  anything derived from the discount.
"""
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import (Adjustment, Charge, PatientLedger, Payment, PaymentAllocation,
                                 Refund)
from apps.core.models import AuditLog, HospitalSettings
from apps.inventory.models import PHARMACY, StockMovement, StockRecord
from apps.inventory.testing import product, stock_the_pharmacy
from apps.patients.models import Patient
from apps.pharmacy.services import create_prescription, dispense_prescription
from apps.sales import services
from apps.sales.discount_policy import DiscountPolicy, DiscountRefused
from apps.sales.models import Sale

ZERO = D("0.00")


class PosDiscountTestCase(TestCase):
    """Two products at round prices, so every figure below is checkable by eye."""

    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="till-pass",
                                                role="cashier", first_name="Ada", last_name="Cash")
        self.accountant = User.objects.create_user(username="acc", password="super-secret",
                                                   role="accountant", first_name="Ngozi",
                                                   last_name="Books")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")

        self.para = product("Paracetamol 500mg", unit_name="tablet",
                            category_name="Pain Relief / Analgesics")
        self.amox = product("Amoxicillin 500mg", unit_name="capsule", category_name="Antibiotics")
        self.vitc = product("Vitamin C", unit_name="tablet", category_name="Vitamins & Supplements")

        # ₦1,000 a unit, so 4 units is the brief's ₦4,000 line.
        self.para_batch = stock_the_pharmacy(item=self.para, quantity=100, actor=self.pharmacist,
                                             batch_no="B001", sale_price="1000")
        self.amox_batch = stock_the_pharmacy(item=self.amox, quantity=100, actor=self.pharmacist,
                                             batch_no="B002", sale_price="1000")
        self.vitc_batch = stock_the_pharmacy(item=self.vitc, quantity=100, actor=self.pharmacist,
                                             batch_no="B003", sale_price="1000")
        self.register = services.open_register(operator=self.cashier, opening_float="0")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M")

    # -- helpers -----------------------------------------------------------

    def policy(self, **fields):
        row = HospitalSettings.load()
        for name, value in fields.items():
            setattr(row, name, value)
        row.save()
        return row

    def sell(self, lines, *, operator=None, **kwargs):
        kwargs.setdefault("customer_name", "Musa Bello")
        sale, _ = services.complete_sale(operator=operator or self.cashier, lines=lines, **kwargs)
        return sale

    def line(self, item, quantity=4, discount=None):
        entry = {"item": item.pk, "quantity": quantity}
        if discount:
            entry["discount"] = discount
        return entry

    @staticmethod
    def held(batch):
        record = StockRecord.objects.filter(batch=batch, location__code=PHARMACY).first()
        return record.quantity if record else 0

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client


# ------------------------------------------------------------------ basics


class ItemDiscountTests(PosDiscountTestCase):
    def test_a_percentage_off_one_item(self):
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "10"})],
                         discount_reason="Loyal customer")
        line = sale.lines.get()
        # The brief's worked example: ₦4,000, 10% = ₦400, ₦3,600.
        self.assertEqual((line.gross, line.line_discount, line.net),
                         (D("4000.00"), D("400.00"), D("3600.00")))
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.total_amount),
                         (D("4000.00"), D("400.00"), D("3600.00")))
        self.assertEqual(sale.payment.amount, D("3600.00"))

    def test_a_fixed_amount_off_one_item(self):
        sale = self.sell([self.line(self.para, 4, {"type": "amount", "value": "500"})],
                         discount_reason="Damaged packaging")
        line = sale.lines.get()
        self.assertEqual((line.gross, line.line_discount, line.net),
                         (D("4000.00"), D("500.00"), D("3500.00")))
        self.assertEqual(sale.total_amount, D("3500.00"))

    def test_the_line_keeps_the_discount_as_asked_and_as_priced(self):
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "10"})],
                         discount_reason="Staff discount")
        line = sale.lines.get()
        # Both, because "10%" and "₦400" answer different questions later.
        self.assertEqual((line.line_discount_type, line.line_discount_value),
                         ("percent", D("10.00")))
        self.assertEqual(line.line_discount, D("400.00"))

    def test_a_discount_cannot_be_more_than_the_line(self):
        with self.assertRaises(ValidationError):
            self.sell([self.line(self.para, 4, {"type": "amount", "value": "4500"})],
                      discount_reason="Too much")
        self.assertEqual(self.held(self.para_batch), 100)   # nothing moved
        self.assertFalse(Sale.objects.filter(status="completed").exists())

    def test_a_discount_cannot_cover_the_whole_sale(self):
        with self.assertRaises(ValidationError):
            self.sell([self.line(self.para, 4, {"type": "percent", "value": "100"})],
                      discount_reason="Free")
        self.assertEqual(self.held(self.para_batch), 100)

    def test_zero_and_negative_and_nonsense_are_refused(self):
        for value in ["0", "-5", "abc"]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    self.sell([self.line(self.para, 1, {"type": "percent", "value": value})],
                              discount_reason="No")
        with self.assertRaises(ValidationError):
            self.sell([self.line(self.para, 1, {"type": "sneaky", "value": "5"})],
                      discount_reason="No")
        with self.assertRaises(ValidationError):
            self.sell([self.line(self.para, 1, {"type": "percent", "value": "101"})],
                      discount_reason="No")

    def test_a_discount_needs_a_reason(self):
        with self.assertRaises(ValidationError):
            self.sell([self.line(self.para, 4, {"type": "percent", "value": "10"})])
        self.assertEqual(self.held(self.para_batch), 100)

    def test_the_products_own_price_is_never_changed(self):
        self.sell([self.line(self.para, 4, {"type": "percent", "value": "25"})],
                  discount_reason="Staff discount")
        self.para_batch.refresh_from_db()
        # The discount lives on the line. The shelf price is what it was.
        self.assertEqual(self.para_batch.sale_price, D("1000.00"))


# ----------------------------------------------------------- several items


class SelectedItemsTests(PosDiscountTestCase):
    """The brief's three-line cart: discount one, then discount two."""

    def cart(self, *discounts):
        """Paracetamol, Amoxicillin, Vitamin C at ₦4,000 / ₦6,000 / ₦3,000."""
        quantities = {self.para: 4, self.amox: 6, self.vitc: 3}
        return [self.line(item, quantity, discounts[index] if index < len(discounts) else None)
                for index, (item, quantity) in enumerate(quantities.items())]

    def test_only_the_selected_item_is_discounted(self):
        sale = self.sell(self.cart({"type": "percent", "value": "10"}),
                         discount_reason="Loyal customer")
        by_name = {line.item.name: line for line in sale.lines.select_related("item")}

        self.assertEqual(by_name["Paracetamol 500mg"].discount, D("400.00"))
        self.assertEqual(by_name["Paracetamol 500mg"].net, D("3600.00"))
        # The other two are untouched — original price, no discount.
        self.assertEqual(by_name["Amoxicillin 500mg"].discount, ZERO)
        self.assertEqual(by_name["Amoxicillin 500mg"].net, D("6000.00"))
        self.assertEqual(by_name["Vitamin C"].discount, ZERO)
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.total_amount),
                         (D("13000.00"), D("400.00"), D("12600.00")))

    def test_two_selected_items_are_discounted_and_the_third_is_not(self):
        sale = self.sell(self.cart({"type": "percent", "value": "10"}, None,
                                   {"type": "percent", "value": "10"}),
                         discount_reason="Staff discount")
        by_name = {line.item.name: line for line in sale.lines.select_related("item")}
        self.assertEqual(by_name["Paracetamol 500mg"].discount, D("400.00"))
        self.assertEqual(by_name["Vitamin C"].discount, D("300.00"))
        self.assertEqual(by_name["Amoxicillin 500mg"].discount, ZERO)
        self.assertEqual(sale.discount_amount, D("700.00"))
        self.assertEqual(sale.total_amount, D("12300.00"))

    def test_each_selected_item_may_take_a_different_kind_of_discount(self):
        sale = self.sell(self.cart({"type": "percent", "value": "10"},
                                   {"type": "amount", "value": "1000"}),
                         discount_reason="Mixed")
        by_name = {line.item.name: line for line in sale.lines.select_related("item")}
        self.assertEqual(by_name["Paracetamol 500mg"].line_discount, D("400.00"))
        self.assertEqual(by_name["Amoxicillin 500mg"].line_discount, D("1000.00"))
        self.assertEqual(sale.discount_amount, D("1400.00"))

    def test_a_sale_wide_discount_sits_on_top_and_the_lines_still_add_up(self):
        sale = self.sell(self.cart({"type": "percent", "value": "10"}),
                         discount={"type": "percent", "value": "10", "reason": "Manager"})
        # 10% off the paracetamol (₦400), then 10% of what is left (₦12,600).
        self.assertEqual(sale.discount_amount, D("1660.00"))
        self.assertEqual(sale.total_amount, D("11340.00"))
        # Every line's own arithmetic closes, and the lines close on the sale.
        lines = list(sale.lines.all())
        for line in lines:
            self.assertEqual(line.net, line.gross - line.discount)
        self.assertEqual(sum((line.discount for line in lines), ZERO), sale.discount_amount)
        self.assertEqual(sum((line.net for line in lines), ZERO), sale.total_amount)


# -------------------------------------------------------------- customers


class CustomerTypeTests(PosDiscountTestCase):
    def test_a_walk_in_customer_is_discounted_without_becoming_a_patient(self):
        before = Patient.objects.count()
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "5"})],
                         customer_name="Musa Bello", customer_phone="08031234567",
                         discount_reason="Walk-in promotion")
        self.assertEqual(Patient.objects.count(), before)
        self.assertEqual((sale.customer_type, sale.customer_name), ("walk_in", "Musa Bello"))
        self.assertEqual(sale.customer_phone, "08031234567")
        # No charge, no ledger — a walk-in owes nothing, discounted or not.
        self.assertIsNone(sale.charge)
        self.assertIsNone(sale.payment.patient)
        self.assertFalse(PatientLedger.objects.exists())
        self.assertEqual(sale.total_amount, D("3800.00"))

    def test_a_registered_patient_gets_the_charge_the_adjustment_and_the_payment(self):
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "20"})],
                         customer_type="patient", patient=self.patient,
                         discount_reason="Hospital concession")
        charge = Charge.objects.get(pk=sale.charge_id)

        # Rule 25's identity, on the patient's own statement: the charge keeps
        # its face value and the discount is its own column.
        self.assertEqual((charge.amount, charge.amount_discounted, charge.amount_paid),
                         (D("4000.00"), D("800.00"), D("3200.00")))
        self.assertEqual(charge.status, "paid")
        self.assertEqual((charge.source_type, charge.department.code), ("pos_sale", "pharmacy"))
        self.assertTrue(Adjustment.objects.filter(charge=charge, kind="discount",
                                                  amount=D("800.00")).exists())
        self.assertEqual(PaymentAllocation.objects.get(payment=sale.payment).charge, charge)
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance, ZERO)

    def test_being_a_patient_does_not_itself_earn_a_discount(self):
        sale = self.sell([self.line(self.para, 4)], customer_type="patient", patient=self.patient)
        self.assertEqual(sale.discount_amount, ZERO)
        self.assertEqual(sale.total_amount, D("4000.00"))
        self.assertFalse(Adjustment.objects.filter(charge=sale.charge, kind="discount").exists())

    def test_different_customers_take_different_discounts_on_the_same_product(self):
        first = self.sell([self.line(self.para, 4, {"type": "percent", "value": "10"})],
                          customer_name="Patient A", discount_reason="Loyal customer")
        second = self.sell([self.line(self.para, 4, {"type": "percent", "value": "20"})],
                           customer_name="Patient B", discount_reason="Staff discount")
        third = self.sell([self.line(self.para, 4, {"type": "amount", "value": "1000"})],
                          customer_name="Special", discount_reason="Management approval")
        self.assertEqual([s.discount_amount for s in (first, second, third)],
                         [D("400.00"), D("800.00"), D("1000.00")])
        # And the product is still ₦1,000 a unit for the next person.
        self.para_batch.refresh_from_db()
        self.assertEqual(self.para_batch.sale_price, D("1000.00"))


# ------------------------------------------------------- policy and limits


class DiscountPolicyTests(PosDiscountTestCase):
    def test_the_defaults_are_the_behaviour_that_was_there_before(self):
        policy = DiscountPolicy.load()
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.types, "both")
        self.assertEqual((policy.limit_percent, policy.max_percent), (D("100.00"), D("100.00")))
        self.assertEqual((policy.limit_amount, policy.max_amount), (ZERO, ZERO))
        # A big discount goes through with no approval, exactly as it used to.
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "90"})],
                         discount_reason="Because it always did")
        self.assertEqual(sale.discount_amount, D("3600.00"))
        self.assertIsNone(sale.discount_approved_by)

    def test_discounts_can_be_switched_off_entirely(self):
        self.policy(pos_discounts_enabled=False)
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 4, {"type": "percent", "value": "5"})],
                      discount_reason="Anything")
        self.assertEqual(caught.exception.code, "discounts_disabled")
        # An ordinary sale still works — the till is not closed, only the giving away.
        self.assertEqual(self.sell([self.line(self.para, 4)]).total_amount, D("4000.00"))

    def test_a_hospital_can_offer_percentages_only(self):
        self.policy(pos_discount_types="percent")
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 4, {"type": "amount", "value": "500"})],
                      discount_reason="Fixed")
        self.assertEqual(caught.exception.code, "discount_type_not_allowed")
        self.assertEqual(
            self.sell([self.line(self.para, 4, {"type": "percent", "value": "10"})],
                      discount_reason="Percent").discount_amount, D("400.00"))

    def test_above_the_cashiers_limit_needs_an_authorisation(self):
        self.policy(pos_discount_limit_percent=D("10"))
        # At the limit: fine.
        self.assertEqual(
            self.sell([self.line(self.para, 4, {"type": "percent", "value": "10"})],
                      discount_reason="Loyal customer").discount_amount, D("400.00"))
        # Above it: refused, by name, with what the till needs to ask.
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 4, {"type": "percent", "value": "25"})],
                      discount_reason="Big one")
        self.assertEqual(caught.exception.code, "discount_needs_approval")
        self.assertIn("Paracetamol 500mg", caught.exception.message)
        self.assertEqual(caught.exception.detail["limit_percent"], "10.00")
        self.assertEqual(self.held(self.para_batch), 96)   # only the allowed sale moved stock

    def test_a_fixed_sum_cannot_walk_past_a_percentage_limit(self):
        # The bypass this policy exists to close: ₦500 off a ₦1,000 line is
        # 50%, whatever the cashier called it.
        self.policy(pos_discount_limit_percent=D("10"))
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 1, {"type": "amount", "value": "500"})],
                      discount_reason="Sneaky")
        self.assertEqual(caught.exception.code, "discount_needs_approval")

    def test_a_fixed_sum_ceiling_of_its_own(self):
        self.policy(pos_discount_limit_amount=D("300"))
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 4, {"type": "amount", "value": "500"})],
                      discount_reason="Over the sum")
        self.assertEqual(caught.exception.detail["limit"], "amount")

    def test_the_maximum_cannot_be_approved_past_by_anybody(self):
        self.policy(pos_discount_limit_percent=D("10"), pos_max_discount_percent=D("50"))
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 4, {"type": "percent", "value": "60"})],
                      discount_reason="Far too much",
                      authorization={"username": "acc", "password": "super-secret"})
        # Not "ask a supervisor" — there is no supervisor who can.
        self.assertEqual(caught.exception.code, "discount_over_maximum")
        self.assertEqual(self.held(self.para_batch), 100)

    def test_a_sale_wide_discount_is_judged_by_the_same_limit(self):
        self.policy(pos_discount_limit_percent=D("10"))
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 4)],
                      discount={"type": "percent", "value": "30", "reason": "Whole sale"})
        self.assertEqual(caught.exception.code, "discount_needs_approval")
        self.assertIn("this sale", caught.exception.message)

    def test_a_preset_or_reason_list_with_a_typo_in_it_does_not_break_the_till(self):
        self.policy(pos_discount_presets="5, 10, oops, 20, 400", pos_discount_reasons="A, ,B")
        policy = DiscountPolicy.load()
        self.assertEqual([str(v) for v in policy.presets], ["5", "10", "20"])
        self.assertEqual(list(policy.reasons), ["A", "B"])


# ------------------------------------------------------------- authorising


class ApprovalTests(PosDiscountTestCase):
    def setUp(self):
        super().setUp()
        self.policy(pos_discount_limit_percent=D("10"))

    def over_limit(self, **kwargs):
        return self.sell([self.line(self.para, 4, {"type": "percent", "value": "25"})],
                         discount_reason="Management approval", **kwargs)

    def test_a_supervisors_credentials_authorise_it_and_are_recorded(self):
        sale = self.over_limit(authorization={"username": "acc", "password": "super-secret"})
        self.assertEqual(sale.discount_amount, D("1000.00"))
        self.assertEqual(sale.total_amount, D("3000.00"))
        # Two columns, because they answer different questions: who gave it,
        # and who allowed it.
        self.assertEqual(sale.discount_by, self.cashier)
        self.assertEqual(sale.discount_approved_by, self.accountant)

    def test_a_wrong_password_is_refused_and_nothing_is_written(self):
        with self.assertRaises(DiscountRefused) as caught:
            self.over_limit(authorization={"username": "acc", "password": "guessing"})
        self.assertEqual(caught.exception.code, "discount_approval_failed")
        self.assertEqual(self.held(self.para_batch), 100)
        self.assertFalse(Sale.objects.filter(status="completed").exists())
        self.assertFalse(Payment.objects.exists())

    def test_a_cashier_cannot_authorise_their_own_over_limit_discount(self):
        # The separation is the point: a cashier's own password is a valid
        # login and still not an authorisation.
        with self.assertRaises(DiscountRefused) as caught:
            self.over_limit(authorization={"username": "cash", "password": "till-pass"})
        self.assertEqual(caught.exception.code, "discount_approval_failed")
        self.assertIn("not authorised to approve", caught.exception.message)

    def test_an_unrelated_members_credentials_are_not_an_authorisation(self):
        with self.assertRaises(DiscountRefused) as caught:
            self.over_limit(authorization={"username": "doc", "password": "t"})
        self.assertEqual(caught.exception.code, "discount_approval_failed")

    def test_an_authorised_person_at_the_till_approves_by_being_who_they_are(self):
        # An accountant may *approve* but may not work a till (POS_ROLES), so
        # the person who both rings up and authorises is in practice an
        # administrator. They need no credentials prompt: they already are one.
        admin = User.objects.create_user(username="boss", password="t", role="admin",
                                         first_name="Chidi", last_name="Admin")
        services.open_register(operator=admin, opening_float="0")
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "25"})],
                         operator=admin, discount_reason="Manager sale")
        self.assertEqual(sale.discount_approved_by, admin)
        self.assertEqual(sale.discount_by, admin)

    def test_an_approver_is_recorded_only_where_one_was_needed(self):
        admin = User.objects.create_user(username="boss", password="t", role="admin")
        services.open_register(operator=admin, opening_float="0")
        within = self.sell([self.line(self.para, 4, {"type": "percent", "value": "5"})],
                           operator=admin, discount_reason="Small")
        # An administrator rang it up, but the policy never asked anybody: the
        # column means "this needed authorising", not "a manager was present".
        self.assertIsNone(within.discount_approved_by)

    def test_a_pharmacist_still_cannot_discount_at_all(self):
        services.open_register(operator=self.pharmacist, opening_float="0")
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.sell([self.line(self.para, 4, {"type": "percent", "value": "5"})],
                      operator=self.pharmacist, discount_reason="Nope")

    def test_the_api_answers_an_over_limit_discount_with_a_code_the_till_can_act_on(self):
        response = self.api(self.cashier).post("/api/sales/complete/", {
            "lines": [{"item": self.para.pk, "quantity": 4,
                       "discount": {"type": "percent", "value": "25"}}],
            "customer_type": "walk_in", "customer_name": "Musa",
            "discount_reason": "Management approval", "payment_method": "cash",
        }, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["code"], "discount_needs_approval")
        self.assertEqual(response.data["limit_percent"], "10.00")

    def test_the_api_takes_the_authorisation_and_completes_the_sale(self):
        response = self.api(self.cashier).post("/api/sales/complete/", {
            "lines": [{"item": self.para.pk, "quantity": 4,
                       "discount": {"type": "percent", "value": "25"}}],
            "customer_type": "walk_in", "customer_name": "Musa",
            "discount_reason": "Management approval", "payment_method": "cash",
            "authorization": {"username": "acc", "password": "super-secret"},
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["discount_amount"], "1000.00")
        self.assertEqual(response.data["discount_approved_by_name"], "Ngozi Books")
        # The password is nowhere in the answer.
        self.assertNotIn("super-secret", str(response.data))


# ------------------------------------------------------ stock, money, audit


class DiscountTouchesNothingElseTests(PosDiscountTestCase):
    def test_a_discount_never_changes_the_quantity_that_leaves_the_shelf(self):
        plain = self.sell([self.line(self.para, 5)], customer_name="Full price")
        self.assertEqual(self.held(self.para_batch), 95)

        discounted = self.sell([self.line(self.para, 5, {"type": "amount", "value": "200"})],
                               discount_reason="Loyal customer")
        # Five units either way. The discount is money, not medicine.
        self.assertEqual(self.held(self.para_batch), 90)
        movement = StockMovement.objects.filter(batch=self.para_batch,
                                                reason="sale").latest("created_at")
        self.assertEqual(movement.change, -5)
        self.assertEqual(discounted.lines.get().quantity, plain.lines.get().quantity)

    def test_fefo_and_the_expiry_rule_are_untouched_by_a_discount(self):
        short = stock_the_pharmacy(item=self.para, quantity=3, actor=self.pharmacist,
                                   batch_no="B000", sale_price="1000", expiry_days=5)
        expired = stock_the_pharmacy(item=self.para, quantity=50, actor=self.pharmacist,
                                     batch_no="OLD", sale_price="1000", expiry_days=-2)
        sale = self.sell([self.line(self.para, 5, {"type": "percent", "value": "10"})],
                         discount_reason="Loyal customer")
        # Earliest unexpired expiry first, and the expired lot is never touched.
        self.assertEqual(self.held(short), 0)
        self.assertEqual(self.held(self.para_batch), 98)
        self.assertEqual(self.held(expired), 50)
        self.assertEqual(sale.discount_amount, D("500.00"))

    def test_the_reporting_identity_holds_after_a_discount(self):
        self.sell([self.line(self.para, 4, {"type": "percent", "value": "10"})],
                  discount_reason="Loyal customer")
        self.sell([self.line(self.amox, 6)], customer_name="Full price")

        data = self.api(self.cashier).get("/api/sales/summary/", {"preset": "today"}).data
        sales = data["sales"]
        # gross − discounts = paid, and the payments agree with it.
        self.assertEqual(D(sales["gross"]) - D(sales["discounts"]), D(sales["total"]))
        self.assertEqual((sales["gross"], sales["discounts"], sales["total"]),
                         ("10000.00", "400.00", "9600.00"))
        self.assertTrue(data["reconciles"])

    def test_the_report_says_who_discounted_what_and_why(self):
        self.policy(pos_discount_limit_percent=D("10"))
        self.sell([self.line(self.para, 4, {"type": "percent", "value": "10"})],
                  discount_reason="Loyal customer")
        self.sell([self.line(self.amox, 4, {"type": "percent", "value": "25"})],
                  discount_reason="Management approval",
                  authorization={"username": "acc", "password": "super-secret"})

        block = self.api(self.accountant).get("/api/sales/summary/", {"preset": "today"}).data["discounts"]
        self.assertEqual(block["total"], "1400.00")
        self.assertEqual({row["label"] for row in block["by_reason"]},
                         {"Loyal customer", "Management approval"})
        self.assertEqual([row["label"] for row in block["by_cashier"]], ["Ada Cash"])
        # Every grouping is a split of the same figure, never a second count.
        self.assertTrue(block["reconciles"])
        # And what had to be authorised is its own line.
        self.assertEqual(block["approved"], {"amount": "1000.00", "count": 1})

    def test_the_audit_row_says_who_discounted_what_for_whom_and_why(self):
        self.policy(pos_discount_limit_percent=D("10"))
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "25"}),
                          self.line(self.amox, 6)],
                         customer_type="patient", patient=self.patient,
                         discount_reason="Hospital concession",
                         authorization={"username": "acc", "password": "super-secret"})

        entry = AuditLog.objects.get(action="pos.discount_applied")
        details = entry.details
        self.assertEqual(entry.actor, self.cashier)
        self.assertEqual(details["reference"], sale.reference)
        self.assertEqual(details["customer"], str(self.patient))
        self.assertEqual(details["patient_number"], self.patient.patient_number)
        self.assertEqual((details["reason"], details["applied_by"], details["approved_by"]),
                         ("Hospital concession", "Ada Cash", "Ngozi Books"))
        # Per line, so one over-limit line in a basket of five is still findable.
        discounted = details["lines"]
        self.assertEqual(len(discounted), 1)
        self.assertEqual(discounted[0]["product"], "Paracetamol 500mg")
        self.assertEqual((discounted[0]["unit_price"], discounted[0]["quantity"]),
                         ("1000.00", 4))
        self.assertEqual((discounted[0]["gross"], discounted[0]["line_discount"],
                          discounted[0]["net"]), ("4000.00", "1000.00", "3000.00"))
        self.assertEqual(discounted[0]["line_discount_value"], "25.00")

    def test_no_audit_row_and_no_discount_columns_when_nothing_was_discounted(self):
        sale = self.sell([self.line(self.para, 4)])
        self.assertFalse(AuditLog.objects.filter(action="pos.discount_applied").exists())
        self.assertEqual((sale.discount_amount, sale.discount_reason), (ZERO, ""))
        self.assertIsNone(sale.discount_by)


# ------------------------------------------------------ refund and cancel


class DiscountAndRefundTests(PosDiscountTestCase):
    """A refund gives back what was paid, never what was billed."""

    def test_a_returned_item_refunds_the_discounted_price(self):
        # The brief's case: ₦4,000 less ₦1,000 = ₦3,000 paid.
        sale = self.sell([self.line(self.para, 4, {"type": "amount", "value": "1000"})],
                         discount_reason="Loyal customer")
        self.assertEqual(sale.total_amount, D("3000.00"))

        piece = sale.items.get()
        returned = services.process_return(
            sale=sale, operator=self.cashier,
            lines=[{"sale_item": piece.pk, "quantity": 4}], reason="Wrong item")

        # ₦3,000 back, not ₦4,000.
        self.assertEqual(returned.amount, D("3000.00"))
        refund = Refund.objects.get(pos_return=returned)
        self.assertEqual(refund.amount, D("3000.00"))
        # And the discount record survives the return.
        sale.refresh_from_db()
        self.assertEqual(sale.discount_amount, D("1000.00"))
        self.assertEqual(sale.discount_reason, "Loyal customer")

    def test_a_partial_return_gives_back_that_share_of_what_was_paid(self):
        sale = self.sell([self.line(self.para, 4, {"type": "amount", "value": "1000"})],
                         discount_reason="Loyal customer")
        piece = sale.items.get()
        returned = services.process_return(
            sale=sale, operator=self.cashier,
            lines=[{"sale_item": piece.pk, "quantity": 1}], reason="One too many")
        # One of four units, at the discounted ₦750 each — not ₦1,000.
        self.assertEqual(returned.amount, D("750.00"))
        self.assertEqual(Refund.objects.get(pos_return=returned).amount, D("750.00"))

    def test_a_patients_discounted_sale_returns_without_putting_them_in_credit(self):
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "25"})],
                         customer_type="patient", patient=self.patient,
                         discount_reason="Hospital concession")
        charge = Charge.objects.get(pk=sale.charge_id)
        self.assertEqual((charge.amount, charge.amount_discounted, charge.amount_paid),
                         (D("4000.00"), D("1000.00"), D("3000.00")))

        piece = sale.items.get()
        services.process_return(sale=sale, operator=self.cashier,
                             lines=[{"sale_item": piece.pk, "quantity": 4}],
                             reason="Returned everything")

        charge.refresh_from_db()
        ledger = PatientLedger.objects.get(patient=self.patient)
        # Rule 12's prohibition, from the other side: the goods came back, the
        # money went back, and nobody is in credit.
        self.assertGreaterEqual(ledger.outstanding_balance, ZERO)
        self.assertEqual(charge.amount, D("4000.00"))       # never rewritten
        self.assertEqual(charge.amount_discounted, D("1000.00"))
        self.assertEqual(charge.amount_returned, D("3000.00"))

    def test_the_stock_goes_to_quarantine_not_back_on_the_shelf(self):
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "25"})],
                         discount_reason="Loyal customer")
        piece = sale.items.get()
        services.process_return(sale=sale, operator=self.cashier,
                             lines=[{"sale_item": piece.pk, "quantity": 4}], reason="Back")
        # 100 − 4 sold, and the 4 are in quarantine, not on the dispensing shelf.
        self.assertEqual(self.held(self.para_batch), 96)


class ThePrescriptionWorkflowIsUntouchedTests(PosDiscountTestCase):
    """
    Doctor → prescription → pharmacy → dispensing → inventory, beside a
    discounted POS sale of the same drug off the same shelf.

    The till is a second channel on one inventory, not a second pharmacy, and a
    POS discount is a fact about a POS sale. Neither may reach the other: a
    prescription is never priced by a discount somebody gave at the counter,
    and dispensing raises the charge it always raised.
    """

    def test_a_pos_discount_does_not_reach_a_prescription_for_the_same_drug(self):
        # A discounted walk-in sale first.
        self.sell([self.line(self.para, 4, {"type": "percent", "value": "50"})],
                  discount_reason="Staff discount")

        # Then the ordinary clinical path, unchanged.
        script = create_prescription(patient=self.patient, item=self.para, quantity=10,
                                     doctor=self.doctor)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)
        script.refresh_from_db()

        charge = Charge.objects.filter(patient=self.patient, source_type="prescription").latest("id")
        # 10 × ₦1,000 at the shelf price. The 50% given at the till is not a
        # price change, so it reaches nothing here.
        self.assertEqual(charge.amount, D("10000.00"))
        self.assertEqual(script.status, "dispensed")
        self.assertFalse(Adjustment.objects.filter(charge=charge).exists())

    def test_the_two_channels_take_stock_off_the_same_shelf_and_both_are_logged(self):
        opening = self.held(self.para_batch)
        self.sell([self.line(self.para, 4, {"type": "percent", "value": "25"})],
                  discount_reason="Loyal customer")
        script = create_prescription(patient=self.patient, item=self.para, quantity=6,
                                     doctor=self.doctor)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

        # 100 − 4 sold − 6 dispensed = 90, and both movements are in the log.
        self.assertEqual(self.held(self.para_batch), opening - 10)
        reasons = set(StockMovement.objects.filter(batch=self.para_batch)
                      .values_list("reason", flat=True))
        self.assertEqual(reasons, {"received", "sale", "prescription"})

    def test_the_discount_policy_never_gates_dispensing(self):
        # Discounts switched off entirely: the clinical path does not notice.
        self.policy(pos_discounts_enabled=False, pos_max_discount_percent=D("0"))
        script = create_prescription(patient=self.patient, item=self.para, quantity=5,
                                     doctor=self.doctor)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)
        script.refresh_from_db()
        self.assertEqual(script.status, "dispensed")
        self.assertEqual(self.held(self.para_batch), 95)


# ------------------------------------------------------------ the cart's walk


class CartDiscountWalkTests(PosDiscountTestCase):
    """
    The counter's own scenario, at the counter's own prices: ₦20 of paracetamol
    and ₦500 of amoxicillin, 10% off the paracetamol only.

    It exists because the arithmetic that matters is the arithmetic a cashier
    reads off the screen — ₦520 less ₦2 is ₦518 — and because the till sends
    the discount **on the line**, which is the shape this whole engine prices,
    audits and refunds against.
    """

    def setUp(self):
        super().setUp()
        # Re-price to the counter's own figures: ₦20 and ₦500.
        self.para_batch.sale_price = D("20.00")
        self.para_batch.save(update_fields=["sale_price"])
        self.amox_batch.sale_price = D("500.00")
        self.amox_batch.save(update_fields=["sale_price"])

    def sell_the_cart(self, **extra):
        return self.api(self.cashier).post("/api/sales/complete/", {
            "lines": [{"item": self.para.pk, "quantity": 1,
                       "discount": {"type": "percent", "value": "10"}},
                      {"item": self.amox.pk, "quantity": 1}],
            "customer_type": "walk_in", "customer_name": "Musa Bello",
            "discount_reason": "Loyal customer", "payment_method": "cash", **extra,
        }, format="json")

    def test_one_discounted_line_and_one_untouched_one(self):
        response = self.sell_the_cart()
        self.assertEqual(response.status_code, 201, response.data)
        sale = response.data

        self.assertEqual((sale["subtotal"], sale["discount_amount"], sale["total_amount"]),
                         ("520.00", "2.00", "518.00"))
        lines = {row["item_name"]: row for row in sale["lines"]}
        self.assertEqual((lines["Paracetamol 500mg"]["gross"],
                          lines["Paracetamol 500mg"]["line_discount"],
                          lines["Paracetamol 500mg"]["net"]), ("20.00", "2.00", "18.00"))
        # The line nobody ticked keeps its price, to the kobo.
        self.assertEqual((lines["Amoxicillin 500mg"]["gross"],
                          lines["Amoxicillin 500mg"]["line_discount"],
                          lines["Amoxicillin 500mg"]["net"]), ("500.00", "0.00", "500.00"))

    def test_the_walk_in_is_not_turned_into_a_patient_to_carry_the_discount(self):
        sale = self.sell_the_cart().data
        self.assertIsNone(sale["patient"])
        self.assertIsNone(sale["charge"])
        self.assertFalse(PatientLedger.objects.exists())
        self.assertEqual(Payment.objects.get(pk=sale["payment"]).amount, D("518.00"))

    def test_whole_units_leave_the_shelf_however_the_line_was_priced(self):
        self.sell_the_cart()
        # One unit each. Not 0.9 of one, and not a quantity derived from ₦18.
        self.assertEqual(self.held(self.para_batch), 99)
        self.assertEqual(self.held(self.amox_batch), 99)
        self.assertEqual(
            StockMovement.objects.get(batch=self.para_batch, reason="sale").change, -1)

    def test_the_same_discount_on_several_selected_lines(self):
        # What "Discount 2 items" sends: one discount per selected line, which
        # is what the engine already prices — never a sale-wide one standing in
        # for a selection.
        response = self.api(self.cashier).post("/api/sales/complete/", {
            "lines": [{"item": self.para.pk, "quantity": 1,
                       "discount": {"type": "percent", "value": "10"}},
                      {"item": self.amox.pk, "quantity": 1,
                       "discount": {"type": "percent", "value": "10"}},
                      {"item": self.vitc.pk, "quantity": 1}],
            "customer_type": "walk_in", "customer_name": "Musa Bello",
            "discount_reason": "Staff discount", "payment_method": "cash",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        lines = {row["item_name"]: row for row in response.data["lines"]}
        self.assertEqual(lines["Paracetamol 500mg"]["net"], "18.00")      # 20 − 10%
        self.assertEqual(lines["Amoxicillin 500mg"]["net"], "450.00")    # 500 − 10%
        self.assertEqual(lines["Vitamin C"]["net"], "1000.00")           # never ticked
        self.assertEqual(response.data["discount_amount"], "52.00")
