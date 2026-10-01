import os
import sys
from pathlib import Path

from hmis import environment as envconf

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- environment
#
# `DJANGO_ENV` names the environment explicitly: `development` (the default —
# SQLite, `runserver`, nothing to configure) or `production` (PostgreSQL, a real
# secret, explicit hosts and HTTPS). `hmis/environment.py` holds the rules, and
# production refuses to start with anything missing rather than falling back.
# Settings come from the process environment only; `.env` files are not read —
# a server supplies them through its service manager (see docs/DEPLOYMENT.md).
HMIS_ENV = envconf.environment(os.environ)
IS_PRODUCTION = HMIS_ENV == envconf.PRODUCTION

SECRET_KEY = envconf.secret_key(os.environ, HMIS_ENV)
DEBUG = envconf.debug(os.environ, HMIS_ENV)
ALLOWED_HOSTS = envconf.hosts(os.environ, HMIS_ENV)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "corsheaders",

    "apps.accounts",
    "apps.patients",
    "apps.clinical",
    "apps.inventory",
    "apps.pharmacy",
    "apps.sales",
    "apps.appointments",
    "apps.ai_agents",
    "apps.core",
    "apps.departments",
    "apps.workflow",
    "apps.billing",
    "apps.diagnostics",
    "apps.laboratory",
    "apps.inpatient",
    "apps.maternity",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    # First, so every log line of the request — and the response — carries
    # its X-Request-ID (apps/core/middleware.py).
    "apps.core.middleware.RequestIdMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hmis.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

WSGI_APPLICATION = "hmis.wsgi.application"

# Development: the SQLite file the project has always used (db.sqlite3 beside
# manage.py — untouched by any of this). Production: PostgreSQL from DB_NAME,
# DB_USER, DB_PASSWORD, DB_HOST and DB_PORT, and nothing else — a missing
# variable stops the process at start-up (`envconf.database_config`).
DATABASES = {"default": envconf.database_config(os.environ, BASE_DIR, HMIS_ENV)}

# Password rules for the forms that validate one (createsuperuser, Django
# Admin's add-user and set-password forms). Production only, so the local
# workflow is unchanged.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
] if IS_PRODUCTION else []


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "hmis.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    # One shape for every refusal, and no stack trace on any of them.
    # `apps/core/exceptions.py` says what each kind of failure answers with.
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}

CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

# Development keeps the two Vite origins it always had; production must name
# its https:// origins (DJANGO_CSRF_TRUSTED_ORIGINS) or it will not start.
CSRF_TRUSTED_ORIGINS = envconf.csrf_origins(os.environ, HMIS_ENV)

# ------------------------------------------------------------ HTTPS / headers
#
# Always on (Django's defaults, stated so nobody has to look them up): no MIME
# sniffing, no framing (nothing in the application uses an iframe — printing
# is the page itself), and a same-origin referrer.
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

if IS_PRODUCTION:
    # Production sits behind Nginx, which terminates TLS and says so in
    # X-Forwarded-Proto. Each switch can be turned off for an unusual proxy
    # set-up, but the defaults are the secure ones, and `check --deploy` fails
    # (apps/core/checks.py) when one is off.
    if envconf.flag(os.environ, "DJANGO_BEHIND_HTTPS_PROXY", True):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = envconf.flag(os.environ, "DJANGO_SECURE_SSL_REDIRECT", True)
    # The health check is answered on plain HTTP so a probe on the loopback
    # interface (behind the proxy) is not redirected away from it.
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    # One year once HTTPS is known to work; a deployment testing HTTPS for the
    # first time can start lower. Subdomains and preload are commitments for
    # the whole domain, so they are opt-in.
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", 31536000))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = envconf.flag(
        os.environ, "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    SECURE_HSTS_PRELOAD = envconf.flag(os.environ, "DJANGO_SECURE_HSTS_PRELOAD", False)

CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
# Celery uses the Django LOGGING below (and so its redaction and request-ID
# filters) rather than installing its own root handlers.
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_BEAT_SCHEDULE = {
    "check-low-stock-nightly": {
        "task": "apps.inventory.tasks.check_low_stock",
        "schedule": 60 * 60 * 24,
    },
    "check-expiring-batches-nightly": {
        "task": "apps.inventory.tasks.check_expiring_batches",
        "schedule": 60 * 60 * 24,
    },
}

# The login lockout (`apps/accounts/lockout.py`) is the only thing in the
# system that reads or writes the cache, and what it keeps there is a count of
# consecutive failed sign-ins. That count has to be one count: with several
# worker processes and a per-process cache, five attempts becomes five *per
# worker*. Redis is already a dependency and already configured above for
# Celery, so the cache points at it when one is configured and falls back to
# this process's own memory when there is not — which is what a single-process
# `runserver` and the test suite want, and neither needs Redis to be up.
#
# Nothing else uses the cache, so this setting changes nothing but the lockout.
# If Redis is configured and then goes down, the lockout fails *open* (see that
# module): a hospital being unable to reach its own records because a cache is
# unavailable is a worse failure than a brute-force window.
_CACHE_URL = os.environ.get("CACHE_URL") or os.environ.get("REDIS_URL")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _CACHE_URL,
    } if _CACHE_URL else {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "hmis-login-lockout",
    }
}

# How many consecutive failed sign-ins a client may make against one username
# before it is shut out, and for how long. The fifth failure is the one that
# locks: four are allowed. Settings rather than constants so a deployment can
# tighten them without a code change; the defaults are the rule as specified.
LOGIN_MAX_FAILED_ATTEMPTS = int(os.environ.get("LOGIN_MAX_FAILED_ATTEMPTS", 5))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", 300))


# --------------------------------------------------------------------- email
#
# Transactional admin notifications go out through Resend
# (`apps/core/email.py`). Everything here is environment configuration: an API
# key does not belong in a repository, and neither does the address of whoever
# currently administers the hospital — that is a person, and people change.
#
# With no key configured, `apps/core/email.py` does not send and says so in the
# log. That is the correct behaviour for a developer machine and for a test
# run: nothing is emailed and nothing fails.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
HMIS_EMAIL_FROM = os.environ.get("HMIS_EMAIL_FROM", "")
HMIS_ADMIN_EMAIL = os.environ.get("HMIS_ADMIN_EMAIL", "")
# Where a link in an email points. The API and the React app are served from
# different origins in development, and an email is read outside both.
HMIS_BASE_URL = os.environ.get("HMIS_BASE_URL", "http://localhost:5173").rstrip("/")
# How long Resend gets before the notification is given up on. A hospital
# transaction has already committed by the time this runs; nobody waits.
RESEND_TIMEOUT_SECONDS = float(os.environ.get("RESEND_TIMEOUT_SECONDS", 10))

# ------------------------------------------------------------------- logging
#
# What a user sees and what an administrator reads are different things. The
# API answers an unexpected failure with a reference and nothing else
# (apps/core/exceptions.py); the traceback goes here, under that reference and
# the request's X-Request-ID.
#
# Where it goes: the console (stdout/stderr). Under systemd that is the
# journal, which rotates and expires on its own (docs/DEPLOYMENT.md), and it is
# the recommended production destination. Setting DJANGO_LOG_DIR adds rotating
# files beside it — application, errors and security — for a server that keeps
# file logs; each is capped (DJANGO_LOG_MAX_BYTES × DJANGO_LOG_BACKUPS).
#
# What never goes there: every handler passes through `RedactingFilter`
# (apps/core/logging.py), which scrubs credentials — tokens, Authorization
# headers, passwords, connection-string passwords, the secret key and the
# database password themselves — from the message *and* the traceback. No
# logger in this application writes a request body, a header, a cookie or a
# patient record; the request log records method, path (never the query
# string, which carries searches by name and phone), status, duration, user id
# and request id. SQL is never logged in production; DJANGO_SQL_DEBUG=true
# turns it on in development only.
LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO" if IS_PRODUCTION else "DEBUG").upper()
LOG_DIR = os.environ.get("DJANGO_LOG_DIR", "").strip()
# A line per request (apps/core/middleware.py). On by default in production,
# where it is the application's own access log; development has runserver's.
REQUEST_LOGGING = envconf.flag(os.environ, "DJANGO_REQUEST_LOG", IS_PRODUCTION)
SQL_DEBUG = envconf.flag(os.environ, "DJANGO_SQL_DEBUG", False) and not IS_PRODUCTION
# The permission walk in the test suite is thousands of deliberate refusals;
# printing each would bury the results. Tests that check a security line
# capture it with assertLogs, which works at any level.
_TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
SECURITY_LOG_LEVEL = "ERROR" if _TESTING else "INFO"

