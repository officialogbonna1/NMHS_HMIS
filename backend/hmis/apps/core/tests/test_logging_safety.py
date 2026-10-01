"""
What reaches a log, and what never does.

Each test runs the real pipeline — the application's loggers through a handler
carrying the same `RedactingFilter` and `RequestIdFilter` the LOGGING setting
installs — and reads the text an administrator would read.
"""
import io
import logging
from unittest import mock

from django.conf import settings
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.logging import REDACTED, RedactingFilter, RequestIdFilter, redact
from apps.core.models import AuditLog

PASSWORD = "S3cret-Passw0rd!"
CLINICAL = "Diagnosis: severe pre-eclampsia, BP 180/120"
PHONE = "08031234567"


class Capture:
    """Attach the production filters and formatter to some loggers, read back."""

    def __init__(self, *names):
        self.names = names
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.addFilter(RedactingFilter())
        self.handler.addFilter(RequestIdFilter())
        self.handler.setFormatter(logging.Formatter("%(levelname)s %(name)s [%(request_id)s] "
                                                    "%(message)s"))
        self.saved = {}

    def __enter__(self):
        for name in self.names:
            logger = logging.getLogger(name)
            self.saved[name] = logger.level
            logger.addHandler(self.handler)
            logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        for name in self.names:
            logger = logging.getLogger(name)
            logger.removeHandler(self.handler)
            logger.setLevel(self.saved[name])

    @property
    def text(self):
        return self.stream.getvalue()


class TheRedactor(SimpleTestCase):
    def test_credential_shapes(self):
        samples = {
            "Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b": "9944b091",
            "authorization=Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig": "eyJhbGci",
            '{"username": "ada", "password": "' + PASSWORD + '"}': PASSWORD,
            "password=" + PASSWORD + "&next=/": PASSWORD,
            "postgresql://hmis:pg-pass-123@db:5432/hmis": "pg-pass-123",
            "redis://:redis-pass-456@127.0.0.1:6379/0": "redis-pass-456",
            "Cookie: sessionid=abcd1234efgh5678; csrftoken=zzzz9999yyyy8888": "abcd1234efgh",
            "key re_ABCDEFGHIJKLMNOPQRSTUV was rejected": "re_ABCDEFGHIJ",
        }
        for text, secret in samples.items():
            with self.subTest(text=text[:30]):
                cleaned = redact(text)
                self.assertNotIn(secret, cleaned)
                self.assertIn(REDACTED, cleaned)

    def test_configured_secret_values_anywhere_in_a_line(self):
        with override_settings(SECRET_KEY="the-real-signing-key-0123456789"), \
                mock.patch.dict(settings.DATABASES["default"], {"PASSWORD": "db-pass-value-xyz"}), \
                override_settings(CELERY_BROKER_URL="redis://:broker-pass-777@r:6379/0"):
            line = redact("boom the-real-signing-key-0123456789 / db-pass-value-xyz / "
                          "broker-pass-777")
        for secret in ("the-real-signing-key", "db-pass-value-xyz", "broker-pass-777"):
            self.assertNotIn(secret, line)

    def test_the_traceback_is_scrubbed_too(self):
        with Capture("hmis.api") as cap:
            try:
                raise RuntimeError("connect failed password=" + PASSWORD)
            except RuntimeError:
                logging.getLogger("hmis.api").exception("Unhandled")
        self.assertIn("RuntimeError", cap.text)
        self.assertNotIn(PASSWORD, cap.text)

    def test_ordinary_words_survive(self):
        self.assertEqual(redact("Token refresh succeeded for user=12"),
                         "Token refresh succeeded for user=12")


