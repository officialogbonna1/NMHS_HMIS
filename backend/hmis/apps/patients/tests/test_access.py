from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import PatientLedger
from apps.billing.services import add_charge, record_payment
from apps.inventory.testing import product, stock_the_pharmacy
from apps.inventory.models import Batch, Item
from apps.patients.access import patient_queryset_for
from apps.patients.models import Patient
from apps.pharmacy.models import Prescription
from apps.pharmacy.services import dispense_prescription


class DepartmentIsolationTests(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(username="doctor", password="test", role="doctor")
        self.reception = User.objects.create_user(username="reception", password="test", role="reception")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M", short_note="restricted", created_by=self.doctor)

    def test_reception_receives_demographic_patient_payload_only(self):
        client = APIClient(); client.force_authenticate(self.reception)
        response = client.get(f"/api/patients/{self.patient.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("short_note", response.data)

    def test_reception_cannot_read_clinical_health_record_tiles(self):
        client = APIClient(); client.force_authenticate(self.reception)
        self.assertEqual(client.get("/api/allergies/").status_code, 403)

    def test_reception_is_denied_vitals_notes_and_prescriptions(self):
        client = APIClient(); client.force_authenticate(self.reception)
        self.assertEqual(client.get("/api/vitals/").status_code, 403)
        self.assertEqual(client.get("/api/notes/").status_code, 403)
        self.assertEqual(client.get("/api/prescriptions/").status_code, 403)


class PharmacistPatientListTests(TestCase):
    """
    The pharmacy's Patients page.

    A patient does not stop being the pharmacy's the moment they pay. This
    used to require an unpaid charge alongside a dispensed prescription, so
    somebody who collected their drugs and settled at the counter dropped
    straight off the list — and "who did I hand that to on Tuesday?" became
    unanswerable from the page meant to answer it.
    """

    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.item = product("Paracetamol 500mg", unit_name="tablet")
        stock_the_pharmacy(item=self.item, quantity=100, actor=self.pharmacist,
                           expiry_days=365)

    def _sees(self):
        return set(patient_queryset_for(self.pharmacist))

    def test_a_pending_prescription_puts_the_patient_on_the_list(self):
        Prescription.objects.create(patient=self.patient, doctor=self.doctor,
                                    item=self.item, quantity=2)
        self.assertIn(self.patient, self._sees())

    def test_the_patient_stays_after_dispensing(self):
        prescription = Prescription.objects.create(patient=self.patient, doctor=self.doctor,
                                                   item=self.item, quantity=2)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        self.assertIn(self.patient, self._sees())

    def test_the_patient_stays_after_they_pay(self):
        """The whole point: paying is not a reason to disappear."""
        prescription = Prescription.objects.create(patient=self.patient, doctor=self.doctor,
                                                   item=self.item, quantity=2)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        owed = PatientLedger.objects.get(patient=self.patient).outstanding_balance
        record_payment(patient=self.patient, amount=owed,
                       received_by=self.pharmacist, channel="pharmacy")
        # Re-read: the ledger cached on the instance predates the payment.
        self.assertEqual(
            PatientLedger.objects.get(patient=self.patient).outstanding_balance, Decimal("0.00"))
        self.assertIn(self.patient, self._sees())

    def test_a_patient_the_pharmacy_has_never_served_is_not_on_the_list(self):
        stranger = Patient.objects.create(first_name="Uche", last_name="Nwosu", sex="M",
                                          created_by=self.reception)
        self.assertNotIn(stranger, self._sees())

    def test_an_unpaid_consultation_fee_alone_does_not_put_someone_on_it(self):
        """
        The old rule matched any unpaid charge next to any dispensed script,
        because two multi-valued relations in one Q match independently — so
        a consultation fee could pull an unrelated patient onto the pharmacy's
        list.
        """
        stranger = Patient.objects.create(first_name="Ngozi", last_name="Ike", sex="F",
                                          created_by=self.reception)
        add_charge(patient=stranger, description="Consultation", amount="2000",
                   created_by=self.reception)
        self.assertNotIn(stranger, self._sees())

    def test_the_pharmacist_can_open_the_patient_and_read_what_was_dispensed(self):
        prescription = Prescription.objects.create(patient=self.patient, doctor=self.doctor,
                                                   item=self.item, quantity=2)
        dispense_prescription(prescription=prescription, pharmacist=self.pharmacist)
        client = APIClient()
        client.force_authenticate(self.pharmacist)
        self.assertEqual(client.get(f"/api/patients/{self.patient.pk}/").status_code, 200)
        rows = client.get("/api/prescriptions/", {"patient": self.patient.pk}).data
        rows = rows.get("results", rows)
        self.assertEqual(rows[0]["item_name"], "Paracetamol 500mg")
        self.assertEqual(rows[0]["status"], "dispensed")

    def test_the_clinical_record_stays_shut_to_the_pharmacy(self):
        """Reading the drug list is not reading the chart."""
        Prescription.objects.create(patient=self.patient, doctor=self.doctor,
                                    item=self.item, quantity=2)
        client = APIClient()
        client.force_authenticate(self.pharmacist)
        self.assertEqual(
            client.get(f"/api/patients/{self.patient.pk}/overview/").status_code, 403)
