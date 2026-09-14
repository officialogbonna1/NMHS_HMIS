"""
Purging a patient from Django admin: the Super Admin's deliberate way past
rule 37, for a registration that was never a real patient.

The API still refuses a patient with history — `test_deletion.py` holds that,
and it is re-checked at the bottom here. This file holds the other door: who
may open it, that it asks twice (the hospital number and a reason), that it
takes the whole record and nobody else's, and that it leaves a trail.
"""
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import models
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import (Adjustment, Charge, PatientLedger, Payment, PaymentAllocation,
                                 PaymentDeferral, Refund, RefundAllocation)
from apps.billing.services import (add_charge, apply_percentage_discount, defer_charge,
                                   record_payment, refund_payment)
from apps.clinical.models import Vitals
from apps.core.models import AuditLog
from apps.departments.models import Department
from apps.inpatient.models import Admission, Bed, Ward
from apps.laboratory.models import LabOrder
from apps.patients.models import Allergy, Patient
from apps.patients.services import PURGE_ORDER, purge_patient
from apps.patients.views import PROTECTED_HISTORY
from apps.workflow.models import PatientRoute, Visit


class PurgeCoversEveryProtectedRelation(TestCase):
    def test_every_protect_foreign_key_to_a_patient_is_purged(self):
        """A new PROTECT relation not listed here would make the purge 500."""
        protected = {rel.get_accessor_name() for rel in Patient._meta.related_objects
                     if rel.on_delete is models.PROTECT}
        self.assertEqual(protected, set(PURGE_ORDER))

    def test_it_is_the_same_history_the_api_refuses_to_delete(self):
        self.assertEqual(set(PURGE_ORDER), set(PROTECTED_HISTORY))