class Boom(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        request.data  # the body was read; it must still not be logged
        raise RuntimeError("database said password=" + PASSWORD)

    def get(self, request):
        return Response({"ok": True})


class Clash(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        raise IntegrityError('duplicate key value violates unique constraint "patient_phone"\n'
                             f"DETAIL:  Key (phone_number)=({PHONE}) already exists.")


urlpatterns = [path("boom/", Boom.as_view()), path("clash/", Clash.as_view())]


@override_settings(ROOT_URLCONF=__name__, REQUEST_LOGGING=True)
class TheApiLogsSafely(SimpleTestCase):
    def test_a_bug_answers_a_reference_and_logs_no_body_or_secret(self):
        with Capture("hmis.api", "hmis.request") as cap:
            response = self.client.post("/boom/?search=Adaeze+" + PHONE,
                                        {"notes": CLINICAL, "password": PASSWORD},
                                        content_type="application/json",
                                        HTTP_AUTHORIZATION="Token 9944b09199c62bcf9418ad84")
        body = response.json()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(set(body), {"detail", "code", "reference"})
        self.assertNotIn("RuntimeError", str(body))
        self.assertNotIn(PASSWORD, str(body))
        # The administrator can find it: the reference and the request ID are logged.
        self.assertIn(body["reference"], cap.text)
        self.assertIn(response["X-Request-ID"], cap.text)
        for leaked in (PASSWORD, CLINICAL, PHONE, "Adaeze", "9944b09199c62bcf"):
            self.assertNotIn(leaked, cap.text)

    def test_the_request_line_has_no_query_string_or_headers(self):
        with Capture("hmis.request") as cap:
            self.client.get("/boom/?search=Adaeze+" + PHONE,
                            HTTP_AUTHORIZATION="Token 9944b09199c62bcf9418ad84",
                            HTTP_COOKIE="sessionid=abcd1234efgh5678")
        self.assertIn("GET /boom/ 200", cap.text)
        for leaked in ("search", PHONE, "Adaeze", "9944b091", "abcd1234efgh"):
            self.assertNotIn(leaked, cap.text)

    def test_a_conflict_logs_the_rule_but_not_the_values(self):
        with Capture("hmis.api") as cap:
            response = self.client.post("/clash/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("patient_phone", cap.text)
        self.assertNotIn(PHONE, cap.text)
        self.assertNotIn(PHONE, response.content.decode())


class SignInAndOut(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada.nurse", password=PASSWORD,
                                             role="nurse")

    def test_a_failure_logs_neither_the_password_nor_what_was_typed_as_the_username(self):
        with Capture("hmis.security") as cap:
            self.client.post("/api/auth/login/", {"username": PASSWORD + "x", "password": "nope"})
            self.client.post("/api/auth/login/", {"username": "ada.nurse", "password": "wrong-pw-1"})
        self.assertIn("Sign-in failed", cap.text)
        for leaked in (PASSWORD, "wrong-pw-1", "nope", "ada.nurse"):
            self.assertNotIn(leaked, cap.text)

    def test_a_success_is_audited_and_logged_without_the_token(self):
        with Capture("hmis.security") as cap:
            response = self.client.post("/api/auth/login/",
                                        {"username": "ada.nurse", "password": PASSWORD})
        token = response.json()["token"]
        self.assertIn(f"Sign-in: user={self.user.pk}", cap.text)
        self.assertNotIn(token, cap.text)
        self.assertNotIn(PASSWORD, cap.text)
        row = AuditLog.objects.get(action="auth.login")
        self.assertEqual(row.actor, self.user)
        self.assertNotIn(token, str(row.details))

        with Capture("hmis.security") as cap:
            self.client.post("/api/auth/logout/", HTTP_AUTHORIZATION=f"Token {token}")
        self.assertIn(f"Sign-out: user={self.user.pk}", cap.text)
        self.assertNotIn(token, cap.text)
        self.assertTrue(AuditLog.objects.filter(action="auth.logout", actor=self.user).exists())

    def test_a_refusal_is_a_security_line_without_the_record(self):
        from rest_framework.authtoken.models import Token

        token = Token.objects.create(user=self.user)
        with Capture("hmis.security") as cap:
            response = self.client.get("/api/finance/report/",
                                       HTTP_AUTHORIZATION=f"Token {token.key}")
        self.assertEqual(response.status_code, 403)
        self.assertIn(f"Permission denied: user={self.user.pk} role=nurse GET /api/finance/report/",
                      cap.text)
        self.assertNotIn(token.key, cap.text)


class CeleryCarriesTheRequestId(SimpleTestCase):
    def test_the_id_travels_in_a_header_and_is_adopted_by_the_task(self):
        from apps.core.logging import request_id_var
        from hmis import celery as celery_module

        headers = {}
        token = request_id_var.set("req-abc123456789")
        try:
            celery_module._carry_request_id(headers=headers)
        finally:
            request_id_var.reset(token)
        self.assertEqual(headers, {"hmis_request_id": "req-abc123456789"})

        task = mock.Mock()
        task.request = mock.Mock(hmis_request_id="req-abc123456789")
        celery_module._adopt_request_id(task_id="t1", task=task)
        self.assertEqual(request_id_var.get(), "req-abc123456789")
        celery_module._drop_request_id(task_id="t1")
        self.assertEqual(request_id_var.get(), "-")

        beat = mock.Mock()
        beat.request = mock.Mock(hmis_request_id=None, headers=None)
        celery_module._adopt_request_id(task_id="t2", task=beat)
        self.assertEqual(request_id_var.get(), "task-t2")
        celery_module._drop_request_id(task_id="t2")
