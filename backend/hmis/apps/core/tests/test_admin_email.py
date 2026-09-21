"""
Admin email, and the rule it must never break.

**A hospital transaction is the record; the email is a courtesy.** Registering
a patient, raising a bill, taking money and discharging somebody are what the
hospital did. Resend being down, slow, mis-keyed or unreachable must leave all
four exactly as they were — so most of this file is the same assertion made
four times over four different failures, because that is the assertion that
matters.

The rest holds the things that would quietly rot: that the recipient comes from
configuration and never from a literal in the source, that one action sends one
email however many times the button is pressed, and that with nothing
configured nothing is sent and nothing breaks.

`CELERY_TASK_ALWAYS_EAGER` is not used. `dispatch_admin_email` queues on
commit, and these tests assert against `deliver` and the Resend call itself,
which is the boundary that actually matters — whether a worker or the request
thread runs it is not what any of this is about.
"""
from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

import httpx
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge, Payment
from apps.billing.services import add_charge, record_payment
from apps.core import email as email_service
from apps.patients.models import Patient

# Configuration, the way a deployment supplies it. Nothing in `apps/` may
# contain an address or a key — `NoHardCodedRecipients` below proves it.
CONFIGURED = dict(
    RESEND_API_KEY="re_test_key",
    HMIS_EMAIL_FROM="hmis@example-hospital.test",
    HMIS_ADMIN_EMAIL="administrator@example-hospital.test",
    HMIS_BASE_URL="https://hmis.example-hospital.test",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                        "LOCATION": "admin-email-tests"}},
)


class EmailTestCase(TestCase):
    """
    Base for everything below.

    `dispatch_admin_email` queues on `transaction.on_commit`, and a `TestCase`
    wraps each test in a transaction it never commits — so without
    `captureOnCommitCallbacks(execute=True)` nothing is ever sent and every
    assertion here would pass for the wrong reason. `sending()` is that,
    combined with the Resend mock, so a test says what it means.
    """

    @contextmanager
    def sending(self, **mock_kwargs):
        with mock.patch.object(email_service, "send_via_resend", **mock_kwargs) as sent:
            with self.captureOnCommitCallbacks(execute=True):
                yield sent



@override_settings(**CONFIGURED)
class EmailIsSentOnEachEvent(EmailTestCase):
    """A, B, C and D — each event reaches the configured administrator."""

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.api.force_authenticate(self.reception)

    def tearDown(self):
        cache.clear()

    def register(self):
        return self.api.post("/api/patients/", {
            "first_name": "Ada", "last_name": "Okoro", "sex": "F", "phone": "08030000000",
        }, format="json")

    def test_registering_a_patient_emails_the_administrator(self):
        with self.sending() as sent:
            response = self.register()

        self.assertEqual(response.status_code, 201)
        sent.assert_called_once()
        call = sent.call_args.kwargs
        self.assertEqual(call["subject"], "New Patient Registered — NMHS")
        self.assertEqual(call["to"], "administrator@example-hospital.test")
        self.assertEqual(call["from_address"], "hmis@example-hospital.test")
        # The patient's identity is on it, and the chart link uses the UUID.
        # "Last, First" — `patientLabel`/`Patient.__str__`'s own format.
        self.assertIn("Okoro, Ada", call["html"])
        self.assertIn(response.data["patient_number"], call["html"])
        self.assertIn(str(response.data["uuid"]), call["html"])

    def test_billing_a_patient_emails_the_administrator(self):
        patient = Patient.objects.create(first_name="Ada", last_name="Okoro", sex="F",
                                         created_by=self.reception)
        with self.sending() as sent:
            charge = add_charge(patient=patient, description="Consultation fee",
                                amount=Decimal("5000"), created_by=self.cashier,
                                source_type="consultation")

        subjects = [c.kwargs["subject"] for c in sent.call_args_list]
        self.assertIn("Patient Billing Notification — NMHS", subjects)
        html = sent.call_args.kwargs["html"]
        self.assertIn("Consultation fee", html)
        self.assertIn(f"CHG-{charge.pk:06d}", html)
        self.assertIn("5,000.00", html)

    def test_a_payment_emails_the_administrator(self):
        patient = Patient.objects.create(first_name="Ada", last_name="Okoro", sex="F",
                                         created_by=self.reception)
        add_charge(patient=patient, description="Consultation fee", amount=Decimal("5000"),
                   created_by=self.cashier, source_type="consultation")

        with self.sending() as sent:
            payment = record_payment(patient=patient, amount=Decimal("2000"),
                                     received_by=self.cashier, method="cash")

        subjects = [c.kwargs["subject"] for c in sent.call_args_list]
        self.assertIn("Payment Received — NMHS", subjects)
        html = sent.call_args.kwargs["html"]
        self.assertIn(f"PAY-{payment.pk:06d}", html)
        self.assertIn("2,000.00", html)
        self.assertIn("Cash", html)

    def test_the_email_carries_the_hospital_identity_not_a_hard_coded_name(self):
        from apps.core.models import HospitalSettings

        settings_row = HospitalSettings.load()
        settings_row.name = "TESTHOSP"
        settings_row.save(update_fields=["name"])

        with self.sending() as sent:
            self.register()

        self.assertIn("TESTHOSP", sent.call_args.kwargs["html"])


