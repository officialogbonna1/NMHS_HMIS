"""
Deleting a patient: the Super Admin alone, deliberately, and never over the
hospital's record.

Three gates, and this file holds all three — who is asking, whether they meant
it, and whether anything in the permanent record points at the row. The
frontend hides the button; none of that is what refuses.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.billing.models import Charge, Payment
from apps.billing.services import add_charge, record_payment
from apps.clinical.models import Vitals
from apps.core.models import AuditLog
from apps.patients.models import Allergy, Patient


class WhoMayDeleteAPatient(TestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.hospital_admin = User.objects.create_user(username="ha", password="t",
                                                       role="hospital_admin")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")
        self.records = User.objects.create_user(username="ro", password="t",
                                                role="records_officer")
        self.client = APIClient()

    def _patient(self):
        return Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                      created_by=self.reception)

    def _delete(self, user, patient, confirm=None):
        self.client.force_authenticate(user)
        body = {"confirm": patient.patient_number if confirm is None else confirm}
        return self.client.delete(f"/api/patients/{patient.uuid}/", body, format="json")

    def test_the_super_admin_can_delete(self):
        patient = self._patient()
        response = self._delete(self.super_admin, patient)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Patient.objects.filter(pk=patient.pk).exists())

    def test_a_django_superuser_with_the_admin_role_can_delete(self):
        """The other half of `IsSuperAdmin` — the flag, as well as the role."""
        root = User.objects.create_superuser(username="root", password="t", role="admin")
        patient = self._patient()
        self.assertEqual(self._delete(root, patient).status_code, 204)

    def test_a_superuser_with_no_hospital_role_still_reaches_no_patient(self):
        """
        `IsSuperAdmin` lets the flag through, and then `patient_queryset_for`
        — the same assignment filter the whole viewset runs on — hands a
        role-less account nothing to delete. The permission is not the
        scoping, and this is the existing rule that "the HMIS role is the
        source of truth for API access", still holding on the way out.
        """
        root = User.objects.create_superuser(username="root2", password="t", role="")
        patient = self._patient()
        self.assertEqual(self._delete(root, patient).status_code, 404)
        self.assertTrue(Patient.objects.filter(pk=patient.pk).exists())

    def test_an_ordinary_hospital_admin_cannot(self):
        """`hospital_admin` passes every other admin check in the system."""
        patient = self._patient()
        self.assertEqual(self._delete(self.hospital_admin, patient).status_code, 403)
        self.assertTrue(Patient.objects.filter(pk=patient.pk).exists())

    def test_reception_cannot_although_it_registers_patients(self):
        patient = self._patient()
        self.assertEqual(self._delete(self.reception, patient).status_code, 403)
        self.assertTrue(Patient.objects.filter(pk=patient.pk).exists())

    def test_the_cash_desk_the_wards_and_records_cannot(self):
        for user in (self.cashier, self.doctor, self.nurse, self.records):
            with self.subTest(user.role):
                patient = self._patient()
                self.assertEqual(self._delete(user, patient).status_code, 403)
                self.assertTrue(Patient.objects.filter(pk=patient.pk).exists())

    def test_an_unauthenticated_caller_cannot(self):
        patient = self._patient()
        response = APIClient().delete(f"/api/patients/{patient.uuid}/",
                                      {"confirm": patient.patient_number}, format="json")
        self.assertIn(response.status_code, (401, 403))
        self.assertTrue(Patient.objects.filter(pk=patient.pk).exists())

    def test_reception_can_still_register_and_amend_a_patient(self):
        """Narrowing `destroy` must not narrow anything beside it."""
        self.client.force_authenticate(self.reception)
        created = self.client.post("/api/patients/", {
            "first_name": "New", "last_name": "Patient", "sex": "F",
        }, format="json")
        self.assertEqual(created.status_code, 201)
        amended = self.client.patch(f"/api/patients/{created.data['uuid']}/",
                                    {"phone_number": "08030000000"}, format="json")
        self.assertEqual(amended.status_code, 200)


class DeletionIsDeliberate(TestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.client = APIClient()
        self.client.force_authenticate(self.super_admin)

    def _delete(self, body=None):
        return self.client.delete(f"/api/patients/{self.patient.uuid}/", body or {},
                                  format="json")

    def test_a_delete_with_no_confirmation_is_refused(self):
        response = self._delete()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "confirmation_required")
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())

    def test_the_wrong_hospital_number_is_refused(self):
        response = self._delete({"confirm": "NMHS-P999999"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())

    def test_the_patients_own_number_confirms_it(self):
        self.assertEqual(self._delete({"confirm": self.patient.patient_number}).status_code, 204)

    def test_the_number_is_accepted_however_it_was_typed(self):
        response = self._delete({"confirm": f"  {self.patient.patient_number.lower()}  "})
        self.assertEqual(response.status_code, 204)

    def test_an_unknown_patient_is_a_404_not_a_deletion(self):
        response = self.client.delete("/api/patients/8b0e5f1e-0000-4000-8000-000000000000/",
                                      {"confirm": "NMHS-P000001"}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_a_malformed_identifier_is_a_404(self):
        response = self.client.delete("/api/patients/not-a-uuid/",
                                      {"confirm": "NMHS-P000001"}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_the_deletion_is_itself_recorded(self):
        number, uuid = self.patient.patient_number, str(self.patient.uuid)
        self._delete({"confirm": number})
        entry = AuditLog.objects.filter(action="patients.deleted").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.super_admin)
        self.assertEqual(entry.details["patient_number"], number)
        self.assertEqual(entry.details["uuid"], uuid)
        self.assertEqual(entry.details["name"], "Doe, Jane")


class DeletionNeverTakesTheRecordWithIt(TestCase):
    """
    What happens to everything hanging off a patient.

    The financial and clinical history the hospital keeps is `PROTECT` at the
    database, and the endpoint turns that into an answer: 409, naming what is
    in the way. Only the rows that belong to nobody else cascade.
    """

    def setUp(self):
        self.super_admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")
        self.patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                              created_by=self.reception)
        self.client = APIClient()
        self.client.force_authenticate(self.super_admin)

    def _delete(self):
        return self.client.delete(f"/api/patients/{self.patient.uuid}/",
                                  {"confirm": self.patient.patient_number}, format="json")

    def test_a_patient_who_has_been_billed_cannot_be_deleted(self):
        add_charge(patient=self.patient, description="Consultation fee", amount="5000",
                   created_by=self.reception, source_type="consultation")
        response = self._delete()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "history_exists")
        self.assertEqual(response.data["references"]["charges"], 1)
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
        self.assertEqual(Charge.objects.count(), 1)

    def test_a_patient_who_has_paid_cannot_be_deleted_and_the_payment_survives(self):
        add_charge(patient=self.patient, description="Card", amount="500",
                   created_by=self.reception, source_type="card")
        record_payment(patient=self.patient, amount="500", received_by=self.cashier)
        response = self._delete()
        self.assertEqual(response.status_code, 409)
        self.assertIn("payments", response.data["references"])
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Payment.objects.first().amount, Decimal("500.00"))

    def test_a_patient_with_a_visit_cannot_be_deleted(self):
        from apps.workflow.models import Visit
        Visit.objects.create(patient=self.patient, opened_by=self.reception)
        response = self._delete()
        self.assertEqual(response.status_code, 409)
        self.assertIn("visits", response.data["references"])

    def test_a_patient_with_a_laboratory_order_cannot_be_deleted(self):
        from apps.laboratory.models import LabOrder
        LabOrder.objects.create(patient=self.patient, requested_by=self.doctor,
                                created_by=self.doctor)
        response = self._delete()
        self.assertEqual(response.status_code, 409)
        self.assertIn("lab_orders", response.data["references"])

    def test_the_refusal_names_every_kind_of_history_at_once(self):
        from apps.workflow.models import Visit
        add_charge(patient=self.patient, description="Card", amount="500",
                   created_by=self.reception, source_type="card")
        record_payment(patient=self.patient, amount="500", received_by=self.cashier)
        Visit.objects.create(patient=self.patient, opened_by=self.reception)
        references = self._delete().data["references"]
        self.assertEqual(set(references), {"charges", "payments", "visits"})

    def test_a_registration_mistake_deletes_with_everything_that_only_describes_them(self):
        """
        Vitals, notes, the health-record tiles and appointments cascade — none
        of them outlives the person they describe, and none of them is money
        or audit.
        """
        Vitals.objects.create(patient=self.patient, recorded_by=self.nurse,
                              visit_time=timezone.now(), temperature_c="37.0")
        Allergy.objects.create(patient=self.patient, name="Penicillin")
        Appointment.objects.create(patient=self.patient, doctor=self.doctor)
        patient_id = self.patient.pk

        self.assertEqual(self._delete().status_code, 204)
        self.assertFalse(Patient.objects.filter(pk=patient_id).exists())
        self.assertFalse(Vitals.objects.filter(patient_id=patient_id).exists())
        self.assertFalse(Allergy.objects.filter(patient_id=patient_id).exists())
        self.assertFalse(Appointment.objects.filter(patient_id=patient_id).exists())
        # The audit row is not a dependent record and outlives all of it.
        self.assertTrue(AuditLog.objects.filter(action="patients.deleted").exists())

    def test_nothing_is_deleted_when_the_refusal_is_a_409(self):
        Vitals.objects.create(patient=self.patient, recorded_by=self.nurse,
                              visit_time=timezone.now(), temperature_c="37.0")
        add_charge(patient=self.patient, description="Card", amount="500",
                   created_by=self.reception, source_type="card")
        self.assertEqual(self._delete().status_code, 409)
        self.assertTrue(Vitals.objects.filter(patient=self.patient).exists())
        self.assertTrue(Charge.objects.filter(patient=self.patient).exists())
        self.assertFalse(AuditLog.objects.filter(action="patients.deleted").exists())
