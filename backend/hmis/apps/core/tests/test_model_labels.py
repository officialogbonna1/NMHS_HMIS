"""
What a record calls itself.

Django labels every foreign-key dropdown, every inline row, every breadcrumb
and every admin log entry with `str(obj)`, and a model with no `__str__` of
its own answers "Ward object (1)". That is unreadable on the screens the
hospital's own administrators work from: a bed cannot be assigned to a ward
nobody can name, and an admission cannot be filed against a bed that reads as
an integer.

These are presentation tests. Nothing here asserts a workflow — it asserts
what the workflow's rows are *called* — with two exceptions at the foot of
this file, which exist to hold the line the labels were not allowed to cross:
`Patient.__str__` gained its hospital number, and the API's `patient_name`
did not (rule 32 — the two identifiers are not interchangeable, and a screen
decides for itself how to show them).
"""
from datetime import date
from decimal import Decimal

from django.apps import apps as django_apps
from django.db import models
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.billing.models import Adjustment, Charge, Payment, PatientLedger
from apps.clinical.models import Vitals
from apps.departments.models import Department, Service
from apps.inpatient.models import Admission, Bed, BedTransfer, DischargeSummary, Ward
from apps.patients.models import (
    Allergy, MedicalCondition, MedicalDevice, MedicalTest, Medication, Patient,
    SurgicalHistory, Vaccination,
)
from apps.workflow.models import PatientRoute, Visit

#: Our own apps, as opposed to Django's own tables.
OUR_APPS = ("accounts", "patients", "clinical", "inventory", "pharmacy", "sales",
            "appointments", "departments", "workflow", "billing", "diagnostics",
            "inpatient", "laboratory", "core")


class EveryModelNamesItself(TestCase):
    """
    The regression guard. A new model with no `__str__` ships a dropdown full
    of "object (1)" and nobody notices until somebody has to use the screen.
    """

    def test_no_model_falls_back_to_the_default_representation(self):
        nameless = [
            model._meta.label for model in django_apps.get_models()
            if model._meta.app_label in OUR_APPS
            and not model._meta.auto_created
            and model.__str__ is models.Model.__str__
        ]
        self.assertEqual(nameless, [], f"these would read as 'Model object (id)': {nameless}")


class Fixture(TestCase):
    """One ward, one bed, one patient, and enough of a visit to route them."""

    @classmethod
    def setUpTestData(cls):
        cls.department = Department.objects.create(code="medicine", name="General Medicine")
        cls.ward = Ward.objects.create(name="Male Medical Ward", department=cls.department)
        cls.bed = Bed.objects.create(ward=cls.ward, number="01")
        cls.spare_bed = Bed.objects.create(ward=cls.ward, number="02")
        cls.nurse = User.objects.create_user(username="nur", password="t", role="nurse",
                                             first_name="Amina", last_name="Bello")
        cls.doctor = User.objects.create_user(username="doc", password="t", role="doctor",
                                              first_name="Chidi", last_name="Nwosu")
        cls.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M")


class WardAndBedLabels(Fixture):
    """The report this work started from: a bed's ward dropdown."""

    def test_a_ward_is_its_name(self):
        self.assertEqual(str(self.ward), "Male Medical Ward")
        self.assertNotIn("object", str(self.ward))

    def test_a_bed_names_its_ward_as_well_as_its_number(self):
        """"01" alone is every ward's first bed."""
        self.assertEqual(str(self.bed), "Bed 01 — Male Medical Ward")
        self.assertNotIn("object", str(self.bed))

    def test_a_department_is_its_name(self):
        self.assertEqual(str(self.department), "General Medicine")

    def test_a_service_names_the_department_that_performs_it(self):
        service = Service.objects.create(department=self.department, name="Dressing",
                                         code="dressing", price=Decimal("1500"))
        self.assertEqual(str(service), "Dressing — General Medicine")


class PeopleLabels(Fixture):
    def test_a_patient_leads_with_their_hospital_number(self):
        self.assertEqual(str(self.patient), f"{self.patient.patient_number} — Doe, John")
        self.assertTrue(self.patient.patient_number.startswith("NMHS-P"))
        self.assertNotIn("object", str(self.patient))

    def test_a_patient_with_no_number_yet_is_still_named(self):
        """`patient_number` is issued on the first save. An unsaved row still
        has to render — a form's own error message quotes it."""
        self.assertEqual(str(Patient(first_name="Ada", last_name="Obi", sex="F")), "Obi, Ada")

    def test_a_member_of_staff_leads_with_their_staff_number(self):
        self.assertEqual(str(self.nurse), f"{self.nurse.staff_number} — Amina Bello (Nurse)")
        self.assertTrue(self.nurse.staff_number.startswith("NMHS-S"))

    def test_an_account_with_no_role_and_no_name_reads_as_its_username(self):
        """Not every row in the users table is staff, and none of them may
        read as "User object (7)"."""
        technical = User.objects.create_user(username="integration", password="t")
        self.assertEqual(str(technical), "integration")


