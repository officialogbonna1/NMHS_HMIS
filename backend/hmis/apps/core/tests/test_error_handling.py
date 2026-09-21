"""
What the API says when something goes wrong.

Two halves, and the whole point is that they are answered differently.

An **expected** failure — a bad field, a missing record, a role that may not,
an expired token, a database constraint — is the application working, and comes
back as a clean, consistent body with a status code that says what happened.

An **unexpected** one is a bug. It is logged whole, with its traceback, and the
caller is told nothing about it beyond a reference they can quote. These tests
assert both directions, including that the tidy answer is *not* given to a bug:
a handler that swallowed everything would pass half of this file and would be
exactly the thing the brief warns against.
"""
import logging
from unittest import mock

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.exceptions import SERVER_ERROR_DETAIL
from apps.patients.models import Patient


class ExpectedFailures(TestCase):
    """Every one of these is an answer, not a fault."""

    def setUp(self):
        self.api = APIClient()
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Okoro", sex="F",
                                              created_by=self.reception)

    def test_a_validation_error_names_the_field_and_carries_a_code(self):
        self.api.force_authenticate(self.reception)
        response = self.api.post("/api/patients/", {"first_name": ""}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "invalid")
        # The per-field detail a form marks its inputs up from survives.
        self.assertIn("last_name", response.data)
        self.assertIsInstance(response.data["detail"], str)

    def test_a_permission_failure_is_a_clean_403(self):
        self.api.force_authenticate(self.nurse)
        response = self.api.get("/api/finance/report/")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "permission_denied")
        self.assertIsInstance(response.data["detail"], str)

    def test_a_missing_record_is_a_clean_404_that_reveals_nothing(self):
        self.api.force_authenticate(self.reception)
        response = self.api.get("/api/patients/00000000-0000-0000-0000-000000000000/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "not_found")
        # Never Django's "Patient matching query does not exist", which would
        # confirm the shape of what the caller was probing for.
        self.assertNotIn("matching query", str(response.data).lower())

    def test_no_credentials_is_a_clean_401_or_403(self):
        response = self.api.get("/api/patients/")
        self.assertIn(response.status_code, (401, 403))
        self.assertIn("code", response.data)

    def test_an_invalid_token_does_not_500(self):
        self.api.credentials(HTTP_AUTHORIZATION="Token not-a-real-token")
        response = self.api.get("/api/patients/")
        self.assertIn(response.status_code, (401, 403))
        self.assertIn("detail", response.data)

    def test_a_django_validation_error_from_a_service_becomes_a_400(self):
        """Model `save()` methods and services all over this codebase raise
        Django's ValidationError, which DRF does not understand on its own."""
        self.api.force_authenticate(self.reception)
        with mock.patch("apps.patients.views.PatientViewSet.perform_create",
                        side_effect=DjangoValidationError({"phone": ["That number is not valid."]})):
            response = self.api.post("/api/patients/", {
                "first_name": "Ada", "last_name": "Okoro", "sex": "F",
            }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "invalid")
        self.assertIn("phone", response.data)

    def test_a_database_integrity_error_is_a_409_and_names_no_column(self):
        self.api.force_authenticate(self.reception)
        with mock.patch("apps.patients.views.PatientViewSet.perform_create",
                        side_effect=IntegrityError(
                            'duplicate key value violates unique constraint '
                            '"patients_patient_patient_number_key"')):
            response = self.api.post("/api/patients/", {
                "first_name": "Ada", "last_name": "Okoro", "sex": "F",
            }, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "conflict")
        body = str(response.data)
        self.assertNotIn("unique constraint", body)
        self.assertNotIn("patients_patient", body)


class ACodeAServiceChoseIsNeverOverwritten(TestCase):
    """
    The regression this handler introduced once, pinned so it cannot return.

    Services across this codebase answer with their own `code` —
    `refund_workflow_required` (rule 33), `insufficient_stock` (rule 30),
    `amendment_reason_required` (rule 45), `no_vitals`, `login_locked`. DRF
    wraps a `ValidationError`'s values in **lists**, so a handler that only
    recognised a string `code` replaced `["refund_workflow_required"]` with a
    generic `"invalid"` — and every caller and test reading that contract broke
    at once. The body's own `code` is now returned exactly as it stands.
    """

    def setUp(self):
        self.api = APIClient()
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.api.force_authenticate(self.cashier)
        self.patient = Patient.objects.create(first_name="Ada", last_name="Okoro", sex="F",
                                              created_by=self.cashier)
        # Registering is reception's, so the two patient-endpoint cases below
        # use their own client rather than the cash desk's.
        self.reception = User.objects.create_user(username="rec4", password="t", role="reception")
        self.desk = APIClient()
        self.desk.force_authenticate(self.reception)

    def test_a_list_wrapped_code_survives_the_handler(self):
        """The live path: a refund posted as an adjustment (rule 33)."""
        response = self.api.post("/api/adjustments/", {
            "patient": self.patient.pk, "kind": "refund",
            "amount": "1000.00", "reason": "Trying it the wrong way",
        }, format="json")

        self.assertEqual(response.status_code, 400)
        # The list form, untouched — not normalised to a string, and not
        # replaced by the handler's generic default.
        self.assertEqual(response.data["code"], ["refund_workflow_required"])

    def test_a_string_code_survives_too(self):
        from unittest import mock as _mock
        from rest_framework.exceptions import ValidationError as DRFValidationError

        with _mock.patch("apps.patients.views.PatientViewSet.perform_create",
                         side_effect=DRFValidationError({"code": "no_vitals",
                                                         "detail": "Nothing recorded."})):
            response = self.desk.post("/api/patients/", {
                "first_name": "Ada", "last_name": "Okoro", "sex": "F",
            }, format="json")
        self.assertEqual(response.data["code"], "no_vitals")

    def test_a_refusal_with_no_code_of_its_own_still_gets_one(self):
        """The handler's actual job: every body has the same keys."""
        response = self.desk.post("/api/patients/", {"first_name": ""}, format="json")
        self.assertEqual(response.data["code"], "invalid")


class UnexpectedFailures(TestCase):
    """A bug is logged in full and described to nobody."""

    def setUp(self):
        self.api = APIClient()
        self.reception = User.objects.create_user(username="rec2", password="t", role="reception")
        self.api.force_authenticate(self.reception)

    def test_a_programming_error_is_a_500_with_no_internals(self):
        with mock.patch("apps.patients.views.PatientViewSet.perform_create",
                        side_effect=KeyError("secret_internal_key")):
            with self.assertLogs("hmis.api", level="ERROR"):
                response = self.api.post("/api/patients/", {
                    "first_name": "Ada", "last_name": "Okoro", "sex": "F",
                }, format="json")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["code"], "server_error")
        self.assertEqual(response.data["detail"], SERVER_ERROR_DETAIL)
        body = str(response.data)
        # Not the exception, not its message, not a frame of the stack.
        self.assertNotIn("secret_internal_key", body)
        self.assertNotIn("KeyError", body)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("site-packages", body)

    def test_the_caller_gets_a_reference_that_is_in_the_log(self):
        """So a support call names the exact entry without the screen having
        leaked anything to produce it."""
        with mock.patch("apps.patients.views.PatientViewSet.perform_create",
                        side_effect=RuntimeError("boom")):
            with self.assertLogs("hmis.api", level="ERROR") as logged:
                response = self.api.post("/api/patients/", {
                    "first_name": "Ada", "last_name": "Okoro", "sex": "F",
                }, format="json")

        reference = response.data["reference"]
        self.assertTrue(reference.startswith("RF-"))
        self.assertIn(reference, "\n".join(logged.output))
        # And the traceback really is in the log, where it is diagnosable.
        self.assertIn("RuntimeError", "\n".join(logged.output))

    def test_a_bug_is_not_dressed_up_as_a_validation_error(self):
        """The failure mode this handler must not have: swallowing everything
        and reporting a bug as a tidy 400 nobody investigates."""
        with mock.patch("apps.patients.views.PatientViewSet.perform_create",
                        side_effect=AttributeError("'NoneType' has no attribute 'pk'")):
            with self.assertLogs("hmis.api", level="ERROR"):
                response = self.api.post("/api/patients/", {
                    "first_name": "Ada", "last_name": "Okoro", "sex": "F",
                }, format="json")

        self.assertEqual(response.status_code, 500)
        self.assertNotEqual(response.status_code, 400)
