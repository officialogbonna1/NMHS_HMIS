"""
Development is SQLite; production is PostgreSQL and nothing else.

The rules live in `hmis/environment.py` as plain functions, so most of this is
tested with a dictionary standing in for the environment. Two tests import the
real settings module in a fresh interpreter, because "production refuses to
start" is a property of the import itself. None of it needs a PostgreSQL
server: building the configuration never connects.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core import checks
from hmis import environment as envconf

BASE_DIR = Path(settings.BASE_DIR)
GOOD_KEY = "k" * 20 + "Qz9#Lm2$" * 5           # 60 chars, not a placeholder
DB = {"DB_NAME": "hmis", "DB_USER": "hmis", "DB_PASSWORD": "pg-secret-value",
      "DB_HOST": "127.0.0.1"}
PRODUCTION = {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": GOOD_KEY,
              "DJANGO_ALLOWED_HOSTS": "hmis.example.org",
              "DJANGO_CSRF_TRUSTED_ORIGINS": "https://hmis.example.org", **DB}


class WhichEnvironment(SimpleTestCase):
    def test_development_is_the_default(self):
        self.assertEqual(envconf.environment({}), "development")
        self.assertEqual(envconf.environment({"DJANGO_ENV": "Production "}), "production")

    def test_an_unknown_name_is_refused_rather_than_guessed(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "DJANGO_ENV"):
            envconf.environment({"DJANGO_ENV": "prod"})

    def test_debug_off_does_not_make_it_production(self):
        self.assertEqual(envconf.environment({"DJANGO_DEBUG": "False"}), "development")


class TheDatabase(SimpleTestCase):
    def test_development_is_the_existing_sqlite_file(self):
        config = envconf.database_config({}, BASE_DIR, "development")
        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(config["NAME"], BASE_DIR / "db.sqlite3")

    def test_development_ignores_postgres_variables(self):
        config = envconf.database_config(DB, BASE_DIR, "development")
        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")

    def test_production_is_postgresql_from_the_environment(self):
        config = envconf.database_config({**DB, "DB_PORT": "6432"}, BASE_DIR, "production")
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual((config["NAME"], config["USER"], config["PASSWORD"], config["HOST"],
                          config["PORT"]), ("hmis", "hmis", "pg-secret-value", "127.0.0.1", "6432"))
        self.assertEqual(envconf.database_config(DB, BASE_DIR, "production")["PORT"], "5432")

    def test_production_without_postgres_fails_and_never_falls_back(self):
        for absent in envconf.REQUIRED_DB_VARS:
            env = {k: v for k, v in DB.items() if k != absent}
            with self.subTest(absent=absent):
                with self.assertRaises(ImproperlyConfigured) as caught:
                    envconf.database_config(env, BASE_DIR, "production")
                message = str(caught.exception)
                self.assertIn(absent, message)
                self.assertIn("no fallback to SQLite", message)
                # The refusal names variables, never the values that were set.
                self.assertNotIn("pg-secret-value", message)
        with self.assertRaises(ImproperlyConfigured):
            envconf.database_config({}, BASE_DIR, "production")

    def test_the_running_test_suite_is_on_development_sqlite(self):
        self.assertEqual(settings.HMIS_ENV, "development")
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3")


class TheOtherProductionRequirements(SimpleTestCase):
    def test_secret_key(self):
        self.assertEqual(envconf.secret_key({}, "development"), envconf.DEV_SECRET_KEY)
        for bad in ("", "change-me", envconf.DEV_SECRET_KEY, "short-but-set"):
            with self.subTest(bad=bad), self.assertRaises(ImproperlyConfigured):
                envconf.secret_key({"DJANGO_SECRET_KEY": bad}, "production")
        self.assertEqual(envconf.secret_key({"DJANGO_SECRET_KEY": GOOD_KEY}, "production"),
                         GOOD_KEY)

    def test_debug(self):
        self.assertTrue(envconf.debug({}, "development"))
        self.assertFalse(envconf.debug({"DJANGO_DEBUG": "False"}, "development"))
        self.assertFalse(envconf.debug({}, "production"))
        with self.assertRaises(ImproperlyConfigured):
            envconf.debug({"DJANGO_DEBUG": "true"}, "production")

    def test_hosts_and_origins(self):
        self.assertEqual(envconf.hosts({}, "development"), ["localhost", "127.0.0.1"])
        for env in ({}, {"DJANGO_ALLOWED_HOSTS": "*"}):
            with self.assertRaises(ImproperlyConfigured):
                envconf.hosts(env, "production")
        self.assertIn("http://localhost:5173", envconf.csrf_origins({}, "development"))
        for env in ({}, {"DJANGO_CSRF_TRUSTED_ORIGINS": "http://hmis.example.org"}):
            with self.assertRaises(ImproperlyConfigured):
                envconf.csrf_origins(env, "production")


def _import_settings(env):
    """Import hmis.settings in a clean interpreter and report what it built."""
    script = (
        "import json, os; os.environ['DJANGO_SETTINGS_MODULE']='hmis.settings'\n"
        "from django.conf import settings as s\n"
        "print(json.dumps({'engine': s.DATABASES['default']['ENGINE'], 'debug': s.DEBUG,"
        " 'ssl_redirect': getattr(s, 'SECURE_SSL_REDIRECT', False),"
        " 'cookie_secure': s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE,"
        " 'hsts': s.SECURE_HSTS_SECONDS, 'proxy': bool(getattr(s, 'SECURE_PROXY_SSL_HEADER', None)),"
        " 'sql_level': s.LOGGING['loggers']['django.db.backends']['level'],"
        " 'validators': len(s.AUTH_PASSWORD_VALIDATORS)}))\n"
    )
    clean = {"PATH": os.environ.get("PATH", ""), **env}
    return subprocess.run([sys.executable, "-c", script], cwd=BASE_DIR, env=clean,
                          capture_output=True, text=True, timeout=120)


class TheRealSettingsModule(SimpleTestCase):
    def test_production_without_database_variables_refuses_to_start(self):
        env = {k: v for k, v in PRODUCTION.items() if not k.startswith("DB_")}
        result = _import_settings(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ImproperlyConfigured", result.stderr)
        self.assertIn("no fallback to SQLite", result.stderr)

    def test_production_builds_postgresql_and_secure_settings(self):
        result = _import_settings({**PRODUCTION, "DJANGO_SQL_DEBUG": "true"})
        self.assertEqual(result.returncode, 0, result.stderr)
        built = json.loads(result.stdout)
        self.assertEqual(built["engine"], "django.db.backends.postgresql")
        self.assertFalse(built["debug"])
        self.assertTrue(built["ssl_redirect"] and built["cookie_secure"] and built["proxy"])
        self.assertGreater(built["hsts"], 0)
        # SQL (and its parameters) is never logged in production, even if asked.
        self.assertEqual(built["sql_level"], "WARNING")
        self.assertGreater(built["validators"], 0)

    def test_development_needs_nothing_and_is_sqlite(self):
        result = _import_settings({})
        self.assertEqual(result.returncode, 0, result.stderr)
        built = json.loads(result.stdout)
        self.assertEqual(built["engine"], "django.db.backends.sqlite3")
        self.assertTrue(built["debug"])
        self.assertEqual(built["validators"], 0)


SECURE = dict(IS_PRODUCTION=True, DEBUG=False, SECURE_SSL_REDIRECT=True,
              SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True, SECURE_HSTS_SECONDS=3600,
              SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
              ALLOWED_HOSTS=["hmis.example.org"],
              CSRF_TRUSTED_ORIGINS=["https://hmis.example.org"], SQL_DEBUG=False)


class CheckDeployFails(SimpleTestCase):
    """`check --deploy` errors (not warnings) until production is production."""

    def ids(self):
        with mock.patch.dict(settings.DATABASES["default"],
                             {"ENGINE": "django.db.backends.postgresql"}), \
                mock.patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:6379/0"}):
            return {p.id for p in checks.production_configuration(None)}

    def test_development_fails_check_deploy(self):
        self.assertEqual({p.id for p in checks.production_configuration(None)}, {"hmis.E001"})

    def test_a_complete_production_configuration_passes(self):
        with override_settings(**SECURE):
            self.assertEqual(self.ids(), set())

    def test_each_insecure_switch_is_an_error(self):
        cases = {"hmis.E002": {"DEBUG": True}, "hmis.E004": {"SECURE_SSL_REDIRECT": False},
                 "hmis.E005": {"SESSION_COOKIE_SECURE": False},
                 "hmis.E006": {"SECURE_HSTS_SECONDS": 0},
                 "hmis.E007": {"SECURE_PROXY_SSL_HEADER": None},
                 "hmis.E008": {"ALLOWED_HOSTS": ["*"]},
                 "hmis.E009": {"CSRF_TRUSTED_ORIGINS": ["http://hmis.example.org"]},
                 "hmis.E010": {"SQL_DEBUG": True}}
        for expected, change in cases.items():
            with self.subTest(expected), override_settings(**{**SECURE, **change}):
                self.assertIn(expected, self.ids())


class TheHealthCheck(TestCase):
    def test_healthy(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "healthy"})
        self.assertIn("X-Request-ID", response)

    def test_unhealthy_says_nothing_else(self):
        failure = OperationalError('could not connect: password=pg-secret-value host="db.internal"')
        with mock.patch("apps.core.health.connection.cursor", side_effect=failure), \
                self.assertLogs("hmis.health", "ERROR") as logs:
            response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unhealthy"})
        joined = " ".join(logs.output)
        self.assertNotIn("pg-secret-value", joined)
        self.assertNotIn("db.internal", joined)

    def test_only_get(self):
        self.assertEqual(self.client.post("/healthz/").status_code, 405)


class TheRequestId(TestCase):
    def test_a_good_incoming_id_is_kept_and_a_bad_one_replaced(self):
        kept = self.client.get("/healthz/", HTTP_X_REQUEST_ID="nginx-req-0123456789")
        self.assertEqual(kept["X-Request-ID"], "nginx-req-0123456789")
        replaced = self.client.get("/healthz/", HTTP_X_REQUEST_ID="<script>alert(1)</script>")
        self.assertRegex(replaced["X-Request-ID"], r"^[0-9a-f]{32}$")
