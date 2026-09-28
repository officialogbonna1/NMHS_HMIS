"""
A prescription is billed when the doctor writes it, and dispensed once **its
own** bill lets it go.

    Doctor prescribes → one pharmacy Charge per line, linked by
    `Prescription.charge` → on the patient's ledger → the cash desk takes
    payment for *that* bill, waives it or authorises Pay later → the pharmacy
    dispenses → stock moves.

The two things this file exists to hold:

* **the patient's balance is not the gate.** A consultation owing ₦30,000
  does not stop a paid script, and paying one script unlocks no other;
* **one line, one charge, ever** — a retried request, a re-bill and the
  historical migration never raise a second.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge, PatientLedger, Payment, PaymentAllocation
from apps.billing.services import add_charge, record_payment
from apps.core.models import AuditLog, Notification
from apps.inventory.models import PHARMACY, StockLocation, StockMovement, StockRecord
from apps.inventory.testing import product, stock_the_pharmacy
from apps.patients.models import Patient
from apps.pharmacy.models import Prescription
from apps.pharmacy.services import (
    PaymentRequired, bill_prescription, cancel_prescription, create_prescription,
    dispense_prescription, dispensing_clearance,
)
from apps.sales import services as pos
from apps.pharmacy.testing import pay_for

D = Decimal


class PrescriptionBillingTestCase(TestCase):
    def setUp(self):
        make = User.objects.create_user
        self.doctor = make(username="doc", password="t", role="doctor",
                           first_name="Tunde", last_name="Okafor")
        self.other_doctor = make(username="doc2", password="t", role="doctor")
        self.pharmacist = make(username="ph", password="t", role="pharmacist")
        self.cashier = make(username="cash", password="t", role="cashier")
        self.reception = make(username="rec", password="t", role="reception")
        self.admin = make(username="boss", password="t", role="admin")
        self.patient = Patient.objects.create(first_name="Michael", last_name="John", sex="M",
                                              created_by=self.reception)
        self.other_patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                                    created_by=self.reception)
        self.paracetamol = product("Paracetamol 500mg", unit_name="Tablet", strength="500 mg")
        self.amoxicillin = product("Amoxicillin", unit_name="Capsule")
        stock_the_pharmacy(item=self.paracetamol, quantity=100, actor=self.pharmacist,
                           batch_no="P1", sale_price="20")
        stock_the_pharmacy(item=self.amoxicillin, quantity=100, actor=self.pharmacist,
                           batch_no="A1", sale_price="250")
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def prescribe(self, lines, *, patient=None, token=None):
        body = {"patient": (patient or self.patient).pk, "lines": lines}
        if token:
            body["client_token"] = str(token)
        return self.api(self.doctor).post("/api/prescriptions/bulk/", body, format="json")

    def one(self, item=None, quantity=10, **kw):
        response = self.prescribe([{"item": (item or self.paracetamol).pk, "quantity": quantity}],
                                  **kw)
        self.assertEqual(response.status_code, 201, response.data)
        return Prescription.objects.get(pk=response.data[0]["id"])

    def shelf(self, item):
        return StockRecord.objects.get(batch__item=item, location=self.pharmacy).quantity

    def charges_for(self, user, patient=None):
        response = self.api(user).get("/api/charges/", {"patient": (patient or self.patient).pk})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data.get("results", response.data)


class PrescribingRaisesTheBillTests(PrescriptionBillingTestCase):
    def test_a_prescription_raises_one_linked_pharmacy_charge_priced_off_the_shelf(self):
        script = self.one(quantity=10)
        charge = script.charge
        self.assertIsNotNone(charge)
        self.assertEqual(Charge.objects.filter(source_type="prescription").count(), 1)
        # ₦20 × 10, the FEFO lot's own sale price — dispensing's pricing rule.
        self.assertEqual((charge.amount, script.quoted_amount), (D("200.00"), D("200.00")))
        self.assertEqual(charge.patient, self.patient)
        self.assertEqual((charge.source_type, charge.source_id), ("prescription", script.pk))
        self.assertEqual(charge.department.code, "pharmacy")
        self.assertEqual(charge.created_by, self.doctor)
        self.assertIn("Paracetamol 500mg ×10", charge.description)
        self.assertEqual(charge.settlement_status, "unpaid")
        # Nothing has left the shelf.
        self.assertEqual(self.shelf(self.paracetamol), 100)
        self.assertFalse(StockMovement.objects.filter(reason="prescription").exists())

    def test_a_script_crossing_two_lots_is_priced_lot_by_lot(self):
        stock_the_pharmacy(item=self.amoxicillin, quantity=5, actor=self.pharmacist,
                           batch_no="A0", sale_price="200", expiry_days=30)
        script = self.one(item=self.amoxicillin, quantity=8)
        # 5 × ₦200 from the earlier lot, then 3 × ₦250.
        self.assertEqual(script.charge.amount, D("1750.00"))

    def test_the_charge_is_on_the_patients_ledger(self):
        self.one(quantity=10)
        ledger = PatientLedger.objects.get(patient=self.patient)
        self.assertEqual(ledger.outstanding_balance, D("200.00"))

    def test_the_cash_desk_reception_and_admin_see_what_it_is_for(self):
        script = self.one(quantity=10)
        for user in (self.cashier, self.reception, self.admin):
            with self.subTest(role=user.role):
                rows = self.charges_for(user)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual((row["patient_number"], row["patient_name"]),
                                 (self.patient.patient_number, self.patient.display_name))
                self.assertEqual(row["settlement_status"], "unpaid")
                self.assertEqual(row["prescription"]["id"], script.pk)
                self.assertEqual(row["prescription"]["drug"], "Paracetamol 500mg")
                self.assertEqual(row["prescription"]["quantity"], 10)
                self.assertEqual(row["prescription"]["prescriber"], "Tunde Okafor")

    def test_the_pharmacy_notification_stays_and_the_cash_desk_is_told_once(self):
        self.prescribe([{"item": self.paracetamol.pk, "quantity": 10},
                        {"item": self.amoxicillin.pk, "quantity": 2}])
        self.assertTrue(Notification.objects.filter(recipient=self.pharmacist,
                                                    title__startswith="New prescription").exists())
        told = Notification.objects.filter(recipient=self.cashier)
        self.assertEqual(told.count(), 1)   # one script, one line on the bell (rule 35)
        self.assertIn("700", told.get().message.replace(",", ""))

    def test_the_prescription_row_carries_its_own_billing(self):
        script = self.one(quantity=10)
        row = self.api(self.pharmacist).get(f"/api/prescriptions/{script.pk}/").data
        self.assertEqual((row["billing"]["status"], row["billing"]["charge"]),
                         ("unpaid", script.charge_id))
        self.assertFalse(row["dispensable"])
        self.assertEqual(row["patient_number"], self.patient.patient_number)
        self.assertEqual(row["doctor_name"], "Tunde Okafor")


class OneLineOneChargeTests(PrescriptionBillingTestCase):
    def test_a_retried_submission_writes_and_bills_nothing_twice(self):
        token = uuid.uuid4()
        lines = [{"item": self.paracetamol.pk, "quantity": 10}]
        first = self.prescribe(lines, token=token)
        again = self.prescribe(lines, token=token)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual([r["id"] for r in first.data], [r["id"] for r in again.data])
        self.assertEqual(Prescription.objects.count(), 1)
        self.assertEqual(Charge.objects.filter(source_type="prescription").count(), 1)
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         D("200.00"))

    def test_billing_an_already_billed_line_returns_its_bill(self):
        script = self.one(quantity=10)
        charge = script.charge
        self.assertEqual(bill_prescription(prescription=script, actor=self.cashier), charge)
        response = self.api(self.cashier).post(f"/api/prescriptions/{script.pk}/bill/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Charge.objects.filter(source_type="prescription").count(), 1)

    def test_losing_the_race_to_bill_rolls_its_own_charge_back(self):
        """
        Two callers both read "no charge yet". The other request's charge lands
        between this one's read and its compare-and-set on `charge IS NULL`, so
        the update matches nothing: this caller's charge goes back with its
        savepoint and it answers with the winner's — on SQLite too, where
        `select_for_update` would not have stopped it.
        """
        from unittest import mock
        from apps.pharmacy import services

        written = Prescription.objects.create(patient=self.patient, doctor=self.doctor,
                                              item=self.paracetamol, quantity=5)
        real_quote = services.quote_prescription
        winner = {}

        def rival_lands_while_this_one_prices(**kwargs):
            # After this caller read "no charge", before its savepoint opens:
            # the other request's bill is written and linked.
            winner["charge"] = add_charge(
                patient=self.patient, description="winner", amount=D("100"),
                created_by=self.doctor, source_type="prescription", source_id=written.pk,
                notify=False)
            Prescription.objects.filter(pk=written.pk).update(charge=winner["charge"])
            return real_quote(**kwargs)

        with mock.patch.object(services, "quote_prescription",
                               side_effect=rival_lands_while_this_one_prices):
            result = bill_prescription(prescription=written, actor=self.doctor)

        self.assertEqual(result, winner["charge"])
        self.assertEqual(Charge.objects.filter(source_type="prescription",
                                               source_id=written.pk).count(), 1)
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         D("100.00"))


class TheGateIsThisLinesOwnBillTests(PrescriptionBillingTestCase):
    def test_an_unpaid_line_is_refused_and_nothing_moves(self):
        script = self.one(quantity=10)
        before = (StockMovement.objects.count(), Charge.objects.count(), Payment.objects.count())
        response = self.api(self.pharmacist).post(f"/api/prescriptions/{script.pk}/dispense/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "payment_required")
        self.assertEqual(response.data["billing"]["status"], "unpaid")
        script.refresh_from_db()
        self.assertEqual((script.status, script.dispensed_at, script.dispensed_by),
                         ("pending", None, None))
        self.assertEqual(self.shelf(self.paracetamol), 100)
        self.assertEqual((StockMovement.objects.count(), Charge.objects.count(),
                          Payment.objects.count()), before)

    def test_a_paid_line_dispenses_through_the_ordinary_workflow(self):
        script = self.one(quantity=10)
        paid = self.api(self.cashier).post("/api/payments/", {
            "patient": self.patient.pk, "amount": "200", "method": "cash",
            "charge": script.charge_id}, format="json")
        self.assertEqual(paid.status_code, 201, paid.data)
        response = self.api(self.pharmacist).post(f"/api/prescriptions/{script.pk}/dispense/")
        self.assertEqual(response.status_code, 200, response.data)
        script.refresh_from_db()
        self.assertEqual((script.status, script.dispensed_by), ("dispensed", self.pharmacist))
        self.assertEqual(self.shelf(self.paracetamol), 90)
        # Dispensing raised no second charge.
        self.assertEqual(Charge.objects.filter(source_type="prescription").count(), 1)
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance, 0)

    def test_the_patients_other_debts_do_not_block_a_paid_script(self):
        add_charge(patient=self.patient, description="Consultation", amount=D("30000"),
                   created_by=self.reception, source_type="consultation", notify=False)
        add_charge(patient=self.patient, description="Laboratory: FBC", amount=D("10000"),
                   created_by=self.reception, source_type="lab_test", notify=False)
        script = self.one(item=self.amoxicillin, quantity=20)          # ₦5,000
        pay_for(script, by=self.cashier)
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         D("40000.00"))
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)
        script.refresh_from_db()
        self.assertEqual(script.status, "dispensed")

    def test_paying_the_patients_balance_generally_does_not_unlock_a_script(self):
        """Money on the account goes oldest first — to the consultation, not the script."""
        add_charge(patient=self.patient, description="Consultation", amount=D("30000"),
                   created_by=self.reception, source_type="consultation", notify=False)
        script = self.one(quantity=10)
        record_payment(patient=self.patient, amount=D("200"), received_by=self.cashier)
        script.refresh_from_db()
        self.assertFalse(dispensing_clearance(script)[0])
        with self.assertRaises(PaymentRequired):
            dispense_prescription(prescription=script, pharmacist=self.pharmacist)

    def test_paying_one_script_unlocks_no_other(self):
        a = self.one(item=self.amoxicillin, quantity=20)
        b = self.one(item=self.paracetamol, quantity=100)
        pay_for(a, by=self.cashier)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.charge.settlement_status, "paid")
        self.assertEqual(b.charge.settlement_status, "unpaid")
        self.assertNotEqual(a.charge_id, b.charge_id)
        dispense_prescription(prescription=a, pharmacist=self.pharmacist)
        with self.assertRaises(PaymentRequired):
            dispense_prescription(prescription=b, pharmacist=self.pharmacist)
        self.assertEqual(self.shelf(self.paracetamol), 100)

    def test_another_patients_payment_changes_nothing(self):
        mine = self.one(quantity=10)
        theirs = self.one(quantity=10, patient=self.other_patient)
        pay_for(theirs, by=self.cashier)
        mine.refresh_from_db()
        self.assertFalse(dispensing_clearance(mine)[0])

    def test_a_waived_line_may_be_dispensed(self):
        script = self.one(quantity=10)
        response = self.api(self.cashier).post(f"/api/charges/{script.charge_id}/waive/",
                                               {"reason": "Indigent patient"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        script.refresh_from_db()
        ok, billing = dispensing_clearance(script)
        self.assertTrue(ok)
        self.assertEqual(billing["label"], "NO PAYMENT REQUIRED")
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

    def test_pay_later_lets_it_go_and_the_money_stays_owed(self):
        script = self.one(quantity=10)
        response = self.api(self.reception).post(f"/api/charges/{script.charge_id}/defer/",
                                                 {"reason": "Will pay on discharge"},
                                                 format="json")
        self.assertIn(response.status_code, (200, 201), response.data)
        script.refresh_from_db()
        ok, billing = dispensing_clearance(script)
        self.assertEqual((ok, billing["status"]), (True, "deferred"))
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         D("200.00"))

    def test_a_part_payment_does_not_release_it_without_pay_later(self):
        script = self.one(quantity=10)
        record_payment(patient=self.patient, amount=D("50"), received_by=self.cashier,
                       charge=script.charge)
        script.refresh_from_db()
        ok, billing = dispensing_clearance(script)
        self.assertEqual((ok, billing["status"], billing["outstanding"]),
                         (False, "partial", "150.00"))
        with self.assertRaises(PaymentRequired):
            dispense_prescription(prescription=script, pharmacist=self.pharmacist)
        # The rest paid → released.
        pay_for(script, by=self.cashier)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

    def test_a_crafted_request_cannot_skip_the_gate(self):
        script = self.one(quantity=10)
        attempts = [
            {}, {"force": True}, {"billing": {"status": "paid"}}, {"dispensable": True},
        ]
        for body in attempts:
            with self.subTest(body=body):
                response = self.api(self.pharmacist).post(
                    f"/api/prescriptions/{script.pk}/dispense/", body, format="json")
                self.assertEqual(response.status_code, 400)
        # And the bill cannot be marked paid through the prescription.
        self.api(self.pharmacist).patch(f"/api/prescriptions/{script.pk}/",
                                        {"charge": None, "quoted_amount": "0"}, format="json")
        script.refresh_from_db()
        self.assertIsNotNone(script.charge_id)
        self.assertEqual(self.shelf(self.paracetamol), 100)


class ChargeScopedPaymentTests(PrescriptionBillingTestCase):
    def test_a_charge_scoped_payment_lands_on_that_bill_only(self):
        consultation = add_charge(patient=self.patient, description="Consultation",
                                  amount=D("30000"), created_by=self.reception,
                                  source_type="consultation", notify=False)
        script = self.one(quantity=10)
        payment = record_payment(patient=self.patient, amount=D("200"),
                                 received_by=self.cashier, charge=script.charge)
        consultation.refresh_from_db()
        self.assertEqual(consultation.amount_paid, 0)
        self.assertEqual(list(PaymentAllocation.objects.filter(payment=payment)
                              .values_list("charge_id", "amount")),
                         [(script.charge_id, D("200.00"))])

    def test_it_cannot_overpay_a_bill_or_pay_somebody_elses(self):
        script = self.one(quantity=10)
        theirs = self.one(quantity=10, patient=self.other_patient)
        add_charge(patient=self.patient, description="Consultation", amount=D("1000"),
                   created_by=self.reception, notify=False)
        with self.assertRaises(ValueError):
            record_payment(patient=self.patient, amount=D("201"), received_by=self.cashier,
                           charge=script.charge)
        with self.assertRaises(ValueError):
            record_payment(patient=self.patient, amount=D("10"), received_by=self.cashier,
                           charge=theirs.charge)
        self.assertFalse(Payment.objects.exists())

    def test_the_pharmacist_takes_it_at_the_counter_on_the_pharmacy_channel(self):
        script = self.one(quantity=10)
        response = self.api(self.pharmacist).post("/api/payments/", {
            "patient": self.patient.pk, "amount": "200", "method": "cash",
            "charge": script.charge_id}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["channel"], "pharmacy")
        self.assertTrue(Notification.objects.filter(
            recipient=self.pharmacist, title__startswith="Ready to dispense").count() == 0)

    def test_the_counter_is_told_when_the_desk_clears_a_script(self):
        script = self.one(quantity=10)
        pay_for(script, by=self.cashier)
        told = Notification.objects.filter(recipient=self.pharmacist,
                                           title__startswith="Ready to dispense")
        self.assertEqual(told.count(), 1)
        self.assertIn("Paracetamol 500mg ×10", told.get().message)


class CancellingAScriptTests(PrescriptionBillingTestCase):
    def test_cancelling_an_unpaid_script_withdraws_its_bill(self):
        script = self.one(quantity=10)
        cancel_prescription(prescription=script, actor=self.doctor, reason="Wrong drug")
        script.charge.refresh_from_db()
        self.assertEqual(script.charge.status, "cancelled")
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance, 0)
        self.assertEqual(Charge.objects.filter(pk=script.charge_id).count(), 1)   # kept

    def test_a_paid_script_keeps_its_bill_for_the_cash_desk_to_refund(self):
        script = self.one(quantity=10)
        pay_for(script, by=self.cashier)
        cancel_prescription(prescription=script, actor=self.doctor, reason="Allergy found")
        script.charge.refresh_from_db()
        self.assertEqual(script.charge.status, "paid")
        row = self.api(self.cashier).get(f"/api/charges/{script.charge_id}/").data
        self.assertTrue(row["service_withdrawn"])


class HistoryIsNotRewrittenTests(PrescriptionBillingTestCase):
    def test_a_legacy_pending_script_is_not_billed_until_somebody_asks(self):
        legacy = Prescription.objects.create(patient=self.patient, doctor=self.doctor,
                                             item=self.paracetamol, quantity=10)
        self.assertIsNone(legacy.charge_id)
        ok, _ = dispensing_clearance(legacy)
        self.assertFalse(ok)
        response = self.api(self.pharmacist).post(f"/api/prescriptions/{legacy.pk}/dispense/")
        self.assertEqual(response.data["code"], "not_billed")
        self.assertFalse(Charge.objects.exists())

        billed = self.api(self.cashier).post(f"/api/prescriptions/{legacy.pk}/bill/")
        self.assertEqual(billed.status_code, 200, billed.data)
        legacy.refresh_from_db()
        self.assertEqual(legacy.charge.amount, D("200.00"))
        self.assertTrue(AuditLog.objects.filter(action="prescription.billed",
                                                object_id=legacy.pk).exists())

    def test_a_script_dispensed_before_the_change_keeps_its_one_charge(self):
        """What the migration links: an old dispensed line and the charge it raised."""
        old = Prescription.objects.create(patient=self.patient, doctor=self.doctor,
                                          item=self.paracetamol, quantity=1, status="dispensed")
        charge = add_charge(patient=self.patient, description="Medication: Paracetamol 500mg",
                            amount=D("20"), created_by=self.pharmacist,
                            source_type="prescription", source_id=old.pk, notify=False)
        from importlib import import_module
        from django.apps import apps as global_apps
        migration = import_module("apps.pharmacy.migrations.0004_prescription_billing")
        migration.link_existing_charges(global_apps, None)
        old.refresh_from_db()
        self.assertEqual(old.charge, charge)
        self.assertEqual(Charge.objects.count(), 1)
        self.assertIsNone(old.quoted_amount)       # nothing priced after the fact
        # Asking to bill it again answers with that same charge.
        self.assertEqual(bill_prescription(prescription=old, actor=self.cashier), charge)
        self.assertEqual(Charge.objects.count(), 1)

    def test_a_clinician_cannot_raise_a_bill(self):
        legacy = Prescription.objects.create(patient=self.patient, doctor=self.doctor,
                                             item=self.paracetamol, quantity=10)
        self.assertEqual(self.api(self.doctor).post(f"/api/prescriptions/{legacy.pk}/bill/")
                         .status_code, 403)


class ThePosIsUntouchedTests(PrescriptionBillingTestCase):
    def test_a_walk_in_sale_raises_no_prescription_charge_and_needs_no_gate(self):
        pos.open_register(operator=self.cashier, opening_float="0")
        sale, _ = pos.complete_sale(operator=self.cashier,
                                    lines=[{"item": self.paracetamol.pk, "quantity": 3}])
        self.assertEqual(sale.status, "completed")
        self.assertIsNone(sale.payment.patient)
        self.assertFalse(Charge.objects.exists())
        self.assertFalse(Prescription.objects.exists())
        self.assertEqual(self.shelf(self.paracetamol), 97)
