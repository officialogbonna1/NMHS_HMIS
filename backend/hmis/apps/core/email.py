"""
Transactional admin email, through Resend.

**The one rule this module exists to keep: sending email can never fail a
hospital transaction.** A patient is registered, a bill is raised, money is
taken, somebody is discharged — those are the record, and they are already
committed by the time anything here runs. An email is a courtesy to whoever
administers the hospital. So every function below returns rather than raises,
whatever Resend, the network, the template engine or the configuration does.
There is exactly one `except Exception` in this file and it is deliberate: it
is the boundary between an external service and the hospital's own record, and
the right answer on that boundary is a log line.

**Nothing here is addressed in code.** The recipient, the sender and the API
key are environment configuration (`HMIS_ADMIN_EMAIL`, `HMIS_EMAIL_FROM`,
`RESEND_API_KEY`), read through settings at call time rather than at import, so
a deployment changes them without a code change and a test can override them.
With no key or no recipient configured nothing is sent and the reason is
logged — which is what a developer machine and a test run want.

**It is sent after the transaction, not inside it.** `dispatch_admin_email`
hands the work to `transaction.on_commit`, so a transaction that rolls back
sends nothing at all: there is no state where the admin is told about a
discharge the database then threw away. Once committed, the work goes to
Celery if a broker will take it and runs inline if none will — the HTTP request
never waits on Resend either way.

**It is idempotent per event.** Each send names the thing it is about
(`patient.registered:NMHS-P000123`, `payment.received:PAY-42`), and that key is
held in the cache for a day; a second send for the same key does nothing. So a
double-clicked button, a retried request or a refreshed browser produces one
email. The cache is the right home for it rather than a table: it is a
de-duplication window, not a record, and if it is ever lost the cost is one
extra notification rather than a wrong one.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.template.loader import render_to_string

logger = logging.getLogger("hmis.email")

RESEND_ENDPOINT = "https://api.resend.com/emails"

#: How long one event's key blocks a repeat send.
DEDUPE_SECONDS = 60 * 60 * 24


def admin_recipient():
    """Who gets told. Configuration, never a literal in this repository."""
    return (getattr(settings, "HMIS_ADMIN_EMAIL", "") or "").strip()


def sender():
    return (getattr(settings, "HMIS_EMAIL_FROM", "") or "").strip()


def api_key():
    return (getattr(settings, "RESEND_API_KEY", "") or "").strip()


def is_configured():
    """Whether there is anywhere to send from, to, and a key to do it with."""
    return bool(api_key() and sender() and admin_recipient())


def base_url():
    return (getattr(settings, "HMIS_BASE_URL", "") or "").rstrip("/")


# ------------------------------------------------------------------ the send


def send_via_resend(*, subject, html, to, from_address, timeout=None):
    """
    One HTTP call to Resend. Raises on failure — the catching is done by
    `deliver` below, which is the only caller, so that this function stays
    something a test can assert the shape of.
    """
    import httpx                                   # imported late: see module note

    timeout = timeout or float(getattr(settings, "RESEND_TIMEOUT_SECONDS", 10))
    response = httpx.post(
        RESEND_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key()}",
                 "Content-Type": "application/json"},
        json={"from": from_address, "to": [to], "subject": subject, "html": html},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def deliver(*, subject, html, to=None, dedupe_key=""):
    """
    Send, and never raise.

    Returns True only if Resend accepted it. Every other outcome — no
    configuration, a duplicate, a timeout, a 4xx, a 5xx, a DNS failure, a
    library that is not installed — is False and a log line. The caller is a
    hospital transaction that has already happened; there is nothing useful it
    could do with an exception.
    """
    recipient = (to or admin_recipient()).strip()
    if not is_configured() or not recipient:
        logger.info("Admin email not sent (%s): email is not configured.", subject)
        return False

    if dedupe_key and not _claim(dedupe_key):
        logger.info("Admin email not sent (%s): %s was already sent.", subject, dedupe_key)
        return False

    try:
        send_via_resend(subject=subject, html=html, to=recipient, from_address=sender())
    except Exception as exc:
        # The boundary. Resend being down, slow, rate-limiting, mis-keyed or
        # unreachable is an operational condition of somebody else's service,
        # and the hospital's record has already been written.
        logger.warning("Admin email failed (%s) via Resend: %s: %s",
                       subject, type(exc).__name__, exc)
        _release(dedupe_key)
        return False

    logger.info("Admin email sent: %s -> %s", subject, _redact(recipient))
    return True


def _claim(key):
    """
    Take this event's slot, if it is free.

    `cache.add` is the atomic half of this: it writes only when the key is
    absent, so two requests racing on the same event produce one email rather
    than two. A cache that raises means no de-duplication rather than no
    email — the notification matters more than the duplicate.
    """
    try:
        return bool(cache.add(_cache_key(key), 1, DEDUPE_SECONDS))
    except Exception:
        return True


def _release(key):
    """
    A send that failed never happened, so its slot goes back — otherwise one
    Resend timeout would suppress that event's email for a day, and a Celery
    retry would find its own claim in the way.
    """
    if not key:
        return
    try:
        cache.delete(_cache_key(key))
    except Exception:
        pass


def _cache_key(key):
    return f"admin-email:{key}"


def _redact(address):
    """Enough of the address to recognise, not enough to harvest from a log."""
    name, _, domain = address.partition("@")
    return f"{name[:2]}***@{domain}" if domain else "***"


# ----------------------------------------------------------- rendering + queueing


def render(template, context):
    """
    An event's HTML, through the shared hospital layout.

    `emails/base.html` holds the masthead, the typography and the footer;
    `context["hospital"]` is the hospital's own configured identity
    (`core.HospitalSettings`, rule 31) rather than a name written into a
    template. A template that cannot be rendered is logged and produces
    nothing — a broken template must not raise into a caller either.
    """
    from .models import HospitalSettings

    try:
        hospital = HospitalSettings.load()
    except Exception:
        hospital = None
    return render_to_string(f"emails/{template}.html", {
        **context,
        "hospital": hospital,
        "base_url": base_url(),
    })


def dispatch_admin_email(*, event, subject, template, context, reference=""):
    """
    The entry point every hospital event uses.

    **Queued on commit.** Nothing is sent from inside the transaction that
    wrote the record: if that transaction rolls back, no email goes out, and
    if it commits the email describes something that is genuinely there.

    **Off the request.** Once committed, the work is handed to Celery so the
    person at the desk is not waiting on Resend. If no broker will take it —
    Redis down, no worker, running in a test — it runs inline instead, still
    wrapped, still unable to raise. Degrading to a slightly slower request is
    the right failure; losing the notification silently is not.

    Returns nothing and raises nothing. Callers do not check it.
    """
    key = f"{event}:{reference}" if reference else ""

    def _go():
        try:
            html = render(template, context)
        except Exception as exc:
            logger.warning("Admin email template %s failed to render: %s: %s",
                           template, type(exc).__name__, exc)
            return
        _queue(subject=subject, html=html, dedupe_key=key)

    try:
        transaction.on_commit(_go)
    except Exception as exc:
        logger.warning("Could not queue admin email (%s): %s", subject, exc)


def _queue(*, subject, html, dedupe_key):
    from .tasks import send_admin_email_task

    try:
        send_admin_email_task.delay(subject=subject, html=html, dedupe_key=dedupe_key)
        return
    except Exception as exc:
        # No broker, no worker, or Redis refusing connections. Not a reason to
        # drop the notification.
        logger.info("Celery would not take the email (%s); sending inline. %s",
                    subject, type(exc).__name__)
    deliver(subject=subject, html=html, dedupe_key=dedupe_key)


__all__ = ["admin_recipient", "deliver", "dispatch_admin_email", "is_configured",
           "render", "send_via_resend"]