_LOG_FILTERS = ["redact", "request_id"]
_handlers = {
    "console": {
        "class": "logging.StreamHandler",
        "formatter": "standard",
        "filters": _LOG_FILTERS,
    },
}
_app_handlers = ["console"]
_security_handlers = ["console"]
if LOG_DIR:
    _rotating = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "standard",
        "filters": _LOG_FILTERS,
        "maxBytes": int(os.environ.get("DJANGO_LOG_MAX_BYTES", 10 * 1024 * 1024)),
        "backupCount": int(os.environ.get("DJANGO_LOG_BACKUPS", 10)),
        "encoding": "utf-8",
        "delay": True,
    }
    _handlers.update({
        "app_file": {**_rotating, "filename": os.path.join(LOG_DIR, "hmis.log")},
        "error_file": {**_rotating, "filename": os.path.join(LOG_DIR, "error.log"),
                       "level": "ERROR"},
        "security_file": {**_rotating, "filename": os.path.join(LOG_DIR, "security.log")},
    })
    _app_handlers = ["console", "app_file", "error_file"]
    _security_handlers = ["console", "security_file", "error_file"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact": {"()": "apps.core.logging.RedactingFilter"},
        "request_id": {"()": "apps.core.logging.RequestIdFilter"},
    },
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname} {name} [{request_id}] {message}",
            "style": "{",
        },
    },
    "handlers": _handlers,
    "root": {"handlers": _app_handlers, "level": "INFO"},
    "loggers": {
        # The application's own operational failures.
        "hmis.api": {"handlers": _app_handlers, "level": LOG_LEVEL, "propagate": False},
        "hmis.email": {"handlers": _app_handlers, "level": LOG_LEVEL, "propagate": False},
        "hmis.request": {"handlers": _app_handlers, "level": "INFO", "propagate": False},
        "hmis.health": {"handlers": _app_handlers, "level": LOG_LEVEL, "propagate": False},
        # Sign-in, sign-out, lockouts and refusals.
        "hmis.security": {"handlers": _security_handlers, "level": SECURITY_LOG_LEVEL,
                          "propagate": False},
        "django.security": {"handlers": _security_handlers, "level": "INFO",
                            "propagate": False},
        # Django logs every 4xx from `django.request` at WARNING, which turns
        # each ordinary 404 and 403 into a line that reads like a fault.
        "django.request": {"handlers": _app_handlers, "level": "ERROR", "propagate": False},
        # SQL and its parameters: never in production (see SQL_DEBUG).
        "django.db.backends": {"handlers": _app_handlers,
                               "level": "DEBUG" if SQL_DEBUG else "WARNING",
                               "propagate": False},
        # Celery's worker and Beat log through the same handlers
        # (CELERY_WORKER_HIJACK_ROOT_LOGGER is False). Task arguments are not
        # logged: Celery's own messages carry the task name and id only.
        "celery": {"handlers": _app_handlers, "level": "INFO", "propagate": False},
    },
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Africa/Lagos")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# Where `collectstatic` gathers Django Admin's CSS and JS for Nginx to serve.
# Unused by `runserver`; the directory is gitignored.
STATIC_ROOT = Path(os.environ.get("DJANGO_STATIC_ROOT") or BASE_DIR / "staticfiles")
MEDIA_URL = "/media/"  # root-relative: uploaded files are linked from the SPA, not from a Django template
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT") or BASE_DIR / "media")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
