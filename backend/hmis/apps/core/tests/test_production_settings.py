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
CLOUD = {"DJANGO_MEDIA_STORAGE": "cloudinary", "CLOUDINARY_CLOUD_NAME": "nmhs-test",
         "CLOUDINARY_API_KEY": "123456789012345",
         "CLOUDINARY_API_SECRET": "cloudinary-secret-value-123"}
PRODUCTION = {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": GOOD_KEY,
              "DJANGO_ALLOWED_HOSTS": "hmis.example.org",
              "DJANGO_CSRF_TRUSTED_ORIGINS": "https://hmis.example.org", **DB, **CLOUD}


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


# ------------------------------------------------------------------ Render

RENDER_WEB = {"RENDER": "true", "RENDER_SERVICE_TYPE": "web",
              "RENDER_EXTERNAL_HOSTNAME": "hmis-api.onrender.com",
              "RENDER_EXTERNAL_URL": "https://hmis-api.onrender.com"}
DATABASE_URL = "postgresql://hmis_user:url%40secret@dpg-abc123-a:5432/hmis"


class TheDatabaseUrl(SimpleTestCase):
    def test_render_style_url(self):
        config = envconf.database_config({"DATABASE_URL": DATABASE_URL}, BASE_DIR, "production")
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual((config["NAME"], config["USER"], config["PASSWORD"], config["HOST"],
                          config["PORT"]), ("hmis", "hmis_user", "url@secret", "dpg-abc123-a", "5432"))
        self.assertNotIn("OPTIONS", config)

    def test_sslmode_is_carried(self):
        config = envconf.database_config(
            {"DATABASE_URL": DATABASE_URL + "?sslmode=require"}, BASE_DIR, "production")
        self.assertEqual(config["OPTIONS"], {"sslmode": "require"})

    def test_the_url_wins_over_the_separate_variables(self):
        config = envconf.database_config({**DB, "DATABASE_URL": DATABASE_URL}, BASE_DIR,
                                         "production")
        self.assertEqual(config["HOST"], "dpg-abc123-a")

    def test_a_bad_url_is_refused_without_echoing_it(self):
        for url in ("sqlite:///db.sqlite3", "mysql://u:pw-x@h/db", "postgresql://u:pw-x@h:notaport/db",
                    "postgresql://h/"):
            with self.subTest(url=url), self.assertRaises(ImproperlyConfigured) as caught:
                envconf.database_config({"DATABASE_URL": url}, BASE_DIR, "production")
            self.assertNotIn("pw-x", str(caught.exception))

    def test_development_ignores_it(self):
        config = envconf.database_config({"DATABASE_URL": DATABASE_URL}, BASE_DIR, "development")
        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")


class RendersOwnAddresses(SimpleTestCase):
    def test_the_onrender_hostname_and_url_are_added(self):
        env = {**RENDER_WEB}
        self.assertEqual(envconf.hosts(env, "production"), ["hmis-api.onrender.com"])
        self.assertEqual(envconf.csrf_origins(env, "production"), ["https://hmis-api.onrender.com"])
        env["DJANGO_ALLOWED_HOSTS"] = "hmis.example-hospital.org"
        self.assertEqual(envconf.hosts(env, "production"),
                         ["hmis.example-hospital.org", "hmis-api.onrender.com"])

    def test_a_worker_needs_no_hostname_but_a_web_service_still_does(self):
        worker = {"RENDER": "true", "RENDER_SERVICE_TYPE": "worker"}
        self.assertEqual(envconf.hosts(worker, "production"), [])
        self.assertEqual(envconf.csrf_origins(worker, "production"), [])
        for env in ({"RENDER": "true", "RENDER_SERVICE_TYPE": "web"},
                    {"RENDER_SERVICE_TYPE": "worker"}):          # not Render: no relaxation
            with self.subTest(env=env), self.assertRaises(ImproperlyConfigured):
                envconf.hosts(env, "production")

    def test_cors(self):
        self.assertEqual(envconf.cors_origins({}, "production"), [])
        self.assertEqual(envconf.cors_origins(
            {"CORS_ALLOWED_ORIGINS": "https://hmis-web.onrender.com/"}, "production"),
            ["https://hmis-web.onrender.com"])
        with self.assertRaises(ImproperlyConfigured):
            envconf.cors_origins({"CORS_ALLOWED_ORIGINS": "http://hmis-web.onrender.com"},
                                 "production")
        self.assertIn("http://localhost:5173", envconf.cors_origins({}, "development"))


