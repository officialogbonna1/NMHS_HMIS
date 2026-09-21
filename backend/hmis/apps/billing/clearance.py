"""
Telling the unit that was waiting that the bill has been settled.

Rule 35 already announces every financial event to the desks that *work the
money* — a charge raised, a payment taken, a discount, a waiver. This is the
other direction, and it is the one thing that was missing from the round
trip: the patient pays at the counter, and the bench or the scanner room has
no way to learn it except by looking again.

Three things keep it from becoming a second notification system:

1. **It is `core.services.notify`**, through the same `Notification` rows,
   the same bell, the same unread count, the same archive. No new model, no
   new poll, no new delivery path.

2. **It is raised from `billing/services.py`** — the chokepoint that already
   announces to the cash desk — so it fires whichever door the money came
   through: a payment at the front desk, a payment at the pharmacy counter,
   a waiver, a discount that finishes the bill off. A view cannot forget it,
   and there is no flag anywhere that could fall out of step, because the
   trigger *is* the charge changing state.

3. **It goes to whoever can act on it, and nobody else** (rule 14). The
   referral the charge came from already knows who is waiting on it, so this
   asks `workflow.views.route_targets` rather than inventing a second idea of
   who a unit is: the named assignee if somebody has claimed the work, else
   the pool that works that queue. A service with no referral behind it —
   a consultation fee, a card, a walk-in sale — tells nobody, because there
   is no unit holding a patient for it.

It says only what the unit already reads on its own worklist: the patient,
the service as it is described on the bill, and the amount cleared. No
diagnosis, no result, no balance on the patient's other bills — the same
boundary rule 35 draws in the opposite direction.
"""
from decimal import Decimal

from apps.core.services import notify

#: Which queue a settled charge unblocks, by the `source_type` the service
#: stamped on it. Only the units that order a priced service off a referral
#: are here, which is the laboratory (rule 24) and imaging (rule 51); the
#: categories beneath them are the price-list categories `RouteService` uses,
#: so the day the eye clinic starts ordering priced examinations it is
#: already covered. A source type that is not here — a consultation fee, a
#: card, a prescription, a POS sale — has no unit waiting on payment and
#: therefore nobody to tell.
SOURCE_PURPOSE = {
    "lab_test": "laboratory",
    "ultrasound": "ultrasound",
    "eye": "eye",
    "procedure": "procedure",
}


def _money(value):
    return f"{Decimal(str(value or 0)):,.2f}"


def route_for(charge):
    """
    The referral this charge was raised against, or None.

    Read back through the row that raised it — `RouteService` for an imaging
    request, `LabOrderTest` for a test — rather than from `source_id`, so a
    charge can never be matched to a row of another kind that happens to
    share a primary key.
    """
    service = charge.route_services.select_related("route__visit__patient").first()
    if service is not None:
        return service.route
    item = charge.lab_order_tests.select_related("order__route__visit__patient").first()
    if item is not None and item.order.route_id:
        return item.order.route
    return None


def announce_cleared(charge, *, actor=None):
    """
    A service's bill has been settled — tell the unit holding the patient.

    Called only where a charge has actually just moved into a settled state;
    working out *that* it moved is the caller's job, because only the caller
    knows what it looked like before. Raises nothing: an announcement must
    never be able to fail a transaction that has already taken money.

    Returns the notifications raised, so a test can count them.
    """
    if SOURCE_PURPOSE.get(charge.source_type) is None:
        return []
    route = route_for(charge)
    if route is None or route.status in {"completed", "cancelled"}:
        # Nothing is waiting: the work is already done, or the patient left.
        return []

    # Who is waiting on this queue. `route_targets` is the one definition —
    # the assignee, else the pool — and it already excludes the doctor who
    # raised the referral, which is right here too: she ordered the scan, she
    # is not the one who performs it.
    from apps.workflow.views import route_targets

    patient = route.visit.patient
    number = getattr(patient, "patient_number", "") or ""
    head = f"{number} — " if number else ""
    # Settled with no money taken — waived outright, or discounted to
    # nothing. The distinction that matters to the unit is not which column
    # forgave it but that the patient owes nothing and has nothing to go and
    # do, so both are said the same way. "Payment cleared · ₦0.00 paid"
    # would read as a mistake.
    written_off = charge.amount_paid <= 0
    if written_off:
        forgiven = charge.amount_waived + charge.amount_discounted
        title = f"No payment required: {patient.display_name}"
        message = (f"{head}{charge.description} · ₦{_money(forgiven or charge.amount)}"
                   " written off"
                   + (f" by {actor.get_full_name() or actor.username}." if actor else "."))
    else:
        title = f"Payment cleared: {patient.display_name}"
        message = (f"{head}{charge.description} · ₦{_money(charge.amount_paid)} paid"
                   f" · nothing outstanding. The patient can be seen.")

    raised = []
    for staff in route_targets(route):
        note = notify(recipient=staff, title=title, message=message,
                      # The event is a bill being settled, so it is billing's
                      # switch that governs it — one category, one place an
                      # administrator turns this kind of traffic down.
                      category="billing",
                      action_url=_station_for(route))
        if note is not None:
            raised.append(note)
    return raised


def _station_for(route):
    """Where the recipient goes to act on it — the unit's own station, which
    is where their queue is, never the generic queue page."""
    from apps.workflow.views import PURPOSE_STATION

    return PURPOSE_STATION.get(route.purpose, "/queue")
