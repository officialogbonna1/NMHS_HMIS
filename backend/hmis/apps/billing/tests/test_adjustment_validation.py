"""
An adjustment has to say what it forgives — and a refund is not an adjustment.

`POST /api/adjustments/` could credit a patient's ledger without naming a
charge: the balance fell, `amount_discounted` stayed at zero, and no charge
column or department column could ever show the money. The dedicated actions
behind the billing counter always named a charge; this closes the generic path
beside them.

**A refund is refused outright.** It used to be the exception here, allowed
with or without a charge — but a refund written through this endpoint had no
`Refund` record behind it, so no revenue figure ever subtracted it. Refunds now
go through the Refund workflow only (see `test_refund_only_through_workflow.py`).
The rules live on the serializer and not on the model: `Adjustment.charge`
stays nullable, and every row already written stays valid.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Adjustment
from apps.billing.services import add_charge
from apps.patients.models import Patient


class AdjustmentValidationTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.charge = add_charge(patient=self.patient, description="Adult Card",
                                 amount=Decimal("2000"), created_by=self.cashier,
                                 source_type="card")
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

    def post(self, **body):
        payload = {"patient": self.patient.pk, "amount": "500", "reason": "Goodwill"}
        payload.update(body)
        return self.client.post("/api/adjustments/", payload, format="json")

    # -- what is now refused ------------------------------------------------

    def test_an_unlinked_discount_is_refused(self):
        response = self.post(kind="discount")
        self.assertEqual(response.status_code, 400)
        self.assertIn("charge", response.data)
        self.assertEqual(Adjustment.objects.filter(kind="discount").count(), 0)

    def test_an_unlinked_waiver_is_refused(self):
        response = self.post(kind="waiver")
        self.assertEqual(response.status_code, 400)
        self.assertIn("charge", response.data)
        self.assertEqual(Adjustment.objects.filter(kind="waiver").count(), 0)

    def test_the_refusal_says_what_is_missing(self):
        message = str(self.post(kind="discount").data["charge"])
        self.assertIn("charge", message.lower())

    def test_a_refused_adjustment_leaves_the_ledger_alone(self):
        before = self.patient.ledger.total_adjustments
        self.post(kind="waiver")
        self.patient.ledger.refresh_from_db()
        self.assertEqual(self.patient.ledger.total_adjustments, before)

    def test_an_unlinked_refund_is_refused(self):
        """It used to be the exception. It had no `Refund` behind it."""
        response = self.post(kind="refund")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], ["refund_workflow_required"])
        self.assertFalse(Adjustment.objects.filter(kind="refund").exists())

    def test_a_linked_refund_is_refused_too(self):
        """Naming a charge does not make it a refund record."""
        response = self.post(kind="refund", charge=self.charge.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], ["refund_workflow_required"])
        self.assertFalse(Adjustment.objects.filter(kind="refund").exists())

    # -- what still works ---------------------------------------------------

    def test_a_linked_discount_succeeds(self):
        response = self.post(kind="discount", charge=self.charge.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Adjustment.objects.get(kind="discount").charge_id, self.charge.pk)

    def test_a_linked_waiver_succeeds(self):
        response = self.post(kind="waiver", charge=self.charge.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Adjustment.objects.get(kind="waiver").charge_id, self.charge.pk)

    def test_the_model_still_allows_an_unlinked_row_of_any_kind(self):
        """
        The check is a serializer rule, not a database constraint. History
        holds unlinked discounts written before it existed, and they must stay
        readable rather than becoming rows the ORM refuses to load.
        """
        historic = Adjustment.objects.create(
            patient=self.patient, kind="discount", amount=Decimal("500"),
            reason="Written before the rule existed", approved_by=self.cashier)
        historic.refresh_from_db()
        self.assertIsNone(historic.charge_id)

    def test_a_historic_unlinked_discount_is_still_reported_as_unlinked(self):
        """
        The ₦500 row in the live database. It is not rewritten, and the
        financial report keeps naming it rather than hiding it.
        """
        from apps.billing import reporting

        Adjustment.objects.create(
            patient=self.patient, kind="discount", amount=Decimal("500"),
            reason="Goodwill", approved_by=self.cashier)

        report = reporting.financial_report(period=reporting.resolve_period("today"))
        self.assertEqual(report["adjustments"]["discounts"], Decimal("500.00"))
        self.assertEqual(report["adjustments"]["unlinked"], Decimal("500.00"))
        self.assertEqual(report["adjustments"]["unlinked_count"], 1)

    # -- the counter's own paths are unaffected -----------------------------

    def test_the_charge_actions_still_grant_discounts_and_waivers(self):
        """
        These always named a charge, so the new rule cannot reach them. If it
        ever does, the billing counter stops working — hence this test.
        """
        discounted = self.client.post(f"/api/charges/{self.charge.pk}/discount/",
                                      {"percent": "10", "reason": "Staff"}, format="json")
        self.assertEqual(discounted.status_code, 200)

        other = add_charge(patient=self.patient, description="Consultation",
                           amount=Decimal("5000"), created_by=self.cashier,
                           source_type="consultation")
        waived = self.client.post(f"/api/charges/{other.pk}/waive/",
                                  {"reason": "Hardship"}, format="json")
        self.assertEqual(waived.status_code, 200)

        self.assertEqual(Adjustment.objects.filter(charge__isnull=True).count(), 0)

    def test_reception_is_still_refused_before_validation_is_reached(self):
        """The permission boundary is unchanged and comes first."""
        client = APIClient(); client.force_authenticate(self.reception)
        response = client.post("/api/adjustments/", {
            "patient": self.patient.pk, "kind": "discount", "amount": "500", "reason": "friend",
        }, format="json")
        self.assertEqual(response.status_code, 403)