@override_settings(**CONFIGURED)
class EmailFailureNeverFailsTheTransaction(EmailTestCase):
    """The rule. Four ways Resend can fail, four transactions that stand."""

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.api.force_authenticate(self.reception)

    def tearDown(self):
        cache.clear()

    FAILURES = {
        "timeout": httpx.ReadTimeout("Resend timed out"),
        "connection refused": httpx.ConnectError("could not reach api.resend.com"),
        "provider error": httpx.HTTPStatusError(
            "500", request=mock.Mock(), response=mock.Mock(status_code=500)),
        "unexpected": RuntimeError("the client library exploded"),
    }

    def test_registration_stands_through_every_email_failure(self):
        for name, failure in self.FAILURES.items():
            with self.subTest(failure=name):
                cache.clear()
                with self.sending(side_effect=failure):
                    response = self.api.post("/api/patients/", {
                        "first_name": "Ada", "last_name": name.replace(" ", "-").title(),
                        "sex": "F",
                    }, format="json")

                self.assertEqual(response.status_code, 201)
                self.assertTrue(
                    Patient.objects.filter(pk=response.data["id"]).exists(),
                    "the patient must remain registered when the email fails")

    def test_billing_stands_when_resend_times_out(self):
        patient = Patient.objects.create(first_name="Ada", last_name="Okoro", sex="F",
                                         created_by=self.reception)
        with self.sending(side_effect=httpx.ReadTimeout("timed out")):
            charge = add_charge(patient=patient, description="Consultation fee",
                                amount=Decimal("5000"), created_by=self.cashier,
                                source_type="consultation")

        charge.refresh_from_db()
        self.assertEqual(Charge.objects.filter(pk=charge.pk).count(), 1)
        self.assertEqual(charge.amount, Decimal("5000"))

    def test_a_payment_stands_when_resend_errors(self):
        patient = Patient.objects.create(first_name="Ada", last_name="Okoro", sex="F",
                                         created_by=self.reception)
        add_charge(patient=patient, description="Consultation fee", amount=Decimal("5000"),
                   created_by=self.cashier, source_type="consultation")

        with self.sending(side_effect=RuntimeError("resend is down")):
            payment = record_payment(patient=patient, amount=Decimal("5000"),
                                     received_by=self.cashier, method="cash")

        # The money landed and the ledger closed, whatever the email did.
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())
        charge = Charge.objects.get(patient=patient)
        self.assertEqual(charge.status, "paid")
        self.assertEqual(charge.amount_paid, Decimal("5000"))

    def test_a_broken_template_cannot_raise_into_a_transaction(self):
        with mock.patch.object(email_service, "render",
                               side_effect=RuntimeError("template is broken")):
          with self.captureOnCommitCallbacks(execute=True):
            response = self.api.post("/api/patients/", {
                "first_name": "Ada", "last_name": "Template", "sex": "F",
            }, format="json")
        self.assertEqual(response.status_code, 201)

    def test_a_dead_celery_broker_falls_back_to_sending_inline(self):
        """No worker and no Redis is a slower request, never a lost record."""
        from apps.core import tasks

        with mock.patch.object(tasks.send_admin_email_task, "delay",
                               side_effect=OSError("redis is down")):
            with self.sending() as sent:
                response = self.api.post("/api/patients/", {
                    "first_name": "Ada", "last_name": "Broker", "sex": "F",
                }, format="json")

        self.assertEqual(response.status_code, 201)
        sent.assert_called_once()

    def test_a_rolled_back_transaction_sends_nothing(self):
        """Queued on commit: nobody is told about a registration that failed."""
        from django.db import transaction

        with mock.patch.object(email_service, "send_via_resend") as sent:
            try:
                with transaction.atomic():
                    Patient.objects.create(first_name="Ada", last_name="Rollback", sex="F",
                                           created_by=self.reception)
                    email_service.dispatch_admin_email(
                        event="patient.registered", reference="never",
                        subject="New Patient Registered — NMHS",
                        template="patient_registered", context={"title": "x", "rows": []})
                    raise RuntimeError("something went wrong after the email was queued")
            except RuntimeError:
                pass

        sent.assert_not_called()


