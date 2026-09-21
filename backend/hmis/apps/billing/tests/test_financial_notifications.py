"""
Who is told when the patient's financial position changes.

The rule this file holds: every financially relevant decision is announced by
`billing/services.py` — the authoritative service, not the view and certainly
not the React app — to the desks that work the money and to nobody else, once
per decision.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Payment
from apps.billing.services import (add_charge, apply_percentage_discount, cancel_charge,
                                   defer_charge, record_payment, refund_payment, waive_charge)
from apps.core.models import Notification, NotificationSetting
from apps.patients.models import Patient


class FinancialNotificationTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier",
                                                first_name="Ada", last_name="Bello")
        self.accountant = User.objects.create_user(username="acc", password="t", role="accountant")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.hospital_admin = User.objects.create_user(username="ha", password="t",
                                                       role="hospital_admin")
        # Everyone who must NOT hear about money.
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.ward = User.objects.create_user(username="wm", password="t", role="ward_manager")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)

    def _notified(self):
        return set(Notification.objects.filter(category="billing")
                   .values_list("recipient__role", flat=True))

    # --- recipients --------------------------------------------------------

    def test_the_money_desks_are_told_and_the_wards_are_not(self):
        add_charge(patient=self.patient, description="Consultation fee", amount="5000",
                   created_by=self.reception, source_type="consultation")
        told = self._notified()
        self.assertEqual(told, {"cashier", "accountant", "admin", "hospital_admin"})
        for outsider in (self.doctor, self.nurse, self.pharmacist, self.lab, self.ward):
            self.assertFalse(
                Notification.objects.filter(recipient=outsider).exists(),
                f"{outsider.role} was told about money they have nothing to do with")

    def test_reception_is_told_when_somebody_else_raises_the_charge(self):
        add_charge(patient=self.patient, description="Card", amount="500",
                   created_by=self.cashier, source_type="card")
        self.assertIn("reception", self._notified())

    def test_whoever_did_it_is_not_told_about_their_own_action(self):
        add_charge(patient=self.patient, description="Card", amount="500",
                   created_by=self.cashier, source_type="card")
        self.assertFalse(Notification.objects.filter(recipient=self.cashier).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.accountant).exists())

    def test_a_disabled_account_is_not_notified(self):
        self.accountant.is_active = False
        self.accountant.save(update_fields=["is_active"])
        add_charge(patient=self.patient, description="Card", amount="500",
                   created_by=self.reception, source_type="card")
        self.assertFalse(Notification.objects.filter(recipient=self.accountant).exists())

    # --- what the message carries -----------------------------------------

    def test_a_notification_names_the_patient_the_number_the_amount_and_the_staff(self):
        add_charge(patient=self.patient, description="Consultation fee", amount="5000",
                   created_by=self.reception, source_type="consultation")
        note = Notification.objects.filter(recipient=self.cashier).first()
        self.assertIn("To collect", note.title)
        self.assertIn(self.patient.display_name, note.title)
        self.assertIn(self.patient.patient_number, note.message)
        self.assertIn("5,000.00", note.message)
        self.assertIn("Consultation fee", note.message)
        self.assertIn("rec", note.message)          # who raised it
        self.assertIn("Outstanding balance", note.message)
        self.assertEqual(note.action_url, "/billing")
        self.assertEqual(note.category, "billing")

    def test_no_clinical_detail_travels_with_a_financial_notification(self):
        """A bill line is a billed service; a chart is not."""
        add_charge(patient=self.patient, description="Laboratory: FBC", amount="3500",
                   created_by=self.reception, source_type="lab_test")
        note = Notification.objects.filter(recipient=self.cashier).first()
        for leak in ("diagnosis", "result", "malaria", "positive"):
            self.assertNotIn(leak, note.message.lower())

    # --- the events --------------------------------------------------------

    def _charge(self, amount="5000"):
        return add_charge(patient=self.patient, description="Consultation fee", amount=amount,
                          created_by=self.reception, source_type="consultation")

    def _titles(self, recipient=None):
        rows = Notification.objects.filter(category="billing")
        if recipient:
            rows = rows.filter(recipient=recipient)
        return [row.title for row in rows]

    def test_a_part_payment_and_a_full_payment_are_announced_differently(self):
        self._charge("5000")
        Notification.objects.all().delete()
        record_payment(patient=self.patient, amount="2000", received_by=self.cashier)
        self.assertTrue(any("Part payment" in t for t in self._titles()))

        Notification.objects.all().delete()
        record_payment(patient=self.patient, amount="3000", received_by=self.cashier)
        self.assertTrue(any("Paid in full" in t for t in self._titles()))

    def test_a_discount_a_waiver_a_deferral_and_a_cancellation_are_each_announced(self):
        for maker, expected in (
            (lambda c: apply_percentage_discount(charge=c, percent=10, reason="Staff",
                                                 approved_by=self.cashier), "Discount applied"),
            (lambda c: waive_charge(charge=c, reason="Hardship", approved_by=self.cashier,
                                    amount="100"), "Waiver applied"),
            (lambda c: defer_charge(charge=c, reason="Will pay Friday",
                                    approved_by=self.reception), "Pay later approved"),
            (lambda c: cancel_charge(charge=c, cancelled_by=self.cashier,
                                     reason="Billed in error"), "Bill cancelled"),
        ):
            with self.subTest(expected):
                charge = self._charge()
                Notification.objects.all().delete()
                maker(charge)
                self.assertTrue(any(expected in t for t in self._titles()),
                                f"{expected} was not announced: {self._titles()}")

    def test_a_refund_is_announced_as_a_refund(self):
        self._charge("5000")
        payment = record_payment(patient=self.patient, amount="5000", received_by=self.cashier)
        Notification.objects.all().delete()
        refund_payment(payment=payment, amount="2000", reason="Overcharged",
                       processed_by=self.cashier)
        titles = self._titles()
        self.assertTrue(any("REFUND" in t for t in titles), titles)
        note = Notification.objects.filter(recipient=self.accountant).first()
        self.assertIn("2,000.00", note.message)
        self.assertIn("Overcharged", note.message)
        self.assertEqual(note.action_url, "/transactions")

    # --- once, not twice ---------------------------------------------------

    def test_billing_at_the_counter_raises_one_notification_per_recipient(self):
        """
        The view used to announce on top of what it called. One decision must
        reach each desk once, however many code paths it passes through.
        """
        client = APIClient()
        client.force_authenticate(self.reception)
        response = client.post("/api/charges/", {
            "patient": self.patient.id, "description": "Consultation fee", "amount": "5000",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Notification.objects.filter(recipient=self.cashier).count(), 1)

    def test_discounting_a_whole_balance_announces_the_total_not_one_per_charge(self):
        for index in range(3):
            add_charge(patient=self.patient, description=f"Item {index}", amount="1000",
                       created_by=self.reception, source_type="other")
        Notification.objects.all().delete()
        client = APIClient()
        client.force_authenticate(self.cashier)
        response = client.post("/api/charges/discount-balance/", {
            "patient": self.patient.id, "percent": "10", "reason": "Staff family",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        notes = Notification.objects.filter(recipient=self.accountant)
        self.assertEqual(notes.count(), 1, "one decision, three charges, one notification")
        self.assertIn("300.00", notes.first().message)

    def test_the_payment_endpoint_announces_once(self):
        self._charge("5000")
        Notification.objects.all().delete()
        client = APIClient()
        client.force_authenticate(self.cashier)
        response = client.post("/api/payments/", {
            "patient": self.patient.id, "amount": "2000", "method": "cash",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Notification.objects.filter(recipient=self.accountant).count(), 1)

    # --- the switch --------------------------------------------------------

    def test_switching_the_billing_category_off_stops_them(self):
        """`core.NotificationSetting` is read by notify() itself (rule 31)."""
        NotificationSetting.objects.update_or_create(category="billing",
                                                    defaults={"is_enabled": False})
        self._charge()
        self.assertFalse(Notification.objects.filter(category="billing").exists())