class PurgingFromDjangoAdmin(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(username="root", password="t", role="admin",
                                                  email="root@example.com")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")
        self.patient = self._patient("Demo", "Screenshot")
        self.client.force_login(self.root)

    def _patient(self, last, first):
        return Patient.objects.create(first_name=first, last_name=last, sex="F",
                                      created_by=self.reception)

    def give_history(self, patient):
        """Every kind of history the hospital keeps, and some it does not."""
        add_charge(patient=patient, description="Consultation fee", amount="5000",
                   created_by=self.reception, source_type="consultation")
        lab = add_charge(patient=patient, description="Laboratory: FBC", amount="3500",
                         created_by=self.reception, source_type="lab_test")
        apply_percentage_discount(charge=lab, percent=10, reason="Staff relative",
                                  approved_by=self.cashier)
        defer_charge(charge=lab, reason="Will pay Friday", approved_by=self.cashier)
        payment = record_payment(patient=patient, amount="5000", received_by=self.cashier)
        refund_payment(payment=payment, amount="1000", reason="Overcharged",
                       processed_by=self.cashier)
        visit = Visit.objects.create(patient=patient, opened_by=self.reception)
        PatientRoute.objects.create(visit=visit, department=Department.objects.first(),
                                    routed_by=self.reception, purpose="laboratory")
        LabOrder.objects.create(patient=patient, requested_by=self.doctor,
                                created_by=self.doctor, visit=visit)
        bed = Bed.objects.create(ward=Ward.objects.create(name=f"Ward {patient.pk}"), number="1")
        Admission.objects.create(patient=patient, bed=bed, admitted_by=self.nurse, visit=visit)
        Vitals.objects.create(patient=patient, recorded_by=self.nurse,
                              visit_time=timezone.now(), temperature_c="37.0")
        Allergy.objects.create(patient=patient, name="Penicillin")
        return bed

    def purge_url(self, patient):
        return reverse("admin:patients_patient_purge", args=[patient.pk])

    def purge(self, patient, confirm=None, reason="Demo registration"):
        return self.client.post(self.purge_url(patient), {
            "confirm": patient.patient_number if confirm is None else confirm,
            "reason": reason,
        })

    def test_the_delete_button_leads_to_the_purge_page(self):
        response = self.client.get(reverse("admin:patients_patient_delete",
                                           args=[self.patient.pk]))
        self.assertRedirects(response, self.purge_url(self.patient))

    def test_the_page_says_what_will_go_and_what_money_is_on_record(self):
        self.give_history(self.patient)
        response = self.client.get(self.purge_url(self.patient))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.patient.patient_number)
        self.assertContains(response, "Charges: 2")
        self.assertContains(response, "Payments: 1")
        self.assertContains(response, "Refunds: 1")
        self.assertContains(response, "8,500.00")

    def test_a_super_admin_purges_a_patient_with_their_whole_history(self):
        bed = self.give_history(self.patient)
        patient_id = self.patient.pk

        response = self.purge(self.patient)

        self.assertRedirects(response, reverse("admin:patients_patient_changelist"))
        self.assertFalse(Patient.objects.filter(pk=patient_id).exists())
        for model in (Charge, Payment, Refund, Adjustment, PaymentDeferral, PatientLedger,
                      Visit, LabOrder, Admission, Vitals, Allergy):
            with self.subTest(model=model.__name__):
                self.assertFalse(model.objects.filter(patient_id=patient_id).exists())
        self.assertFalse(PaymentAllocation.objects.exists())
        self.assertFalse(RefundAllocation.objects.exists())
        self.assertFalse(PatientRoute.objects.exists())
        # The bed stays, and is free again.
        self.assertTrue(Bed.objects.filter(pk=bed.pk).exists())
        self.assertFalse(bed.admissions.filter(status="admitted").exists())

    def test_another_patients_record_is_not_touched(self):
        other = self._patient("Person", "Real")
        self.give_history(self.patient)
        self.give_history(other)

        self.purge(self.patient)

        self.assertTrue(Patient.objects.filter(pk=other.pk).exists())
        self.assertEqual(Charge.objects.filter(patient=other).count(), 2)
        self.assertEqual(Payment.objects.filter(patient=other).count(), 1)
        self.assertEqual(Refund.objects.filter(patient=other).count(), 1)
        self.assertEqual(Visit.objects.filter(patient=other).count(), 1)
        self.assertEqual(LabOrder.objects.filter(patient=other).count(), 1)
        self.assertEqual(Admission.objects.filter(patient=other).count(), 1)
        self.assertTrue(PaymentAllocation.objects.filter(payment__patient=other).exists())

    def test_the_purge_is_recorded_with_the_reason_and_what_it_removed(self):
        self.give_history(self.patient)
        number, uuid = self.patient.patient_number, str(self.patient.uuid)

        self.purge(self.patient)

        entry = AuditLog.objects.get(action="patients.purged")
        self.assertEqual(entry.actor, self.root)
        self.assertEqual(entry.details["patient_number"], number)
        self.assertEqual(entry.details["uuid"], uuid)
        self.assertEqual(entry.details["name"], "Demo, Screenshot")
        self.assertEqual(entry.details["reason"], "Demo registration")
        self.assertEqual(entry.details["removed"]["charges"], 2)
        self.assertEqual(entry.details["money"]["charged"], "8500.00")
        self.assertTrue(LogEntry.objects.filter(action_flag=DELETION,
                                                object_repr__contains=number).exists())

    def test_a_patient_with_no_history_is_deleted_the_same_way(self):
        self.assertRedirects(self.purge(self.patient),
                             reverse("admin:patients_patient_changelist"))
        self.assertFalse(Patient.objects.filter(pk=self.patient.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="patients.purged").exists())

    def test_the_wrong_number_deletes_nothing(self):
        self.give_history(self.patient)
        for confirm in ("", "NMHS-P999999"):
            with self.subTest(confirm=confirm):
                response = self.purge(self.patient, confirm=confirm)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
                self.assertEqual(Charge.objects.filter(patient=self.patient).count(), 2)
        self.assertFalse(AuditLog.objects.filter(action="patients.purged").exists())

    def test_no_reason_deletes_nothing(self):
        response = self.purge(self.patient, reason="   ")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())

    def test_there_is_no_bulk_delete(self):
        response = self.client.get(reverse("admin:patients_patient_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="delete_selected"')

    def test_an_ordinary_hospital_admin_cannot_even_with_every_django_permission(self):
        hospital_admin = User.objects.create_user(username="ha", password="t",
                                                  role="hospital_admin", is_staff=True)
        hospital_admin.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="patients"))
        self.give_history(self.patient)
        self.client.force_login(hospital_admin)

        self.assertEqual(self.client.get(self.purge_url(self.patient)).status_code, 403)
        self.assertEqual(self.purge(self.patient).status_code, 403)
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())

    def test_the_service_itself_refuses_anyone_but_the_super_admin(self):
        hospital_admin = User.objects.create_user(username="ha2", password="t",
                                                  role="hospital_admin")
        with self.assertRaises(PermissionDenied):
            purge_patient(patient=self.patient, actor=hospital_admin, reason="Duplicate",
                          confirmation=self.patient.patient_number)
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())

    def test_the_api_still_refuses_a_patient_with_history(self):
        """The purge is Django admin's; every HMIS screen keeps rule 37's 409."""
        self.give_history(self.patient)
        api = APIClient()
        api.force_authenticate(self.root)
        response = api.delete(f"/api/patients/{self.patient.uuid}/",
                              {"confirm": self.patient.patient_number}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