@override_settings(**CONFIGURED)
class OneActionSendsOneEmail(EmailTestCase):
    """Rule 10 — a retried request must not re-notify."""

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")

    def tearDown(self):
        cache.clear()

    def test_the_same_event_is_only_emailed_once(self):
        patient = Patient.objects.create(first_name="Ada", last_name="Okoro", sex="F",
                                         created_by=self.reception)
        charge = add_charge(patient=patient, description="Consultation fee",
                            amount=Decimal("5000"), created_by=self.cashier,
                            source_type="consultation", notify=False)

        from apps.core import notifications_email as events

        with self.sending() as sent:
            for _ in range(4):        # a double-click, a retry, a refresh
                events.patient_billed(charge=charge, patient=patient, actor=self.cashier)

        self.assertEqual(sent.call_count, 1)

    def test_a_failed_send_may_be_retried(self):
        """The claim is released on failure, so a Celery retry after a timeout
        can still deliver — de-duplication must not swallow the notification."""
        with mock.patch.object(email_service, "send_via_resend",
                               side_effect=httpx.ReadTimeout("t")):
            first = email_service.deliver(subject="s", html="<p>x</p>", dedupe_key="evt:2")
        self.assertFalse(first)

        with mock.patch.object(email_service, "send_via_resend") as sent:
            second = email_service.deliver(subject="s", html="<p>x</p>", dedupe_key="evt:2")
        self.assertTrue(second)
        sent.assert_called_once()


class NoHardCodedRecipients(TestCase):
    """The configuration rule, asserted against the source itself."""

    def test_the_recipient_comes_from_settings(self):
        with override_settings(HMIS_ADMIN_EMAIL="someone@elsewhere.test"):
            self.assertEqual(email_service.admin_recipient(), "someone@elsewhere.test")
        with override_settings(HMIS_ADMIN_EMAIL=""):
            self.assertEqual(email_service.admin_recipient(), "")

    def test_no_email_address_or_api_key_is_written_into_the_source(self):
        """A literal address or key in `apps/` would survive a redeployment and
        outlive whoever it pointed at."""
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[3] / "apps"
        address = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
        key = re.compile(r"re_[A-Za-z0-9]{12,}")
        offenders = []
        for path in root.rglob("*.py"):
            if "/tests/" in str(path) or path.name.startswith("test_"):
                continue        # fixtures may name example.test addresses
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in address.findall(text) + key.findall(text):
                if match.endswith((".test", ".example")) or "noreply@anthropic" in match:
                    continue
                offenders.append(f"{path.relative_to(root)}: {match}")
        self.assertEqual(offenders, [], "\n".join(offenders))


class WithNothingConfigured(EmailTestCase):
    """A developer machine: nothing is sent, nothing breaks, and it says so."""

    @override_settings(RESEND_API_KEY="", HMIS_EMAIL_FROM="", HMIS_ADMIN_EMAIL="")
    def test_nothing_is_sent_and_no_transaction_is_harmed(self):
        api = APIClient()
        reception = User.objects.create_user(username="rec3", password="t", role="reception")
        api.force_authenticate(reception)

        with mock.patch.object(email_service, "send_via_resend") as sent:
            with self.assertLogs("hmis.email", level="INFO"):
              with self.captureOnCommitCallbacks(execute=True):
                response = api.post("/api/patients/", {
                    "first_name": "Ada", "last_name": "Unconfigured", "sex": "F",
                }, format="json")

        self.assertEqual(response.status_code, 201)
        sent.assert_not_called()
        self.assertFalse(email_service.is_configured())