class TheMediaStorage(SimpleTestCase):
    def test_development_is_the_local_directory(self):
        self.assertEqual(envconf.media_storage({}, "development")["BACKEND"],
                         "django.core.files.storage.FileSystemStorage")

    def test_production_must_choose(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "DJANGO_MEDIA_STORAGE"):
            envconf.media_storage({}, "production")

    def test_filesystem_is_refused_on_render_only(self):
        self.assertEqual(envconf.media_storage({"DJANGO_MEDIA_STORAGE": "filesystem"},
                                               "production")["BACKEND"],
                         "django.core.files.storage.FileSystemStorage")
        with self.assertRaisesMessage(ImproperlyConfigured, "Render"):
            envconf.media_storage({"DJANGO_MEDIA_STORAGE": "filesystem", "RENDER": "true"},
                                  "production")

    def test_cloudinary_needs_its_credentials_and_names_them_only(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            envconf.media_storage({"DJANGO_MEDIA_STORAGE": "cloudinary",
                                   "CLOUDINARY_API_SECRET": "cloudinary-secret-value-123"},
                                  "production")
        self.assertIn("CLOUDINARY_CLOUD_NAME", str(caught.exception))
        self.assertNotIn("cloudinary-secret-value-123", str(caught.exception))
        for bad in ({"CLOUDINARY_LINK_EXPIRY_SECONDS": "0"},
                    {"CLOUDINARY_LINK_EXPIRY_SECONDS": "forever"},
                    {"CLOUDINARY_LINK_EXPIRY_SECONDS": "999999"},
                    {"CLOUDINARY_FOLDER": "../../elsewhere?x"}):
            with self.subTest(bad=bad), self.assertRaises(ImproperlyConfigured):
                envconf.media_storage({**CLOUD, **bad}, "production")

    def test_s3_is_no_longer_an_option(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "cloudinary"):
            envconf.media_storage({"DJANGO_MEDIA_STORAGE": "s3"}, "production")

    def test_cloudinary_configuration(self):
        config = envconf.media_storage(CLOUD, "production")
        self.assertEqual(config["BACKEND"], "apps.core.storage.PrivateCloudinaryStorage")
        self.assertEqual(config["OPTIONS"], {
            "cloud_name": "nmhs-test", "api_key": "123456789012345",
            "api_secret": "cloudinary-secret-value-123", "folder": "nmhs-hmis/media",
            "link_seconds": 900})


class ThePrivateCloudinaryStorage(SimpleTestCase):
    """The backend, with Cloudinary's SDK mocked where it would reach the network."""

    def storage(self):
        from apps.core.storage import PrivateCloudinaryStorage

        return PrivateCloudinaryStorage(**envconf.media_storage(CLOUD, "production")["OPTIONS"])

    def test_uploads_are_raw_authenticated_and_never_overwrite(self):
        from django.core.files.base import ContentFile

        with mock.patch("cloudinary.uploader.upload") as upload:
            name = self.storage().save("medical_tests/2026/10/scan.pdf", ContentFile(b"%PDF-1.4"))
        self.assertRegex(name, r"^medical_tests/2026/10/scan_[0-9a-f]{8}\.pdf$")
        options = upload.call_args.kwargs
        self.assertEqual(options["type"], "authenticated")
        self.assertEqual(options["resource_type"], "raw")
        self.assertEqual(options["public_id"], f"nmhs-hmis/media/{name}")
        self.assertFalse(options["overwrite"])

    def test_a_link_is_a_private_download_url_that_expires(self):
        import time
        from urllib.parse import parse_qs, urlsplit

        url = self.storage().url("medical_tests/2026/10/scan_ab12cd34.pdf")   # signed offline
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        self.assertEqual((parts.scheme, parts.netloc), ("https", "api.cloudinary.com"))
        self.assertTrue(parts.path.endswith("/nmhs-test/raw/download"))
        self.assertEqual(query["type"], ["authenticated"])
        self.assertEqual(query["public_id"], ["nmhs-hmis/media/medical_tests/2026/10/scan_ab12cd34.pdf"])
        self.assertAlmostEqual(int(query["expires_at"][0]), int(time.time()) + 900, delta=5)
        self.assertIn("signature", query)
        self.assertNotIn("cloudinary-secret-value-123", url)
        self.assertNotIn("res.cloudinary.com", url)        # never the CDN address

    def test_names_stay_unique_within_the_field_length(self):
        storage = self.storage()
        long = "medical_tests/2026/10/" + "x" * 120 + ".pdf"
        first, second = (storage.get_available_name(long, max_length=100) for _ in range(2))
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 100)
        self.assertTrue(first.endswith(".pdf"))

    def test_delete_and_exists_address_the_authenticated_raw_asset(self):
        from cloudinary.exceptions import NotFound

        storage = self.storage()
        with mock.patch("cloudinary.uploader.destroy") as destroy:
            storage.delete("medical_tests/a.pdf")
        self.assertEqual(destroy.call_args.args[0], "nmhs-hmis/media/medical_tests/a.pdf")
        self.assertEqual((destroy.call_args.kwargs["type"], destroy.call_args.kwargs["resource_type"]),
                         ("authenticated", "raw"))
        with mock.patch("cloudinary.api.resource", side_effect=NotFound("gone")):
            self.assertFalse(storage.exists("medical_tests/a.pdf"))

    def test_the_secret_is_not_in_its_repr(self):
        self.assertNotIn("cloudinary-secret-value-123", repr(self.storage()))