class WardWorkLabels(Fixture):
    def setUp(self):
        self.admission = Admission.objects.create(
            patient=self.patient, bed=self.bed, admitted_by=self.doctor,
            attending_doctor=self.doctor)

    def test_an_admission_names_the_patient_the_bed_and_the_state(self):
        self.assertEqual(
            str(self.admission),
            f"{self.patient.patient_number} — Doe, John · Bed 01 — Male Medical Ward · Admitted")

    def test_a_bed_transfer_names_both_beds(self):
        transfer = BedTransfer.objects.create(admission=self.admission, from_bed=self.bed,
                                              to_bed=self.spare_bed, transferred_by=self.nurse)
        self.assertEqual(
            str(transfer),
            f"{self.patient.patient_number} — Doe, John · Bed 01 — Male Medical Ward "
            f"→ Bed 02 — Male Medical Ward")

    def test_a_discharge_summary_is_its_reference_and_the_patient(self):
        summary = DischargeSummary.objects.create(
            admission=self.admission, diagnosis="Resolved", summary="Home.",
            completed_by=self.doctor)
        self.assertEqual(str(summary), f"{summary.reference}: {self.patient}")
        self.assertIn("DCH-", str(summary))


class ClinicalAndRoutingLabels(Fixture):
    def test_vitals_name_the_patient_and_when_they_were_taken(self):
        vitals = Vitals.objects.create(patient=self.patient, recorded_by=self.nurse,
                                       visit_time=timezone.now(), heart_rate=78)
        self.assertIn(str(self.patient), str(vitals))
        self.assertIn("vitals", str(vitals))
        self.assertNotIn("object", str(vitals))

    def test_a_visit_names_the_patient_its_kind_and_its_state(self):
        visit = Visit.objects.create(patient=self.patient, opened_by=self.nurse)
        self.assertIn(str(self.patient), str(visit))
        self.assertIn("Outpatient", str(visit))
        self.assertIn("Open", str(visit))

    def test_a_route_says_who_was_sent_where_and_what_for(self):
        visit = Visit.objects.create(patient=self.patient, opened_by=self.nurse)
        route = PatientRoute.objects.create(visit=visit, department=self.department,
                                            purpose="vitals", routed_by=self.nurse)
        self.assertEqual(
            str(route),
            f"{self.patient} · Vitals → General Medicine (Queued)")

    def test_an_appointment_names_the_patient_and_the_doctor_waited_on(self):
        appointment = Appointment.objects.create(patient=self.patient, doctor=self.doctor,
                                                 reason="Follow-up")
        self.assertEqual(str(appointment), f"{self.patient} · Chidi Nwosu (Queued)")


class MoneyLabels(Fixture):
    def test_a_charge_names_the_patient_the_service_the_amount_and_the_state(self):
        charge = Charge.objects.create(patient=self.patient, description="Laboratory: FBC",
                                       amount=Decimal("3500"), created_by=self.doctor)
        self.assertEqual(str(charge),
                         f"{self.patient} · Laboratory: FBC · 3,500.00 (Unpaid)")

    def test_a_payment_names_who_paid_how_much_and_how(self):
        payment = Payment.objects.create(patient=self.patient, amount=Decimal("2000"),
                                         received_by=self.doctor)
        self.assertEqual(str(payment), f"{self.patient} · 2,000.00 Cash (Front desk)")

    def test_a_walk_in_payment_has_no_patient_and_still_reads(self):
        """A POS customer is deliberately not a patient (rule 39)."""
        payment = Payment.objects.create(amount=Decimal("500"), channel="pharmacy",
                                         received_by=self.doctor)
        self.assertEqual(str(payment), "Walk-in customer · 500.00 Cash (Pharmacy)")

    def test_an_adjustment_names_what_was_forgiven(self):
        adjustment = Adjustment.objects.create(patient=self.patient, kind="waiver",
                                               amount=Decimal("1000"), reason="Hardship",
                                               approved_by=self.doctor)
        self.assertEqual(str(adjustment), f"{self.patient} · Waiver 1,000.00")

    def test_a_ledger_names_the_patient_and_what_they_owe(self):
        ledger = PatientLedger.objects.create(patient=self.patient,
                                              total_charges=Decimal("3500"))
        self.assertEqual(str(ledger), f"{self.patient} · outstanding 3,500.00")


