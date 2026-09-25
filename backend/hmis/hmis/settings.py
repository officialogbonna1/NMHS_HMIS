import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

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

# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.postgresql",
#         "NAME": os.environ.get("DB_NAME", "hmis"),
#         "USER": os.environ.get("DB_USER", "hmis"),
#         "PASSWORD": os.environ.get("DB_PASSWORD", "hmis"),
#         "HOST": os.environ.get("DB_HOST", "localhost"),
#         "PORT": os.environ.get("DB_PORT", "5432"),
#     }
# }


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}



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

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
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
# An expected refusal is not news: a wrong password, a 404, a 403 and a
# validation error are the application working. What must always reach the log
# is the unexpected — `apps.core.exceptions` logs those with their traceback
# and a reference the user is shown, so a support call names the exact entry.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # The two that carry this application's own operational failures.
        "hmis.api": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "hmis.email": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Django logs every 4xx from `django.request` at WARNING, which turns
        # each ordinary 404 and 403 into a line that reads like a fault.
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Africa/Lagos")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "/media/"  # root-relative: uploaded files are linked from the SPA, not from a Django template
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
