"""
Background delivery of admin email.

Celery already runs in this deployment (`hmis/celery.py`, the inventory
alerts), so this is one more task on the existing queue rather than a second
one. Its whole job is to keep Resend off the HTTP request: by the time it runs,
the patient is registered, the bill is raised or the discharge is filed, and
the person at the desk has already been answered.

**A retry must not become a second email.** The task carries the event's
de-duplication key and `apps/core/email.deliver` claims it — a claim that is
released only when a send genuinely failed, so a retry after a timeout can try
again while a retry after a success cannot send twice.

**It gives up quietly.** Three attempts with a widening gap, then the failure
stands in the log and nothing else happens. A notification that could not be
delivered is not worth waking anyone over, and it must never retry forever
against a provider that is rejecting the message outright.
"""
import logging

from celery import shared_task

logger = logging.getLogger("hmis.email")

MAX_RETRIES = 3


@shared_task(bind=True, max_retries=MAX_RETRIES, default_retry_delay=60,
             acks_late=True, ignore_result=True)
def send_admin_email_task(self, *, subject, html, dedupe_key=""):
    """
    Deliver one rendered email.

    `deliver` never raises, so the retry decision is made from its answer
    rather than from an exception: False means it did not go, and the key has
    been released, so trying again is safe.
    """
    from .email import deliver

    if deliver(subject=subject, html=html, dedupe_key=dedupe_key):
        return True

    try:
        # `countdown` widens with each attempt: a provider that is rate
        # limiting or briefly down is given room rather than hammered.
        raise self.retry(countdown=60 * (2 ** self.request.retries))
    except Exception:
        if self.request.retries >= MAX_RETRIES:
            logger.warning("Giving up on admin email after %s attempts: %s",
                           self.request.retries, subject)
        raise
