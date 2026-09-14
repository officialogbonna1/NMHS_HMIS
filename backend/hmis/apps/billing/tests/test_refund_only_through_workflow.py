"""
A refund enters the money in exactly one way.

    Successful payment → adds to revenue
    Refund record      → reduces revenue
    Adjustment         → is never a refund

The Refund workflow (`POST /api/payments/<id>/refund/`, and the charge-scoped
cancel & refund behind it) writes a `Refund` through
`billing.services.refund_payment`. That record is what Total Facility Revenue,
net revenue retained and the refunds report subtract.

`POST /api/adjustments/` used to accept `kind="refund"` as well, which wrote an
`Adjustment` with no `Refund` behind it: the patient's ledger moved, and every
revenue figure said nothing had gone back. It is refused now — on create and on
update — while discounts and waivers through the same endpoint work exactly as
before, for exactly the same roles.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import reporting
from apps.billing.models import Adjustment, PatientLedger, Refund
from apps.billing.services import add_charge, record_payment
from apps.patients.models import Patient


class RefundOnlyThroughTheRefundWorkflow(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.accountant = User.objects.create_user(username="acc", password="t", role="accountant")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        # ₦10,000 paid in full, and a ₦5,000 bill still open to discount or waive.
        add_charge(patient=self.patient, description="Consultation fee", amount="10000",
                   created_by=self.reception, source_type="consultation")
        self.payment = record_payment(patient=self.patient, amount="10000",
                                      received_by=self.cashier)
        self.open_bill = add_charge(patient=self.patient, description="Laboratory: FBC",
                                    amount="5000", created_by=self.reception,
                                    source_type="lab_test")
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

    def adjust(self, user=None, **body):
        if user is not None:
            self.client.force_authenticate(user)
        payload = {"patient": self.patient.pk, "amount": "500", "reason": "Goodwill"}
        payload.update(body)
        return self.client.post("/api/adjustments/", payload, format="json")

    def ledger(self):
        return PatientLedger.objects.get(patient=self.patient)

    # --- 1, 2: legitimate adjustments still work, for the same roles -----

    def test_a_discount_adjustment_still_works(self):
        for user in (self.cashier, self.accountant):
            with self.subTest(role=user.role):
                before = self.ledger().total_adjustments
                response = self.adjust(user, kind="discount", charge=self.open_bill.pk)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["kind"], "discount")
                self.assertEqual(self.ledger().total_adjustments, before + Decimal("500"))
        self.assertEqual(Adjustment.objects.filter(kind="discount").count(), 2)

    def test_a_waiver_adjustment_still_works(self):
        for user in (self.cashier, self.accountant):
            with self.subTest(role=user.role):
                response = self.adjust(user, kind="waiver", charge=self.open_bill.pk)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["kind"], "waiver")
        self.assertEqual(Adjustment.objects.filter(kind="waiver").count(), 2)

    # --- 3, 7, 8: a refund through the adjustment endpoint is refused ----

    def test_a_refund_adjustment_is_refused_with_a_clear_reason(self):
        for user in (self.cashier, self.accountant):
            for body in ({}, {"charge": self.open_bill.pk}):
                with self.subTest(role=user.role, linked=bool(body)):
                    response = self.adjust(user, kind="refund", **body)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.data["code"], ["refund_workflow_required"])
                    self.assertIn("Refund", str(response.data["kind"]))

    def test_a_refused_refund_adjustment_writes_nothing_and_moves_no_money(self):
        revenue_before = reporting.facility_revenue()
        today = reporting.resolve_period("today")
        report_before = reporting.financial_report(period=today)
        ledger_before = self.ledger()

        self.assertEqual(self.adjust(kind="refund", amount="2000").status_code, 400)

        self.assertFalse(Refund.objects.exists())
        self.assertFalse(Adjustment.objects.filter(kind="refund").exists())
        self.assertEqual(reporting.facility_revenue(), revenue_before)
        report_after = reporting.financial_report(period=today)
        self.assertEqual(report_after["collections"]["refunds"],
                         report_before["collections"]["refunds"])
        self.assertEqual(report_after["collections"]["net"], report_before["collections"]["net"])
        ledger_after = self.ledger()
        self.assertEqual(ledger_after.total_adjustments, ledger_before.total_adjustments)
        self.assertEqual(ledger_after.outstanding_balance, ledger_before.outstanding_balance)

    def test_a_discount_cannot_be_turned_into_a_refund_afterwards(self):
        created = self.adjust(kind="discount", charge=self.open_bill.pk)
        response = self.client.patch(f"/api/adjustments/{created.data['id']}/",
                                     {"kind": "refund"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], ["refund_workflow_required"])
        self.assertEqual(Adjustment.objects.get(pk=created.data["id"]).kind, "discount")
        self.assertEqual(reporting.facility_revenue()["refunded"], Decimal("0.00"))

    # --- 4, 5, 6: the Refund workflow still works ------------------------

    def test_the_refund_workflow_creates_a_refund_and_reduces_revenue(self):
        self.assertEqual(reporting.facility_revenue()["total"], Decimal("10000.00"))

        response = self.client.post(f"/api/payments/{self.payment.pk}/refund/",
                                    {"amount": "3000", "reason": "Overcharged"}, format="json")

        self.assertEqual(response.status_code, 201)
        refund = Refund.objects.get()
        self.assertEqual(refund.payment_id, self.payment.pk)
        self.assertEqual(refund.patient_id, self.patient.pk)
        self.assertEqual(refund.amount, Decimal("3000.00"))
        self.assertEqual(refund.processed_by, self.cashier)
        # The workflow still writes its own ledger row beside the Refund.
        self.assertEqual(Adjustment.objects.get(kind="refund").amount, Decimal("3000.00"))
        self.assertEqual(reporting.facility_revenue(), {
            "received": Decimal("10000.00"), "refunded": Decimal("3000.00"),
            "total": Decimal("7000.00"),
        })

    def test_the_workflows_own_ledger_row_cannot_be_edited_through_the_endpoint(self):
        """Editing it would move the ledger away from the Refund it answers."""
        self.client.post(f"/api/payments/{self.payment.pk}/refund/",
                         {"amount": "3000", "reason": "Overcharged"}, format="json")
        row = Adjustment.objects.get(kind="refund")

        response = self.client.patch(f"/api/adjustments/{row.pk}/", {"amount": "100"},
                                     format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], ["refund_workflow_required"])
        row.refresh_from_db()
        self.assertEqual(row.amount, Decimal("3000.00"))
        self.assertEqual(reporting.facility_revenue()["total"], Decimal("7000.00"))

    # --- RBAC is unchanged -----------------------------------------------

    def test_reception_is_still_refused_before_validation_is_reached(self):
        response = self.adjust(self.reception, kind="refund")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Adjustment.objects.filter(kind="refund").exists())
