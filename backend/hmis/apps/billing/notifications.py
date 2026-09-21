"""
Money announced once.

Every financially relevant decision the hospital takes — a bill raised, money
taken, a discount, a waiver, a refund, a cancellation, a pay-later — reaches
the desks that work the money through this module and nowhere else.

Three rules it exists to hold:

1. **Server-side, at the service.** The announcement is raised from
   `billing/services.py`, which is the only place a charge, a payment, an
   adjustment or a deferral is ever created. A view cannot forget to
   announce, and a path that never goes near a view — the laboratory raising
   its own charge, the pharmacy raising one on dispensing — announces
   identically. The React app is told nothing it then has to re-tell.

2. **Once.** One event, one call, at the point the money actually moved. A
   service that loops (discounting a whole balance) announces the total, not
   one notification per charge; a view that calls a service does not announce
   a second time on top of it.

3. **Money only.** The message carries the patient's name and hospital
   number, the amount, what was done, the reference, who did it and what is
   still owed. It never carries a diagnosis, a drug, a test name or anything
   else off the chart — `Charge.description` is a billed service ("Laboratory:
   FBC"), which is the line on the bill the patient is holding and is
   deliberately as far as this goes.

Recipients are `FINANCIAL_NOTICE_ROLES` and nobody else, minus whoever
performed the action: a cashier being told about the payment they just took
is noise, and rule 14 says the bell goes to whoever can act on it.
"""
from decimal import Decimal

from apps.accounts.permissions import FINANCIAL_NOTICE_ROLES
from apps.core.services import notify

#: What each event is called on screen, and where the notification lands.
#: Every one of these paths is a route in `frontend/src/main.jsx` —
#: `apps/core/tests/test_notification_links.py` is what proves it.
EVENTS = {
    # A bill has been raised and somebody has to collect it. The title is the
    # one the cash desk has read since before this module existed.
    "charge_raised":    ("To collect", "/billing"),
    "payment_full":     ("Paid in full", "/billing"),
    "payment_part":     ("Part payment", "/billing"),
    "charge_cancelled": ("Bill cancelled", "/billing"),
    # The unused-service workflow. Named apart from a plain refund because the
    # two mean different things to whoever reads the bell: one says money went
    # back, this one says the hospital is no longer billing for the service.
    # Both open Transaction History rather than the Service Cancellations desk:
    # reception is told as well, and a link to a page its own guard bounces is
    # a dead end — every FINANCIAL_NOTICE_ROLE can open the statement.
    "service_cancelled":  ("Service cancelled", "/transactions"),
    "cancel_and_refund":  ("Service cancelled & refunded", "/transactions"),
    "discount":         ("Discount applied", "/waivers"),
    "waiver":           ("Waiver applied", "/waivers"),
    "refund":           ("REFUND", "/transactions"),
    "deferral":         ("Pay later approved", "/outstanding"),
}


def _money(value):
    return f"{Decimal(str(value or 0)):,.2f}"


def _who(user):
    if user is None:
        return "the system"
    return user.get_full_name() or user.username


def recipients(actor=None):
    """
    The desks that work the money, minus whoever just did the thing.

    Inactive accounts are skipped — a notification to somebody who has left
    is an unread count nobody will ever clear.
    """
    from apps.accounts.models import User

    queryset = User.objects.filter(role__in=FINANCIAL_NOTICE_ROLES, is_active=True)
    if actor is not None and actor.pk:
        queryset = queryset.exclude(pk=actor.pk)
    return queryset


def announce(*, event, patient, amount, actor, detail="", reference="", outstanding=None):
    """
    Tell the money desks that this patient's financial position changed.

    `detail` is the billed line or the reason; `reference` is whatever the
    reader would quote back — a charge description, a payment method, a
    receipt reference. `outstanding` is the patient's balance *after* the
    action, included wherever the service can cheaply know it.

    Returns the notifications raised, so a caller (or a test) can count them.
    Raises nothing: an announcement must never be able to fail a transaction
    that has already moved money.
    """
    label, action_url = EVENTS[event]
    title = f"{label}: {patient.display_name}"

    parts = [f"{_money(amount)}"]
    if detail:
        parts.append(str(detail))
    parts.append(f"by {_who(actor)}")
    if reference:
        parts.append(str(reference))
    number = getattr(patient, "patient_number", "") or ""
    head = f"{number} — " if number else ""
    message = head + " · ".join(parts) + "."
    if outstanding is not None:
        message += f" Outstanding balance {_money(outstanding)}."

    raised = []
    for staff in recipients(actor):
        note = notify(recipient=staff, title=title, message=message,
                      category="billing", action_url=action_url)
        if note is not None:
            raised.append(note)
    return raised
