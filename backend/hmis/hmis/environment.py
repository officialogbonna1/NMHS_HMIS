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
import re
from urllib.parse import parse_qs, unquote, urlsplit

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


def on_render(env):
    """Render sets RENDER=true on every service it runs."""
    return (env.get("RENDER") or "").strip().lower() == "true"


def on_railway(env):
    """Railway sets RAILWAY_PROJECT_ID / RAILWAY_ENVIRONMENT_ID on every service."""
    return bool((env.get("RAILWAY_PROJECT_ID") or env.get("RAILWAY_ENVIRONMENT_ID") or "").strip())


#: The Host header Railway's deploy-time health check sends (Railway docs:
#: "add healthcheck.railway.app to your list of allowed hosts").
RAILWAY_HEALTHCHECK_HOST = "healthcheck.railway.app"


def railway_domain(env):
    """The service's public Railway domain (`*.up.railway.app` or a custom one)."""
    return (env.get("RAILWAY_PUBLIC_DOMAIN") or "").strip() if on_railway(env) else ""


def serves_http(env):
    """
    False only for a process that answers no HTTP request, and so has no
    hostname or origin to declare: a Render background worker or cron job, or
    a Railway service with no public domain (the Celery worker and Beat).
    Everything else (the web service, any self-hosted server) serves HTTP.
    """
    service_type = (env.get("RENDER_SERVICE_TYPE") or "").strip().lower()
    if on_render(env) and service_type in ("worker", "cron"):
        return False
    if on_railway(env) and not railway_domain(env):
        return False
    return True


