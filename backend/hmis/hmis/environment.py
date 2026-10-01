"""
Which environment this process is, and what that environment requires.

`settings.py` reads the process environment, as it always has; this module
holds the decisions that read so they can be tested as plain functions without
re-importing settings. Two environments, named explicitly by `DJANGO_ENV`:

    development  (the default) — SQLite at backend/hmis/db.sqlite3, DEBUG on
                 unless switched off, nothing else required. `runserver` and the
                 test suite work exactly as they always have.
    production   — PostgreSQL from the `DB_*` variables, a real secret key,
                 explicit hosts and origins. Anything missing is an
                 `ImproperlyConfigured` at start-up that names the variable,
                 never a quiet fallback to SQLite or to a development default.

Production is never inferred from `DEBUG=False`: a developer switching DEBUG off
to look at the 404 page is not deploying.
"""
from django.core.exceptions import ImproperlyConfigured

DEVELOPMENT = "development"
PRODUCTION = "production"
ENVIRONMENTS = (DEVELOPMENT, PRODUCTION)

#: The development key in `settings.py`. Production refuses it by value.
DEV_SECRET_KEY = "dev-only-change-me"
#: Example values from `.env.example` that must never reach production.
PLACEHOLDER_SECRETS = {DEV_SECRET_KEY, "change-me", ""}
MIN_SECRET_KEY_LENGTH = 50

#: The PostgreSQL connection, as the existing `.env.example` already names it.
#: `DB_PORT` has a default; the rest must be given.
REQUIRED_DB_VARS = ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST")


def environment(env):
    """The named environment, or a clear refusal for anything else."""
    name = (env.get("DJANGO_ENV") or DEVELOPMENT).strip().lower()
    if name not in ENVIRONMENTS:
        raise ImproperlyConfigured(
            f"DJANGO_ENV must be one of {', '.join(ENVIRONMENTS)} (got {name!r}).")
    return name


def flag(env, name, default):
    """A boolean variable: true/1/yes/on, false/0/no/off, or the default."""
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    raise ImproperlyConfigured(f"{name} must be true or false (got {raw!r}).")


def listing(env, name, default=""):
    """A comma-separated variable as a list, blanks dropped."""
    return [part.strip() for part in (env.get(name) or default).split(",") if part.strip()]


def missing(env, names):
    return [name for name in names if not (env.get(name) or "").strip()]


def database_config(env, base_dir, name):
    """
    `DATABASES["default"]` for this environment.

    Development is the SQLite file the project has always used. Production is
    PostgreSQL, and only PostgreSQL: missing variables are refused by name (the
    values are never echoed), so a misconfigured server stops at start-up
    instead of creating an empty SQLite file and serving a hospital from it.
    """
    if name == DEVELOPMENT:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": base_dir / "db.sqlite3"}
    absent = missing(env, REQUIRED_DB_VARS)
    if absent:
        raise ImproperlyConfigured(
            "Production uses PostgreSQL and these variables are not set: "
            f"{', '.join(absent)}. There is no fallback to SQLite.")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env["DB_NAME"],
        "USER": env["DB_USER"],
        "PASSWORD": env["DB_PASSWORD"],
        "HOST": env["DB_HOST"],
        "PORT": (env.get("DB_PORT") or "5432").strip(),
        # Reuse connections across requests; Gunicorn workers are long-lived.
        "CONN_MAX_AGE": int(env.get("DB_CONN_MAX_AGE") or 60),
        "CONN_HEALTH_CHECKS": True,
    }


def secret_key(env, name):
    """The signing key: anything in development, a real one in production."""
    key = env.get("DJANGO_SECRET_KEY") or DEV_SECRET_KEY
    if name == PRODUCTION:
        if key in PLACEHOLDER_SECRETS or len(key) < MIN_SECRET_KEY_LENGTH:
            raise ImproperlyConfigured(
                "DJANGO_SECRET_KEY must be set to a random value of at least "
                f"{MIN_SECRET_KEY_LENGTH} characters in production.")
    return key


def debug(env, name):
    """DEBUG: on by default in development, never on in production."""
    if name == PRODUCTION:
        if flag(env, "DJANGO_DEBUG", False):
            raise ImproperlyConfigured("DJANGO_DEBUG cannot be true in production.")
        return False
    # Development keeps the historical rule: only the exact string "True"
    # switches it on, and an unset variable means on.
    return env.get("DJANGO_DEBUG", "True") == "True"


def hosts(env, name):
    if name == PRODUCTION:
        values = listing(env, "DJANGO_ALLOWED_HOSTS")
        if not values:
            raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list the production hostnames.")
        if "*" in values:
            raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS may not be '*' in production.")
        return values
    return listing(env, "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")


def csrf_origins(env, name):
    if name == PRODUCTION:
        values = listing(env, "DJANGO_CSRF_TRUSTED_ORIGINS")
        if not values:
            raise ImproperlyConfigured(
                "DJANGO_CSRF_TRUSTED_ORIGINS must list the https:// origins staff and "
                "administrators use (Django Admin sign-in is refused without it).")
        insecure = [origin for origin in values if not origin.startswith("https://")]
        if insecure:
            raise ImproperlyConfigured(
                "DJANGO_CSRF_TRUSTED_ORIGINS must be https:// origins in production.")
        return values
    return listing(env, "DJANGO_CSRF_TRUSTED_ORIGINS",
                   "http://localhost:5173,http://127.0.0.1:5173")
