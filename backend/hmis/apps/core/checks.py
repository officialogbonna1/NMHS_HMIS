"""
`manage.py check --deploy` — refuse, don't warn, when production is insecure.

Django's own deployment checks are warnings, and a warning does not fail the
command. These are errors, so `check --deploy` exits non-zero until the
production configuration is actually production. Most missing *variables* stop
the process earlier, at settings import (hmis/environment.py); these catch the
switches a deployment may turn off, and a `--deploy` run outside production.
"""
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


@register(Tags.security, deploy=True)
def production_configuration(app_configs, **kwargs):
    if not getattr(settings, "IS_PRODUCTION", False):
        return [Error(
            "DJANGO_ENV is not 'production'; this is a development configuration "
            "(SQLite, development secret, no HTTPS).",
            hint="Set DJANGO_ENV=production and the production variables "
                 "(docs/DEPLOYMENT.md) before deploying.",
            id="hmis.E001")]

    problems = []

    def need(ok, msg, hint, ident):
        if not ok:
            problems.append(Error(msg, hint=hint, id=ident))

    need(not settings.DEBUG, "DEBUG is on.", "DJANGO_DEBUG must be false.", "hmis.E002")
    need(settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql",
         "Production is not using PostgreSQL.", "Set DB_NAME, DB_USER, DB_PASSWORD, DB_HOST.",
         "hmis.E003")
    need(getattr(settings, "SECURE_SSL_REDIRECT", False),
         "HTTP is not redirected to HTTPS.", "Leave DJANGO_SECURE_SSL_REDIRECT on.", "hmis.E004")
    need(getattr(settings, "SESSION_COOKIE_SECURE", False)
         and getattr(settings, "CSRF_COOKIE_SECURE", False),
         "Session or CSRF cookies may be sent over HTTP.", "Both must be secure.", "hmis.E005")
    need(getattr(settings, "SECURE_HSTS_SECONDS", 0) > 0,
         "HSTS is off.", "Set DJANGO_SECURE_HSTS_SECONDS above zero.", "hmis.E006")
    need(getattr(settings, "SECURE_PROXY_SSL_HEADER", None) is not None
         or not getattr(settings, "SECURE_SSL_REDIRECT", False),
         "HTTPS redirect is on but Django cannot see that the proxy used HTTPS — "
         "every request would redirect forever.",
         "Leave DJANGO_BEHIND_HTTPS_PROXY on behind Nginx.", "hmis.E007")
    need("*" not in settings.ALLOWED_HOSTS and bool(settings.ALLOWED_HOSTS),
         "ALLOWED_HOSTS is empty or a wildcard.", "List the production hostnames.", "hmis.E008")
    need(all(o.startswith("https://") for o in settings.CSRF_TRUSTED_ORIGINS)
         and bool(settings.CSRF_TRUSTED_ORIGINS),
         "CSRF_TRUSTED_ORIGINS is empty or not HTTPS.", "List the https:// origins.",
         "hmis.E009")
    need(not getattr(settings, "SQL_DEBUG", False),
         "SQL logging is on.", "SQL and its parameters are never logged in production.",
         "hmis.E010")

    if not _env_has("REDIS_URL"):
        problems.append(Warning(
            "REDIS_URL is not set; Celery and the shared login-lockout cache are using "
            "their local defaults.",
            hint="Set REDIS_URL so every Gunicorn worker shares one lockout count.",
            id="hmis.W001"))
    return problems


def _env_has(name):
    import os
    return bool(os.environ.get(name))
