"""
What the printed documents need from the API.

The card, the invoice and the receipt are rendered client-side from these
payloads, so a field missing here prints as a dash on a document the patient
walks away with.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.services import add_charge, record_payment
from apps.patients.models import Patient


class PatientCardPayloadTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.client = APIClient(); self.client.force_authenticate(self.reception)

    def _register(self, **overrides):
        payload = {
            "first_name": "Jane", "middle_name": "A", "last_name": "Doe", "sex": "F",
            "birthdate": "1990-04-02", "phone_number": "08134621576",
            "street_address": "12 Market Road", "city": "Aba", "state": "Abia",
            "country": "Nigeria",
            "emergency_contact_name": "Chidi Nwosu",
            "emergency_contact_relationship": "Brother",
            "emergency_contact_phone": "08030001111",
            "emergency_contact_alt_phone": "08030002222",
            "emergency_contact_address": "4 Ngwa Road, Aba",
            "emergency_contact_notes": "Works nights, best before 8am",
        }
        payload.update(overrides)
        return self.client.post("/api/patients/", payload, format="json")

    def test_registering_returns_everything_the_card_prints(self):
        response = self._register()
        self.assertEqual(response.status_code, 201, response.data)
        card = response.data
        # The file number is the point of the slip.
        self.assertTrue(card["file_number"].startswith("NMHS-"))
        self.assertEqual(card["sex_display"], "Female")   # not "F"
        self.assertEqual(card["street_address"], "12 Market Road")
        self.assertEqual(card["city"], "Aba")
        self.assertEqual(card["state"], "Abia")
        self.assertEqual(card["country"], "Nigeria")
        self.assertEqual(card["phone_number"], "08134621576")
        self.assertIn("created_at", card)

    def test_the_card_can_be_reprinted_from_the_chart(self):
        patient_id = self._register().data["id"]
        response = self.client.get(f"/api/patients/{patient_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["file_number"])
        self.assertEqual(response.data["sex_display"], "Female")

    def test_country_defaults_so_the_desk_does_not_retype_it(self):
        """Almost every patient is local; a field retyped each time is a
        field left blank."""
        response = self.client.post("/api/patients/", {
            "first_name": "Chidi", "last_name": "Eze", "sex": "M",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["country"], "Nigeria")

    def test_a_patient_from_elsewhere_keeps_their_own_country(self):
        response = self._register(country="Ghana", state="Greater Accra")
        self.assertEqual(response.data["country"], "Ghana")
        self.assertEqual(response.data["state"], "Greater Accra")

    def test_the_card_carries_the_emergency_contact(self):
        card = self._register().data
        self.assertEqual(card["emergency_contact_name"], "Chidi Nwosu")
        self.assertEqual(card["emergency_contact_relationship"], "Brother")
        self.assertEqual(card["emergency_contact_phone"], "08030001111")
        self.assertEqual(card["emergency_contact_alt_phone"], "08030002222")

    def test_a_patient_with_nobody_named_still_registers(self):
        """The contact is asked for, not demanded — somebody may arrive alone."""
        response = self.client.post("/api/patients/", {
            "first_name": "Chidi", "last_name": "Eze", "sex": "M",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["emergency_contact_name"], "")

    def test_the_chart_shows_the_address_as_one_line(self):
        doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        patient_id = self._register().data["id"]
        from apps.workflow.models import Visit
        from apps.patients.models import Patient
        Visit.objects.create(patient=Patient.objects.get(pk=patient_id),
                             opened_by=self.reception, attending_doctor=doctor)

        client = APIClient(); client.force_authenticate(doctor)
        overview = client.get(f"/api/patients/{patient_id}/overview/")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.data["patient"]["address"],
                         "12 Market Road, Aba, Abia, Nigeria")

        # The doctor sees who to call without opening the registration screen.
        contact = overview.data["patient"]["emergency_contact"]
        self.assertEqual(contact["name"], "Chidi Nwosu")
        self.assertEqual(contact["relationship"], "Brother")
        self.assertEqual(contact["phone"], "08030001111")
        self.assertIn("nights", contact["notes"])


class BillAndReceiptPayloadTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="test", role="reception",
                                                  first_name="Ada", last_name="Bello")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.client = APIClient(); self.client.force_authenticate(self.reception)

    def test_the_invoice_has_the_figures_it_totals(self):
        add_charge(patient=self.patient, description="General Consultation",
                   amount=Decimal("5000"), created_by=self.reception)
        record_payment(patient=self.patient, amount=Decimal("2000"), method="cash",
                       received_by=self.reception)

        charge = self.client.get("/api/charges/", {"patient": self.patient.id}).data["results"][0]
        self.assertEqual(charge["description"], "General Consultation")
        self.assertEqual(Decimal(charge["amount"]), Decimal("5000.00"))
        self.assertEqual(Decimal(charge["amount_paid"]), Decimal("2000.00"))
        self.assertEqual(Decimal(charge["amount_discounted"]), Decimal("0.00"))
        self.assertIn("created_at", charge)
        # amount − paid − discounted is what the invoice prints as due.
        due = Decimal(charge["amount"]) - Decimal(charge["amount_paid"]) - Decimal(charge["amount_discounted"])
        self.assertEqual(due, Decimal("3000.00"))

    def test_the_receipt_has_who_took_the_money_and_how(self):
        add_charge(patient=self.patient, description="Adult Card",
                   amount=Decimal("2000"), created_by=self.reception)
        response = self.client.post("/api/payments/", {
            "patient": self.patient.id, "amount": "2000", "method": "transfer",
            "reference": "TRF-9931",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)

        receipt = response.data
        self.assertEqual(Decimal(receipt["amount"]), Decimal("2000.00"))
        self.assertEqual(receipt["method"], "transfer")
        self.assertEqual(receipt["channel"], "front_desk")
        self.assertEqual(receipt["reference"], "TRF-9931")
        self.assertEqual(receipt["received_by_name"], "Ada Bello")
        self.assertEqual(receipt["patient_name"], str(self.patient))
        self.assertIn("id", receipt)        # the receipt number
        self.assertIn("created_at", receipt)

    def test_the_balance_printed_after_a_payment_is_the_real_one(self):
        add_charge(patient=self.patient, description="Consultation",
                   amount=Decimal("5000"), created_by=self.reception)
        self.client.post("/api/payments/", {
            "patient": self.patient.id, "amount": "1500", "method": "cash",
        }, format="json")
        ledger = self.client.get("/api/ledgers/", {"patient": self.patient.id}).data["results"][0]
        self.assertEqual(Decimal(ledger["outstanding_balance"]), Decimal("3500.00"))
