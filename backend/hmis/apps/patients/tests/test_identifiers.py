"""
The two identifiers a patient carries, and the line between them.

`Patient.uuid` addresses the record; `Patient.patient_number` is what a person
reads off a card. Neither is an authorisation — the last case here is the one
that matters most: knowing either identifier gets an unassigned clinician a
404, exactly as an unknown one would.
"""

import uuid as uuid_module

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core import identifiers
from apps.patients.models import Patient


def a_patient(**kwargs):
    fields = {"first_name": "Ada", "last_name": "Okonkwo", "sex": "F"}
    fields.update(kwargs)
    return Patient.objects.create(**fields)


class IdentifierFormatTests(TestCase):
    def test_a_number_names_the_register_it_belongs_to(self):
        self.assertEqual(identifiers.format_number(identifiers.PATIENT, 1), "NMHS-P000001")
        self.assertEqual(identifiers.format_number(identifiers.STAFF, 1), "NMHS-S000001")

    def test_a_legacy_number_keeps_its_figures_and_only_gains_the_letter(self):
        # The card in the patient's hand still reads the same six digits.
        self.assertEqual(
            identifiers.adopt_number("NMHS-000013", identifiers.PATIENT, 13),
            "NMHS-P000013",
        )

    def test_a_number_already_in_the_current_shape_is_left_alone(self):
        # Even when the sequence disagrees: an identifier that moves is not one.
        self.assertEqual(
            identifiers.adopt_number("NMHS-P000042", identifiers.PATIENT, 999),
            "NMHS-P000042",
        )

    def test_a_number_from_the_other_register_is_reissued_not_reused(self):
        self.assertEqual(
            identifiers.adopt_number("NMHS-S000003", identifiers.PATIENT, 7),
            "NMHS-P000007",
        )

    def test_an_unrecognised_or_missing_number_is_issued_fresh(self):
        self.assertEqual(identifiers.adopt_number("", identifiers.PATIENT, 4), "NMHS-P000004")
        self.assertEqual(identifiers.adopt_number("OLD/4", identifiers.PATIENT, 4), "NMHS-P000004")


class PatientNumberTests(TestCase):
    def test_registering_issues_a_number_in_the_new_format(self):
        patient = a_patient()
        self.assertRegex(patient.patient_number, r"^NMHS-P\d{6}$")

    def test_numbers_run_in_sequence_and_are_unique(self):
        numbers = [a_patient(last_name=f"P{n}").patient_number for n in range(3)]
        self.assertEqual(len(set(numbers)), 3)
        self.assertEqual(numbers, sorted(numbers))

    def test_the_number_does_not_move_when_the_record_is_edited(self):
        patient = a_patient()
        issued = patient.patient_number
        patient.phone_number = "08030000000"
        patient.last_name = "Married-Name"
        patient.save()
        patient.refresh_from_db()
        self.assertEqual(patient.patient_number, issued)

    def test_a_deleted_patients_number_is_not_handed_to_the_next_one(self):
        gone = a_patient(last_name="Gone").patient_number
        Patient.objects.filter(patient_number=gone).delete()
        self.assertNotEqual(a_patient(last_name="Next").patient_number, gone)

    def test_file_number_is_the_same_value_under_its_old_name(self):
        patient = a_patient()
        self.assertEqual(patient.file_number, patient.patient_number)

    def test_the_number_is_not_derived_from_the_uuid(self):
        patient = a_patient()
        self.assertNotIn(str(patient.uuid)[:8], patient.patient_number)

    def test_every_patient_gets_their_own_uuid(self):
        uuids = {a_patient(last_name=f"P{n}").uuid for n in range(3)}
        self.assertEqual(len(uuids), 3)


class PatientApiIdentityTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="desk", password="t", role="reception")
        self.patient = a_patient(phone_number="08031234567")
        self.client = APIClient()
        self.client.force_authenticate(self.reception)

    def test_the_payload_carries_both_identifiers_under_their_own_names(self):
        response = self.client.get(f"/api/patients/{self.patient.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["uuid"], str(self.patient.uuid))
        self.assertEqual(response.data["patient_number"], self.patient.patient_number)
        # The old name still answers, with the same value, for existing callers.
        self.assertEqual(response.data["file_number"], self.patient.patient_number)

    def test_a_patient_can_be_fetched_by_uuid(self):
        response = self.client.get(f"/api/patients/{self.patient.uuid}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["patient_number"], self.patient.patient_number)

    def test_the_legacy_numeric_route_still_answers(self):
        # Every nested `?patient=` filter and every existing client holds it.
        response = self.client.get(f"/api/patients/{self.patient.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_an_unknown_uuid_is_a_404_not_a_500(self):
        response = self.client.get(f"/api/patients/{uuid_module.uuid4()}/")
        self.assertEqual(response.status_code, 404)

    def test_a_lookup_value_that_is_neither_is_a_404_not_a_500(self):
        self.assertEqual(self.client.get("/api/patients/NMHS-P000001/").status_code, 404)
        self.assertEqual(self.client.get("/api/patients/not-an-id/").status_code, 404)

    def test_the_number_is_never_accepted_from_a_registration_form(self):
        response = self.client.post("/api/patients/", {
            "first_name": "New", "last_name": "Baby", "sex": "F",
            "patient_number": "NMHS-P999999", "file_number": "NMHS-P999999",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.data["patient_number"], "NMHS-P999999")

    def test_the_number_cannot_be_edited_afterwards(self):
        issued = self.patient.patient_number
        response = self.client.patch(f"/api/patients/{self.patient.pk}/",
                                     {"patient_number": "NMHS-P999999"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.patient_number, issued)


class PatientSearchTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="desk2", password="t", role="reception")
        self.patient = a_patient(phone_number="08031234567")
        a_patient(first_name="Chidi", last_name="Eze", phone_number="08099999999")
        self.client = APIClient()
        self.client.force_authenticate(self.reception)

    def _search(self, term):
        response = self.client.get("/api/patients/", {"search": term})
        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        return [row["patient_number"] for row in rows]

    def test_a_patient_is_found_by_the_number_on_their_card(self):
        self.assertEqual(self._search(self.patient.patient_number), [self.patient.patient_number])

    def test_a_patient_is_found_by_name(self):
        self.assertEqual(self._search("Okonkwo"), [self.patient.patient_number])

    def test_a_patient_is_found_by_the_phone_number_read_out_to_the_desk(self):
        self.assertEqual(self._search("08031234567"), [self.patient.patient_number])


class IdentifiersAreNotPermissionTests(TestCase):
    """
    The point of the whole arrangement: an identifier addresses a record, it
    does not unlock one. A doctor with no claim on this patient gets the same
    404 whichever of the three they quote.
    """

    def setUp(self):
        self.stranger = User.objects.create_user(username="passing-doctor", password="t", role="doctor")
        self.patient = a_patient()
        self.client = APIClient()
        self.client.force_authenticate(self.stranger)

    def test_knowing_the_uuid_opens_nothing(self):
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.uuid}/").status_code, 404)

    def test_knowing_the_numeric_id_opens_nothing(self):
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.pk}/").status_code, 404)

    def test_knowing_the_patient_number_finds_nothing(self):
        response = self.client.get("/api/patients/", {"search": self.patient.patient_number})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("results", response.data), [])

    def test_the_chart_stays_shut_to_a_uuid_too(self):
        self.assertEqual(
            self.client.get(f"/api/patients/{self.patient.uuid}/overview/").status_code, 404
        )
