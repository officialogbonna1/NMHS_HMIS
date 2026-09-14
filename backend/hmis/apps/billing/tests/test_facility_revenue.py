"""
Total Facility Revenue — all time, the whole facility:

    every successful payment received − every refund processed

Held here against each kind of financial event, so that none of them can drift
into the figure:

* a payment adds what was received, and a part payment only what was received;
* a refund subtracts what went back, and partial refunds add up;
* a discount and a waiver subtract nothing — the money was never received, and
  taking them off payments as well would count them twice;
* a bill still owed, and a service cancelled before anybody paid, add nothing;
* the date range on the page never moves it, and neither does who is asking.

It is *not* gross − discounts − waivers − outstanding. That is the receivables
identity the report's cohort block closes on, and it answers a different
question.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import reporting
from apps.billing.models import Charge, Payment
from apps.billing.services import (add_charge, apply_amount_discount, apply_percentage_discount,
                                   cancel_and_refund, cancel_charge, record_payment,
                                   refund_payment, waive_charge)
from apps.patients.models import Patient


def _backdate(instance, days):
    """Move a row into the past. `created_at` is auto_now_add, so it takes an update()."""
    when = timezone.now() - timedelta(days=days)
    type(instance).objects.filter(pk=instance.pk).update(created_at=when)
    instance.refresh_from_db()
    return instance


class TotalFacilityRevenueTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.accountant = User.objects.create_user(username="acc", password="t", role="accountant")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.pharmacist = User.objects.create_user(username="pharm", password="t",
                                                   role="pharmacist")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.other = Patient.objects.create(first_name="Bola", last_name="Eze", sex="M",
                                            created_by=self.reception)

    def bill(self, amount, *, patient=None, source_type="consultation"):
        return add_charge(patient=patient or self.patient, description="Service",
                          amount=Decimal(amount), created_by=self.cashier,
                          source_type=source_type)

    def revenue(self):
        return reporting.facility_revenue()["total"]

    # --- the definition, event by event --------------------------------

    def test_the_worked_example(self):
        """Bill 100,000; discount 10,000; waiver 20,000; paid 70,000; then 10,000 refunded."""
        charge = self.bill("100000")
        apply_amount_discount(charge=charge, amount="10000", reason="Staff relative",
                              approved_by=self.cashier)
        waive_charge(charge=Charge.objects.get(pk=charge.pk), amount="20000",
                     reason="Hardship", approved_by=self.cashier)
        payment = record_payment(patient=self.patient, amount="70000", received_by=self.cashier)
        self.assertEqual(self.revenue(), Decimal("70000.00"))

        refund_payment(payment=payment, amount="10000", reason="Overcharged",
                       processed_by=self.cashier)
        self.assertEqual(reporting.facility_revenue(), {
            "received": Decimal("70000.00"), "refunded": Decimal("10000.00"),
            "total": Decimal("60000.00"),
        })

    def test_a_discount_or_a_waiver_on_its_own_changes_nothing(self):
        apply_percentage_discount(charge=self.bill("4000"), percent="25", reason="Staff",
                                  approved_by=self.cashier)
        waive_charge(charge=self.bill("3000", patient=self.other), reason="Hardship",
                     approved_by=self.cashier)
        self.assertEqual(self.revenue(), Decimal("0.00"))

    def test_a_part_payment_counts_only_what_was_received(self):
        self.bill("50000")
        record_payment(patient=self.patient, amount="20000", received_by=self.cashier)
        self.assertEqual(self.revenue(), Decimal("20000.00"))

    def test_an_outstanding_bill_is_not_revenue(self):
        self.bill("15000")
        self.assertEqual(self.revenue(), Decimal("0.00"))

    def test_a_service_cancelled_before_payment_changes_nothing(self):
        cancel_charge(charge=self.bill("4000", source_type="laboratory"),
                      cancelled_by=self.cashier, reason="Test never run")
        self.assertEqual(self.revenue(), Decimal("0.00"))

    def test_cancel_and_refund_takes_back_exactly_what_went_back(self):
        self.bill("5000")
        scan = self.bill("4000", source_type="ultrasound")
        record_payment(patient=self.patient, amount="9000", received_by=self.cashier)

        cancel_and_refund(charge=Charge.objects.get(pk=scan.pk), reason="Scan never done",
                          actor=self.cashier, expected_amount="4000")

        self.assertEqual(self.revenue(), Decimal("5000.00"))

    def test_partial_refunds_add_up(self):
        self.bill("10000")
        payment = record_payment(patient=self.patient, amount="10000", received_by=self.cashier)
        refund_payment(payment=payment, amount="3000", reason="Part unused",
                       processed_by=self.cashier)
        refund_payment(payment=Payment.objects.get(pk=payment.pk), amount="2000",
                       reason="More unused", processed_by=self.cashier)
        self.assertEqual(reporting.facility_revenue(), {
            "received": Decimal("10000.00"), "refunded": Decimal("5000.00"),
            "total": Decimal("5000.00"),
        })

    def test_if_payments_ever_gain_a_void_state_this_figure_must_learn_it(self):
        """
        Today a `Payment` row *is* a successful payment: there is nothing to
        exclude. If a status or void column is added, this fails so that
        `facility_revenue` is taught to leave voided payments out.
        """
        fields = {field.name for field in Payment._meta.get_fields()}
        self.assertFalse(fields & {"status", "is_void", "voided", "voided_at", "is_cancelled",
                                   "cancelled_at", "reversed_at"},
                         "Payment has a void/cancel state — exclude it from facility_revenue.")

    # --- all time, whole facility --------------------------------------

    def test_every_desk_and_every_user_counts(self):
        self.bill("3000")
        self.bill("2000", patient=self.other, source_type="prescription")
        record_payment(patient=self.patient, amount="1000", received_by=self.reception,
                       channel="front_desk")
        record_payment(patient=self.patient, amount="2000", received_by=self.cashier,
                       channel="cashier")
        record_payment(patient=self.other, amount="2000", received_by=self.pharmacist,
                       channel="pharmacy")
        self.assertEqual(self.revenue(), Decimal("5000.00"))

        client = APIClient()
        for user in (self.cashier, self.accountant):
            with self.subTest(user=user.username):
                client.force_authenticate(user)
                data = client.get("/api/finance/report/", {"preset": "today"}).data
                self.assertEqual(Decimal(str(data["facility_revenue"]["total"])),
                                 Decimal("5000.00"))

    def test_the_date_range_never_moves_it(self):
        self.bill("8000")
        old = _backdate(record_payment(patient=self.patient, amount="5000",
                                       received_by=self.cashier), days=400)
        _backdate(refund_payment(payment=old, amount="1000", reason="Overcharged",
                                 processed_by=self.cashier), days=399)
        record_payment(patient=self.patient, amount="3000", received_by=self.cashier)

        periods = [reporting.resolve_period(preset) for preset in reporting.PRESETS
                   if preset != "custom"]
        periods.append(reporting.resolve_period("custom", "2020-01-01", "2020-01-31"))
        for period in periods:
            with self.subTest(period=period.as_dict()["label"]):
                report = reporting.financial_report(period=period)
                self.assertEqual(report["facility_revenue"]["total"], Decimal("7000.00"))

        # The period's own figure does move — the two are not the same card.
        today = reporting.financial_report(period=reporting.resolve_period("today"))
        self.assertEqual(today["collections"]["net"], Decimal("3000.00"))

    def test_it_equals_the_periods_net_revenue_over_all_of_history(self):
        self.bill("20000")
        first = _backdate(record_payment(patient=self.patient, amount="6000",
                                         received_by=self.cashier), days=90)
        _backdate(refund_payment(payment=first, amount="1500", reason="Unused",
                                 processed_by=self.cashier), days=60)
        record_payment(patient=self.patient, amount="4000", received_by=self.cashier)

        start = (timezone.localdate() - timedelta(days=120)).isoformat()
        everything = reporting.resolve_period("custom", start, timezone.localdate().isoformat())
        report = reporting.financial_report(period=everything)
        self.assertEqual(report["facility_revenue"]["total"], report["collections"]["net"])
        self.assertEqual(report["facility_revenue"]["total"], Decimal("8500.00"))

    def test_it_costs_two_queries_whatever_the_history(self):
        self.bill("9000")
        for _ in range(3):
            record_payment(patient=self.patient, amount="1000", received_by=self.cashier)
        with self.assertNumQueries(2):
            reporting.facility_revenue()