def _database_url(url):
    """
    A `postgresql://user:password@host:port/name[?sslmode=…]` URL — the shape
    Render (and most hosts) hand out — as Django's settings. A malformed URL is
    refused without echoing it, because it carries the password.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise ImproperlyConfigured("DATABASE_URL is not a valid URL.") from None
    if parts.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured(
            "DATABASE_URL must be a postgresql:// URL in production. There is no fallback "
            "to SQLite.")
    config = {
        "NAME": unquote(parts.path.lstrip("/")),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(port or 5432),
    }
    absent = [key for key in ("NAME", "USER", "HOST") if not config[key]]
    if absent:
        raise ImproperlyConfigured(
            f"DATABASE_URL is missing its {', '.join(k.lower() for k in absent)}.")
    sslmode = parse_qs(parts.query).get("sslmode")
    if sslmode:
        config["OPTIONS"] = {"sslmode": sslmode[0]}
    return config


def database_config(env, base_dir, name):
    """
    `DATABASES["default"]` for this environment.

    Development is the SQLite file the project has always used. Production is
    PostgreSQL, and only PostgreSQL — from `DATABASE_URL` when it is set (one
    string, as Render's dashboard and Blueprint provide it), otherwise from the
    separate `DB_*` variables. Missing configuration is refused by name (values
    are never echoed), so a misconfigured server stops at start-up instead of
    creating an empty SQLite file and serving a hospital from it.
    """
    if name == DEVELOPMENT:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": base_dir / "db.sqlite3"}
    if (env.get("DATABASE_URL") or "").strip():
        config = _database_url(env["DATABASE_URL"])
    else:
        absent = missing(env, REQUIRED_DB_VARS)
        if absent:
            raise ImproperlyConfigured(
                "Production uses PostgreSQL: set DATABASE_URL, or all of "
                f"{', '.join(REQUIRED_DB_VARS)} (not set: {', '.join(absent)}). "
                "There is no fallback to SQLite.")
        config = {
            "NAME": env["DB_NAME"],
            "USER": env["DB_USER"],
            "PASSWORD": env["DB_PASSWORD"],
            "HOST": env["DB_HOST"],
            "PORT": (env.get("DB_PORT") or "5432").strip(),
        }
    return {
        "ENGINE": "django.db.backends.postgresql",
        **config,
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
        # Render's own `onrender.com` hostname — what its health check sends
        # when the service has no custom domain.
        render_host = (env.get("RENDER_EXTERNAL_HOSTNAME") or "").strip()
        if render_host and render_host not in values:
            values.append(render_host)
        # Railway: the service's public domain, and the Host its health check
        # sends — only on a service that serves HTTP (has a public domain).
        for railway_host in ([railway_domain(env), RAILWAY_HEALTHCHECK_HOST]
                             if railway_domain(env) else []):
            if railway_host not in values:
                values.append(railway_host)
        if not values and not serves_http(env):
            return []
        if not values:
            raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list the production hostnames.")
        if "*" in values:
            raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS may not be '*' in production.")
        return values
    return listing(env, "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")


def csrf_origins(env, name):
    if name == PRODUCTION:
        values = listing(env, "DJANGO_CSRF_TRUSTED_ORIGINS")
        render_url = (env.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
        if render_url and render_url not in values:
            values.append(render_url)
        if railway_domain(env) and f"https://{railway_domain(env)}" not in values:
            values.append(f"https://{railway_domain(env)}")
        if not values and not serves_http(env):
            return []
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


def cors_origins(env, name):
    """
    Browser origins allowed to call the API from another origin — the React
    static site when it is served apart from the API (Render). Production
    defaults to none (a same-origin deployment needs none) and accepts only
    https:// origins; development keeps the Vite origins.
    """
    if name == PRODUCTION:
        values = listing(env, "CORS_ALLOWED_ORIGINS")
        if any(not origin.startswith("https://") for origin in values):
            raise ImproperlyConfigured("CORS_ALLOWED_ORIGINS must be https:// origins in production.")
        return [origin.rstrip("/") for origin in values]
    return listing(env, "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")


_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def client_ip_header(env):
    """
    The request header that holds the real client address, as a `META` key.

    Unset means the historical rule (the first `X-Forwarded-For` hop, correct
    behind an Nginx that overwrites that header). Render's proxy *appends* to
    X-Forwarded-For, so its first hop is whatever the client sent; there,
    Cloudflare's `CF-Connecting-IP` — which Cloudflare always overwrites — is
    the address to trust.
    """
    raw = (env.get("HMIS_CLIENT_IP_HEADER") or "").strip()
    if not raw:
        return ""
    if not _HEADER_NAME.match(raw):
        raise ImproperlyConfigured("HMIS_CLIENT_IP_HEADER must be a header name, "
                                   "e.g. CF-Connecting-IP.")
    return "HTTP_" + raw.upper().replace("-", "_")


MEDIA_STORAGES = ("filesystem", "cloudinary")
REQUIRED_CLOUDINARY_VARS = ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")
#: How long a document link works. Short by default; capped at a day.
DEFAULT_LINK_SECONDS = 900
MAX_LINK_SECONDS = 86400


def media_storage(env, name):
    """
    Where uploaded patient documents are kept: Django's `STORAGES["default"]`.

    Development: the local `media/` directory, as always. Production must say
    which, explicitly (`DJANGO_MEDIA_STORAGE`), because getting it wrong loses
    patient documents silently:

    * `cloudinary` — Cloudinary, **privately** (apps/core/storage.py): every
      file is an `authenticated` asset, which Cloudinary refuses to deliver
      without a signature, and every link the API returns is a private download
      URL that expires after `CLOUDINARY_LINK_EXPIRY_SECONDS` (default 900 =
      15 minutes). Only an endpoint the caller may already read returns one.
    * `filesystem` — `MEDIA_ROOT` on the server's own disk, served by a
      restricted reverse proxy. **Refused on Render and Railway**: their service
      filesystems are wiped on every deploy, and nothing there serves `/media/`.
    """
    filesystem = {"BACKEND": "django.core.files.storage.FileSystemStorage"}
    if name == DEVELOPMENT:
        return filesystem
    choice = (env.get("DJANGO_MEDIA_STORAGE") or "").strip().lower()
    if choice not in MEDIA_STORAGES:
        raise ImproperlyConfigured(
            "DJANGO_MEDIA_STORAGE must be 'cloudinary' or 'filesystem' in production — where "
            "uploaded patient documents are kept has to be decided, not defaulted.")
    if choice == "filesystem":
        if on_render(env) or on_railway(env):
            host = "Render" if on_render(env) else "Railway"
            raise ImproperlyConfigured(
                f"DJANGO_MEDIA_STORAGE=filesystem is refused on {host}: the service "
                "filesystem is lost on every deploy. Use private Cloudinary storage (cloudinary).")
        return filesystem
    absent = missing(env, REQUIRED_CLOUDINARY_VARS)
    if absent:
        raise ImproperlyConfigured(f"DJANGO_MEDIA_STORAGE=cloudinary needs: {', '.join(absent)}.")
    raw_seconds = (env.get("CLOUDINARY_LINK_EXPIRY_SECONDS") or str(DEFAULT_LINK_SECONDS)).strip()
    try:
        seconds = int(raw_seconds)
    except ValueError:
        seconds = 0
    if not 60 <= seconds <= MAX_LINK_SECONDS:
        raise ImproperlyConfigured(
            f"CLOUDINARY_LINK_EXPIRY_SECONDS must be a whole number from 60 to {MAX_LINK_SECONDS}.")
    folder = (env.get("CLOUDINARY_FOLDER") or "nmhs-hmis/media").strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_\-/]{1,100}", folder):
        raise ImproperlyConfigured(
            "CLOUDINARY_FOLDER may contain only letters, digits, '_', '-' and '/'.")
    return {
        "BACKEND": "apps.core.storage.PrivateCloudinaryStorage",
        "OPTIONS": {
            "cloud_name": env["CLOUDINARY_CLOUD_NAME"].strip(),
            "api_key": env["CLOUDINARY_API_KEY"].strip(),
            "api_secret": env["CLOUDINARY_API_SECRET"].strip(),
            "folder": folder,
            "link_seconds": seconds,
        },
    }


def admin_networks(env):
    """`HMIS_ADMIN_ALLOWED_NETWORKS` — comma-separated CIDR ranges, or none."""
    import ipaddress

    networks = []
    for value in listing(env, "HMIS_ADMIN_ALLOWED_NETWORKS"):
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            raise ImproperlyConfigured(
                f"HMIS_ADMIN_ALLOWED_NETWORKS has an invalid range: {value!r}.") from None
    return tuple(networks)