class HealthRecordTileLabels(Fixture):
    """The nine tiles are rows on a chart, and every one of them was nameless."""

    def test_an_allergy_names_the_substance_and_flags_a_severe_one(self):
        mild = Allergy.objects.create(patient=self.patient, name="Dust")
        severe = Allergy.objects.create(patient=self.patient, name="Penicillin",
                                        is_dangerous=True)
        self.assertEqual(str(mild), f"{self.patient} · allergy: Dust")
        self.assertEqual(str(severe), f"{self.patient} · allergy: Penicillin (severe)")

    def test_a_medication_carries_its_strength(self):
        drug = Medication.objects.create(patient=self.patient, name="Salbutamol",
                                         strength="100mcg")
        self.assertEqual(str(drug), f"{self.patient} · medication: Salbutamol 100mcg")

    def test_the_other_tiles_name_the_patient_and_the_entry(self):
        rows = [
            (MedicalCondition.objects.create(patient=self.patient, name="Asthma"), "Asthma"),
            (MedicalDevice.objects.create(patient=self.patient, name="Inhaler"), "Inhaler"),
            (SurgicalHistory.objects.create(patient=self.patient, name="Appendectomy",
                                            surgery_date=date(2019, 4, 2)), "Appendectomy"),
            (Vaccination.objects.create(patient=self.patient, name="Tetanus",
                                        date_administered=date(2024, 1, 9)), "Tetanus"),
            (MedicalTest.objects.create(patient=self.patient, title="Chest X-ray",
                                        test_type="xray", test_date=date(2025, 6, 1)),
             "Chest X-ray"),
        ]
        for row, detail in rows:
            with self.subTest(row=type(row).__name__):
                self.assertIn(str(self.patient), str(row))
                self.assertIn(detail, str(row))
                self.assertNotIn("object", str(row))

    def test_a_date_that_was_never_recorded_does_not_break_the_label(self):
        """Every date on these tiles is optional, and a label is rendered on
        rows that have none."""
        surgery = SurgicalHistory.objects.create(patient=self.patient, name="Appendectomy")
        self.assertEqual(str(surgery), f"{self.patient} · surgery: Appendectomy (—)")


class AdminDropdownsShowTheLabels(Fixture):
    """
    `__str__` is only half the answer: an admin may override a label with its
    own form or an autocomplete. These render the real pages and read what
    comes back, which is what the report was actually about.
    """

    def setUp(self):
        self.root = User.objects.create_superuser(username="root", password="t", role="admin",
                                                  email="root@example.test")
        self.client.force_login(self.root)

    def test_the_ward_dropdown_on_the_bed_form_names_the_ward(self):
        """The bed's ward is an autocomplete, so the form renders only the
        ward already chosen; the rest arrive through the endpoint below."""
        page = self.client.get(
            reverse("admin:inpatient_bed_change", args=[self.bed.pk])).content.decode()
        self.assertIn("Male Medical Ward", page)
        self.assertNotIn(f"Ward object ({self.ward.pk})", page)

    def test_the_bed_dropdown_on_the_admission_form_names_ward_and_bed(self):
        page = self.client.get(reverse("admin:inpatient_admission_add")).content.decode()
        self.assertIn("Bed 01 — Male Medical Ward", page)
        self.assertNotIn(f"Bed object ({self.bed.pk})", page)

    def test_the_bed_column_on_the_admission_list_names_the_bed(self):
        Admission.objects.create(patient=self.patient, bed=self.bed, admitted_by=self.root)
        page = self.client.get(reverse("admin:inpatient_admission_changelist")).content.decode()
        self.assertIn("Bed 01 — Male Medical Ward", page)
        self.assertNotIn("Bed object", page)

    def test_the_staff_selector_on_a_department_names_people_not_ids(self):
        """`staff` is a many-to-many, rendered as a picker of `__str__`s."""
        page = self.client.get(
            reverse("admin:departments_department_change", args=[self.department.pk])
        ).content.decode()
        self.assertIn("Amina Bello", page)
        self.assertNotIn(f"User object ({self.nurse.pk})", page)

    def test_an_autocomplete_offers_the_same_label(self):
        """Autocomplete builds its own JSON, from `__str__` unless an admin
        overrides it — so it has to be checked separately."""
        response = self.client.get(
            reverse("admin:autocomplete"),
            {"app_label": "inpatient", "model_name": "bed", "field_name": "ward", "term": "medical"},
        )
        texts = [row["text"] for row in response.json()["results"]]
        self.assertIn("Male Medical Ward", texts)


class TheApiStillReadsTheNameAlone(Fixture):
    """
    The line this change was not allowed to cross. `patient_name` is a name:
    the screens print the hospital number themselves, from `patient_number`,
    and a payload that folded the two together would read
    "NMHS-P000001 — Doe, John (NMHS-P000001)" on every queue row.
    """

    def test_display_name_is_the_name_on_its_own(self):
        self.assertEqual(self.patient.display_name, "Doe, John")

    def test_an_api_payload_carries_the_name_and_the_number_separately(self):
        api = APIClient()
        api.force_authenticate(self.doctor)
        appointment = Appointment.objects.create(patient=self.patient, doctor=self.doctor,
                                                 reason="Follow-up")
        row = api.get("/api/appointments/").data["results"][0]
        self.assertEqual(row["patient_name"], "Doe, John")
        self.assertEqual(row["id"], appointment.id)

    def test_a_notification_title_is_not_a_hospital_number(self):
        """Rule 35's messages carry the number in their own place; a title
        that led with one would read as a filing reference, not a person."""
        from apps.billing import notifications

        User.objects.create_user(username="cash", password="t", role="cashier",
                                 first_name="Ngozi", last_name="Eze")
        raised = notifications.announce(
            event="charge_raised", patient=self.patient, actor=self.doctor,
            amount=Decimal("3500"), detail="Laboratory: FBC")
        self.assertTrue(raised)
        title = raised[0].title
        self.assertIn("Doe, John", title)
        self.assertNotIn(self.patient.patient_number, title)