class TheClientAddress(SimpleTestCase):
    def test_render_reads_the_header_cloudflare_overwrites(self):
        from django.test import RequestFactory

        from apps.accounts import lockout

        self.assertEqual(envconf.client_ip_header({"HMIS_CLIENT_IP_HEADER": "CF-Connecting-IP"}),
                         "HTTP_CF_CONNECTING_IP")
        with self.assertRaises(ImproperlyConfigured):
            envconf.client_ip_header({"HMIS_CLIENT_IP_HEADER": "CF Connecting IP;"})
        request = RequestFactory().post("/api/auth/login/", HTTP_X_FORWARDED_FOR="6.6.6.6, 41.58.1.2",
                                        HTTP_CF_CONNECTING_IP="41.58.1.2")
        with override_settings(HMIS_CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP"):
            self.assertEqual(lockout.client_ip(request), "41.58.1.2")
        # Unset: the historical rule, unchanged (first X-Forwarded-For hop).
        with override_settings(HMIS_CLIENT_IP_HEADER=""):
            self.assertEqual(lockout.client_ip(request), "6.6.6.6")


def _render_settings(extra=None):
    script = (
        "import json, os; os.environ['DJANGO_SETTINGS_MODULE']='hmis.settings'\n"
        "from django.conf import settings as s\n"
        "print(json.dumps({'engine': s.DATABASES['default']['ENGINE'],"
        " 'host': s.DATABASES['default']['HOST'], 'hosts': s.ALLOWED_HOSTS,"
        " 'csrf': s.CSRF_TRUSTED_ORIGINS, 'cors': s.CORS_ALLOWED_ORIGINS,"
        " 'whitenoise': 'whitenoise.middleware.WhiteNoiseMiddleware' in s.MIDDLEWARE,"
        " 'static': s.STORAGES['staticfiles']['BACKEND'], 'media': s.STORAGES['default']['BACKEND'],"
        " 'ip_header': s.HMIS_CLIENT_IP_HEADER}))\n"
    )
    env = {"PATH": os.environ.get("PATH", ""), "DJANGO_ENV": "production",
           "DJANGO_SECRET_KEY": GOOD_KEY, "DATABASE_URL": DATABASE_URL,
           "CORS_ALLOWED_ORIGINS": "https://hmis-web.onrender.com",
           "HMIS_CLIENT_IP_HEADER": "CF-Connecting-IP", **CLOUD, **RENDER_WEB, **(extra or {})}
    return subprocess.run([sys.executable, "-c", script], cwd=BASE_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


class TheRenderBlueprintsEnvironment(SimpleTestCase):
    """The variables render.yaml provides are enough, and build what Render needs."""

    def test_the_web_service(self):
        result = _render_settings()
        self.assertEqual(result.returncode, 0, result.stderr)
        built = json.loads(result.stdout)
        self.assertEqual((built["engine"], built["host"]),
                         ("django.db.backends.postgresql", "dpg-abc123-a"))
        self.assertEqual(built["hosts"], ["hmis-api.onrender.com"])
        self.assertEqual(built["csrf"], ["https://hmis-api.onrender.com"])
        self.assertEqual(built["cors"], ["https://hmis-web.onrender.com"])
        self.assertTrue(built["whitenoise"])
        self.assertEqual(built["static"], "whitenoise.storage.CompressedManifestStaticFilesStorage")
        self.assertEqual(built["media"], "apps.core.storage.PrivateCloudinaryStorage")
        self.assertEqual(built["ip_header"], "HTTP_CF_CONNECTING_IP")

    def test_a_celery_worker_starts_without_a_hostname(self):
        worker = {"RENDER_SERVICE_TYPE": "worker", "RENDER_EXTERNAL_HOSTNAME": "",
                  "RENDER_EXTERNAL_URL": ""}
        result = _render_settings(worker)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["hosts"], [])

    def test_without_the_database_it_refuses_to_start(self):
        result = _render_settings({"DATABASE_URL": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no fallback to SQLite", result.stderr)

    def test_collectstatic_builds_the_admin_assets(self):
        import tempfile

        with tempfile.TemporaryDirectory() as target:
            env = {"PATH": os.environ.get("PATH", ""), "DJANGO_ENV": "production",
                   "DJANGO_SECRET_KEY": GOOD_KEY, "DATABASE_URL": DATABASE_URL,
                   "DJANGO_STATIC_ROOT": target, **CLOUD, **RENDER_WEB}
            result = subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"],
                                    cwd=BASE_DIR, env=env, capture_output=True, text=True,
                                    timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertTrue((Path(target) / "staticfiles.json").exists())     # manifest
            self.assertTrue(any(Path(target, "admin", "css").glob("base.*.css")))


class RenderDeployChecks(SimpleTestCase):
    def test_render_needs_the_trusted_ip_header_and_object_storage(self):
        s3 = {"default": {"BACKEND": "apps.core.storage.PrivateCloudinaryStorage"},
              "staticfiles": settings.STORAGES["staticfiles"]}
        with override_settings(**SECURE, HMIS_ON_RENDER=True, HMIS_CLIENT_IP_HEADER="",
                               STORAGES=s3):
            self.assertIn("hmis.E011", CheckDeployFails.ids(self))
        local = {**s3, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
        with override_settings(**SECURE, HMIS_ON_RENDER=True,
                               HMIS_CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP", STORAGES=local):
            self.assertIn("hmis.E012", CheckDeployFails.ids(self))
        with override_settings(**SECURE, HMIS_ON_RENDER=True,
                               HMIS_CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP", STORAGES=s3,
                               LOG_DIR="/var/log/x"):
            problems = {p.id for p in __import__("apps.core.checks", fromlist=["x"])
                        .production_configuration(None)}
            self.assertIn("hmis.W002", problems)


class TheAdminNetworkRestriction(TestCase):
    def test_off_by_default(self):
        self.assertEqual(envconf.admin_networks({}), ())
        self.assertNotEqual(self.client.get("/admin/login/").status_code, 404)

    def test_invalid_range_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            envconf.admin_networks({"HMIS_ADMIN_ALLOWED_NETWORKS": "not-a-network"})

    def test_only_listed_networks_reach_django_admin_and_xff_cannot_forge_it(self):
        networks = envconf.admin_networks({"HMIS_ADMIN_ALLOWED_NETWORKS": "41.58.0.0/16"})
        with override_settings(HMIS_ADMIN_ALLOWED_NETWORKS=networks,
                               HMIS_CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP"):
            inside = self.client.get("/admin/login/", HTTP_CF_CONNECTING_IP="41.58.9.9")
            self.assertEqual(inside.status_code, 200)
            with self.assertLogs("hmis.security", "WARNING"):
                forged = self.client.get("/admin/login/", HTTP_CF_CONNECTING_IP="6.6.6.6",
                                         HTTP_X_FORWARDED_FOR="41.58.9.9")
            self.assertEqual(forged.status_code, 404)
            # The API and the health check are untouched by it.
            self.assertEqual(self.client.get("/healthz/",
                                             HTTP_CF_CONNECTING_IP="6.6.6.6").status_code, 200)


# ------------------------------------------------------------------ Railway

RAILWAY_API = {"RAILWAY_PROJECT_ID": "proj-123", "RAILWAY_ENVIRONMENT_ID": "env-123",
               "RAILWAY_PUBLIC_DOMAIN": "hmis-api-production.up.railway.app"}
RAILWAY_WORKER = {"RAILWAY_PROJECT_ID": "proj-123", "RAILWAY_ENVIRONMENT_ID": "env-123"}
RAILWAY_DATABASE_URL = "postgresql://postgres:pg-railway-pass@postgres.railway.internal:5432/railway"


class RailwaysOwnAddresses(SimpleTestCase):
    def test_the_public_domain_and_the_healthcheck_host_are_allowed(self):
        self.assertEqual(envconf.hosts(RAILWAY_API, "production"),
                         ["hmis-api-production.up.railway.app", "healthcheck.railway.app"])
        self.assertEqual(envconf.csrf_origins(RAILWAY_API, "production"),
                         ["https://hmis-api-production.up.railway.app"])
        custom = {**RAILWAY_API, "DJANGO_ALLOWED_HOSTS": "api.example-hospital.org"}
        self.assertEqual(envconf.hosts(custom, "production")[0], "api.example-hospital.org")

    def test_a_service_without_a_public_domain_is_a_celery_process(self):
        self.assertFalse(envconf.serves_http(RAILWAY_WORKER))
        self.assertEqual(envconf.hosts(RAILWAY_WORKER, "production"), [])
        self.assertEqual(envconf.csrf_origins(RAILWAY_WORKER, "production"), [])
        self.assertTrue(envconf.serves_http(RAILWAY_API))

    def test_railway_variables_mean_nothing_off_railway(self):
        env = {"RAILWAY_PUBLIC_DOMAIN": "x.up.railway.app"}      # no project/environment id
        self.assertFalse(envconf.on_railway(env))
        with self.assertRaises(ImproperlyConfigured):
            envconf.hosts(env, "production")

    def test_filesystem_media_is_refused_on_railway(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "Railway"):
            envconf.media_storage({"DJANGO_MEDIA_STORAGE": "filesystem", **RAILWAY_API},
                                  "production")
        self.assertEqual(envconf.media_storage({**CLOUD, **RAILWAY_API}, "production")["BACKEND"],
                         "apps.core.storage.PrivateCloudinaryStorage")

    def test_railways_database_url(self):
        config = envconf.database_config({"DATABASE_URL": RAILWAY_DATABASE_URL}, BASE_DIR,
                                         "production")
        self.assertEqual((config["HOST"], config["NAME"], config["USER"]),
                         ("postgres.railway.internal", "railway", "postgres"))


def _railway_settings(extra):
    script = (
        "import json, os; os.environ['DJANGO_SETTINGS_MODULE']='hmis.settings'\n"
        "from django.conf import settings as s\n"
        "print(json.dumps({'engine': s.DATABASES['default']['HOST'],"
        " 'hosts': s.ALLOWED_HOSTS, 'csrf': s.CSRF_TRUSTED_ORIGINS,"
        " 'railway': s.HMIS_ON_RAILWAY, 'render': s.HMIS_ON_RENDER,"
        " 'media': s.STORAGES['default']['BACKEND'], 'ip_header': s.HMIS_CLIENT_IP_HEADER}))\n"
    )
    env = {"PATH": os.environ.get("PATH", ""), "DJANGO_ENV": "production",
           "DJANGO_SECRET_KEY": GOOD_KEY, "DATABASE_URL": RAILWAY_DATABASE_URL,
           "REDIS_URL": "redis://default:redis-pass@redis.railway.internal:6379",
           "HMIS_CLIENT_IP_HEADER": "X-Real-IP", **CLOUD, **extra}
    return subprocess.run([sys.executable, "-c", script], cwd=BASE_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


class TheRailwayEnvironment(SimpleTestCase):
    """The variables docs/RAILWAY.md lists are enough for the API, worker and Beat."""

    def test_the_api_service(self):
        result = _railway_settings({**RAILWAY_API,
                                    "CORS_ALLOWED_ORIGINS": "https://hmis-web.up.railway.app"})
        self.assertEqual(result.returncode, 0, result.stderr)
        built = json.loads(result.stdout)
        self.assertEqual(built["engine"], "postgres.railway.internal")
        self.assertIn("healthcheck.railway.app", built["hosts"])
        self.assertEqual(built["csrf"], ["https://hmis-api-production.up.railway.app"])
        self.assertEqual((built["railway"], built["render"]), (True, False))
        self.assertEqual(built["media"], "apps.core.storage.PrivateCloudinaryStorage")
        self.assertEqual(built["ip_header"], "HTTP_X_REAL_IP")

    def test_the_worker_and_beat_start_without_a_domain(self):
        result = _railway_settings(RAILWAY_WORKER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["hosts"], [])

    def test_production_without_postgres_still_refuses(self):
        result = _railway_settings({**RAILWAY_API, "DATABASE_URL": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no fallback to SQLite", result.stderr)


class RailwayDeployChecks(SimpleTestCase):
    def test_documents_must_be_on_cloudinary_on_railway(self):
        local = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                 "staticfiles": settings.STORAGES["staticfiles"]}
        with override_settings(**SECURE, HMIS_ON_RAILWAY=True, STORAGES=local):
            self.assertIn("hmis.E012", CheckDeployFails.ids(self))
        cloud = {**local, "default": {"BACKEND": "apps.core.storage.PrivateCloudinaryStorage"}}
        with override_settings(**SECURE, HMIS_ON_RAILWAY=True, STORAGES=cloud):
            self.assertNotIn("hmis.E012", CheckDeployFails.ids(self))


class TheRailwayConfigFiles(SimpleTestCase):
    """railway/*/railway.toml say what docs/RAILWAY.md says, and nothing else."""

    ROOT = BASE_DIR.parent.parent

    def load(self, service):
        import tomllib

        with open(self.ROOT / "railway" / service / "railway.toml", "rb") as handle:
            return tomllib.load(handle)

    def test_the_api_migrates_once_and_starts_gunicorn(self):
        api = self.load("api")
        # Built from Dockerfile.api, not Railpack: Python comes from the image.
        self.assertEqual((api["build"]["builder"], api["build"]["dockerfilePath"]),
                         ("DOCKERFILE", "Dockerfile.api"))
        self.assertNotIn("buildCommand", api["build"])
        self.assertTrue((BASE_DIR / api["build"]["dockerfilePath"]).exists())
        self.assertEqual(api["deploy"]["preDeployCommand"], ["python manage.py migrate --noinput"])
        self.assertTrue(api["deploy"]["startCommand"].startswith("gunicorn hmis.wsgi:application"))
        self.assertNotIn("migrate", api["deploy"]["startCommand"])
        self.assertEqual(api["deploy"]["healthcheckPath"], "/healthz/")

    def test_celery_commands_are_the_existing_ones(self):
        self.assertEqual(self.load("worker")["deploy"]["startCommand"],
                         "celery -A hmis worker -l info --concurrency 2")
        self.assertEqual(self.load("beat")["deploy"]["startCommand"],
                         "celery -A hmis beat -l info --schedule /tmp/celerybeat-schedule")
        for service in ("worker", "beat"):
            self.assertNotIn("preDeployCommand", self.load(service)["deploy"])

    def test_the_frontend_falls_back_to_index_and_sends_the_headers(self):
        caddy = (self.ROOT / "frontend" / "Caddyfile").read_text()
        for expected in ("/index.html", 'X-Frame-Options "DENY"', 'X-Content-Type-Options "nosniff"',
                         'Referrer-Policy "same-origin"', "Strict-Transport-Security",
                         "respond /health 200"):
            self.assertIn(expected, caddy)
        self.assertEqual(self.load("web")["deploy"]["healthcheckPath"], "/health")


class TheApiImage(SimpleTestCase):
    """Dockerfile.api: Python 3.12, collectstatic at build, no migrate, no secrets."""

    def dockerfile(self):
        return (BASE_DIR / "Dockerfile.api").read_text()

    def instructions(self):
        return [line.strip() for line in self.dockerfile().splitlines()
                if line.strip() and not line.strip().startswith("#")]

    def test_python_312_and_pip_through_python(self):
        lines = self.instructions()
        self.assertEqual(lines[0], "FROM python:3.12-slim")
        self.assertIn("RUN python -m pip install -r requirements.txt", lines)

    def test_collectstatic_at_build_and_never_migrate(self):
        runs = [line for line in self.instructions() if line.startswith("RUN")]
        self.assertIn("RUN DJANGO_SETTINGS_MODULE=hmis.settings_build python manage.py "
                      "collectstatic --noinput", runs)
        self.assertFalse(any("migrate" in line for line in runs))

    def test_gunicorn_takes_railways_port_and_the_image_holds_no_configuration(self):
        lines = self.instructions()
        cmd = next(line for line in lines if line.startswith("CMD"))
        self.assertIn('"gunicorn", "hmis.wsgi:application"', cmd)
        self.assertNotIn("8000", cmd)                    # no hard-coded bind
        self.assertNotIn("--bind", cmd)
        configured = " ".join(line for line in lines if line.startswith(("ENV", "ARG")))
        for name in ("DJANGO_SECRET_KEY", "DATABASE_URL", "REDIS_URL", "CLOUDINARY",
                     "CORS_ALLOWED_ORIGINS", "DJANGO_ENV", "RESEND", "ARG "):
            self.assertNotIn(name, configured)

    def test_the_build_context_leaves_local_and_secret_files_out(self):
        ignored = (BASE_DIR / ".dockerignore").read_text().splitlines()
        for entry in (".git", "venv/", "__pycache__/", "db.sqlite3", "*.sqlite3", ".env",
                      ".env.*", "media/", "staticfiles/", "dump.rdb", "node_modules/"):
            self.assertIn(entry, ignored)

    def test_build_settings_change_only_the_static_storage(self):
        from hmis import settings_build

        self.assertEqual(settings_build.STORAGES["staticfiles"]["BACKEND"],
                         "whitenoise.storage.CompressedManifestStaticFilesStorage")
        self.assertEqual(settings_build.STORAGES["default"], settings.STORAGES["default"])
        self.assertEqual(settings_build.HMIS_ENV, "development")   # no production configuration
