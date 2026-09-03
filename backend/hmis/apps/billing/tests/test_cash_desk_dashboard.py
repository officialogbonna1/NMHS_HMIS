"""
The cashier's dashboard, and being told when somebody else raises a bill.

A cashier is never routed a patient, so the generic "My queue" card was
always zero and the figures they are actually asked for at the end of a
shift were nowhere.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge, Payment, Adjustment
from apps.billing.services import add_charge, record_payment, apply_percentage_discount, waive_charge
from apps.core.models import Notification
from apps.patients.models import Patient


class CashDeskDashboardTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier")
        self.other_cashier = User.objects.create_user(username="cashier2", password="test", role="cashier")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.client = APIClient(); self.client.force_authenticate(self.cashier)

    def _cards(self, client=None):
        response = (client or self.client).get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        return {c["key"]: c for c in response.data["cards"] if "key" in c}

    def _charge(self, amount, by=None):
        return add_charge(patient=self.patient, description="Consultation",
                          amount=Decimal(amount), created_by=by or self.reception)

    def test_the_cash_desk_gets_money_cards_not_a_patient_queue(self):
        keys = set(self._cards())
        self.assertIn("taken_today", keys)
        self.assertIn("outstanding", keys)
        self.assertNotIn("my_queue", keys, "a cashier is never routed a patient")

    def test_taken_today_is_what_was_collected(self):
        self._charge("5000")
        record_payment(patient=self.patient, amount=Decimal("2000"), method="cash",
                       received_by=self.cashier)
        record_payment(patient=self.patient, amount=Decimal("1500"), method="transfer",
                       received_by=self.cashier)
        cards = self._cards()
        self.assertEqual(Decimal(str(cards["taken_today"]["value"])), Decimal("3500"))
        self.assertEqual(Decimal(str(cards["cash_today"]["value"])), Decimal("2000"))
        self.assertEqual(cards["taken_today"]["format"], "currency")

    def test_this_desks_own_takings_are_separate_from_the_whole_hospitals(self):
        self._charge("5000")
        record_payment(patient=self.patient, amount=Decimal("2000"), method="cash",
                       received_by=self.cashier)
        record_payment(patient=self.patient, amount=Decimal("1000"), method="cash",
                       received_by=self.other_cashier)
        cards = self._cards()
        self.assertEqual(Decimal(str(cards["taken_today"]["value"])), Decimal("3000"))
        self.assertEqual(Decimal(str(cards["my_desk_today"]["value"])), Decimal("2000"))

    def test_yesterdays_money_is_not_in_todays_figure(self):
        self._charge("5000")
        payment = record_payment(patient=self.patient, amount=Decimal("2000"), method="cash",
                                 received_by=self.cashier)
        Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.assertEqual(Decimal(str(self._cards()["taken_today"]["value"])), Decimal("0"))

    def test_part_paid_and_unpaid_are_counted_separately(self):
        first = self._charge("5000")
        self._charge("3000")
        record_payment(patient=self.patient, amount=Decimal("1000"), method="cash",
                       received_by=self.cashier)
        first.refresh_from_db()
        self.assertEqual(first.status, "partial")

        cards = self._cards()
        self.assertEqual(cards["part_paid"]["value"], 1)
        self.assertEqual(cards["unpaid_charges"]["value"], 2)   # partial counts as still owing

    def test_discounts_waivers_and_refunds_each_have_their_own_figure(self):
        charge = self._charge("10000")
        apply_percentage_discount(charge=charge, percent="10", reason="Staff",
                                  approved_by=self.cashier)
        waived = self._charge("2000")
        waive_charge(charge=waived, reason="Hardship", approved_by=self.cashier)
        Adjustment.objects.create(patient=self.patient, kind="refund", amount=Decimal("500"),
                                  reason="Overpaid", approved_by=self.cashier)

        cards = self._cards()
        self.assertEqual(Decimal(str(cards["discounted_today"]["value"])), Decimal("1000"))
        self.assertEqual(Decimal(str(cards["waived_today"]["value"])), Decimal("2000"))
        self.assertEqual(Decimal(str(cards["refunded_today"]["value"])), Decimal("500"))

    def test_the_outstanding_balance_is_what_the_hospital_is_still_owed(self):
        self._charge("5000")
        record_payment(patient=self.patient, amount=Decimal("2000"), method="cash",
                       received_by=self.cashier)
        self.assertEqual(Decimal(str(self._cards()["outstanding"]["value"])), Decimal("3000"))

    def test_every_card_opens_a_page_the_cashier_can_reach(self):
        allowed = {"/billing", "/transactions", "/patients", "/notifications"}
        for card in self.client.get("/api/dashboard/").data["cards"]:
            self.assertIn(card["href"], allowed, card["label"])

    def test_a_fresh_unpaid_charge_raises_an_alert(self):
        self._charge("5000")
        alerts = self.client.get("/api/dashboard/").data["alerts"]
        self.assertTrue(any("still unpaid" in a["label"] for a in alerts), alerts)

    def test_the_alert_clears_once_it_is_settled(self):
        self._charge("5000")
        record_payment(patient=self.patient, amount=Decimal("5000"), method="cash",
                       received_by=self.cashier)
        alerts = self.client.get("/api/dashboard/").data["alerts"]
        self.assertFalse(any("still unpaid" in a["label"] for a in alerts), alerts)


class BillingNotificationTests(TestCase):
    """Reception bills a patient and walks them over — the desk should know
    before the patient is standing there."""

    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.cashier = User.objects.create_user(username="cashier", password="test", role="cashier",
                                                first_name="Ada", last_name="Bello")
        self.accountant = User.objects.create_user(username="accountant", password="test", role="accountant")
        self.nurse = User.objects.create_user(username="nurse", password="test", role="nurse")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.client = APIClient(); self.client.force_authenticate(self.reception)

    def _bill(self, amount="5000", description="Consultation fee"):
        return self.client.post("/api/charges/", {
            "patient": self.patient.id, "description": description, "amount": amount,
        }, format="json")

    def test_the_cash_desk_is_told_when_the_front_desk_bills_somebody(self):
        self.assertEqual(self._bill().status_code, 201)
        note = Notification.objects.filter(recipient=self.cashier, category="billing").first()
        self.assertIsNotNone(note, "the cashier was not told about a charge they have to collect")
        self.assertIn("To collect", note.title)
        self.assertIn(str(self.patient), note.title)
        self.assertIn("Consultation fee", note.message)
        self.assertIn("reception", note.message)
        self.assertEqual(note.action_url, "/billing")

    def test_accountants_are_told_too(self):
        self._bill()
        self.assertTrue(Notification.objects.filter(recipient=self.accountant, category="billing").exists())

    def test_nobody_outside_the_cash_desk_is_told(self):
        self._bill()
        self.assertFalse(Notification.objects.filter(recipient=self.nurse).exists())

    def test_a_cashier_billing_is_not_told_about_their_own_charge(self):
        client = APIClient(); client.force_authenticate(self.cashier)
        client.post("/api/charges/", {
            "patient": self.patient.id, "description": "Card", "amount": "500",
        }, format="json")
        self.assertFalse(Notification.objects.filter(recipient=self.cashier).exists())
        # …but their colleague still is.
        self.assertTrue(Notification.objects.filter(recipient=self.accountant).exists())

    def test_a_disabled_cashier_is_not_notified(self):
        self.cashier.is_active = False
        self.cashier.save(update_fields=["is_active"])
        self._bill()
        self.assertFalse(Notification.objects.filter(recipient=self.cashier).exists())
