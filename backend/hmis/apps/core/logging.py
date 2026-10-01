"""
Two logging filters every handler runs (see LOGGING in settings.py).

`RequestIdFilter` stamps each record with the request ID of the request — or
Celery task — that produced it, so one support reference finds every line of
one request across Django and the worker.

`RedactingFilter` is the safety net under a rule the code already keeps: no
logger here writes a body, a header, a cookie or a credential. A net is still
worth having, because an exception's own message is not under our control — a
database driver will happily name a DSN, a library will quote the header it
rejected. The filter renders the record (message *and* traceback), scrubs it,
and hands the logging system the scrubbed text. It knows two kinds of secret:

* **shapes** — an `Authorization:` value, `Token`/`Bearer` credentials,
  `password=` / `secret=` / `api_key=` style assignments, `sessionid` and
  `csrftoken` cookies, the password in a `scheme://user:password@host` URL,
  Resend keys and private-key blocks;
* **values** — the configured `SECRET_KEY`, database password and Redis URL
  password, replaced wherever they appear verbatim.

It is not a patient-data scrubber and does not pretend to be: patient
information is kept out of the logs by never being logged (apps/core/
exceptions.py, middleware.py, accounts/views.py), not by pattern-matching names.
"""
import contextvars
import logging
import re
from urllib.parse import urlsplit

#: The request (or Celery task) the current code is running for.
request_id_var = contextvars.ContextVar("hmis_request_id", default="-")

REDACTED = "[REDACTED]"

_PATTERNS = [
    # Authorization: Token abc / Bearer abc / Basic abc — the whole value.
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}]+(\s+[^\s,;\"'}]+)?"),
     r"\1" + REDACTED),
    (re.compile(r"(?i)\b(token|bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    # key=value / "key": "value" for credential-shaped keys.
    (re.compile(r"(?i)([\"']?\b(?:password|passwd|pwd|secret|secret_key|api[_-]?key|"
                r"access[_-]?token|refresh[_-]?token|auth[_-]?token|token|sessionid|"
                r"csrftoken|csrfmiddlewaretoken)\b[\"']?\s*[:=]\s*[\"']?)[^\s,;&\"'}]+"),
     r"\1" + REDACTED),
    # scheme://user:password@host — keep the scheme, user and host.
    (re.compile(r"([a-z][a-z0-9+.-]*://[^:/@\s]*:)[^@\s/]+(@)", re.I), r"\1" + REDACTED + r"\2"),
    # Resend keys and PEM private keys.
    (re.compile(r"\bre_[A-Za-z0-9_]{16,}"), REDACTED),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     REDACTED),
]


def _configured_secrets():
    """The literal secret values this process holds, longest first."""
    from django.conf import settings

    values = set()
    try:
        values.add(getattr(settings, "SECRET_KEY", "") or "")
        for db in getattr(settings, "DATABASES", {}).values():
            values.add(db.get("PASSWORD") or "")
        for name in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
            url = getattr(settings, name, "") or ""
            values.add(urlsplit(url).password or "")
        values.add(getattr(settings, "RESEND_API_KEY", "") or "")
    except Exception:  # settings not configured (e.g. a bare script)
        pass
    # Short values would redact ordinary words; real secrets are long.
    return sorted((v for v in values if len(v) >= 6), key=len, reverse=True)


def redact(text):
    """`text` with every credential this module recognises replaced."""
    if not text:
        return text
    for secret in _configured_secrets():
        text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrub the fully rendered record — message, arguments and traceback."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.msg = redact(message)
        record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        # The traceback now lives, scrubbed, in exc_text; formatters print that
        # rather than re-rendering the raw exception.
        record.exc_info = None
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


class RequestIdFilter(logging.Filter):
    """Give every record a `request_id` (`-` outside a request or task)."""

    def filter(self, record):
        if not getattr(record, "request_id", None):
            # Django's own `django.request` lines are written after the
            # middleware has returned, but they carry the request itself.
            from_request = getattr(getattr(record, "request", None), "request_id", None)
            record.request_id = from_request or request_id_var.get()
        return True
