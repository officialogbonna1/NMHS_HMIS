"""
What a referral *asked for*, and the money that follows from asking.

The referral itself — who was sent where, who was told, what came back — is
`workflow/views.py` and is untouched by this module. What lives here is the
one thing the laboratory had and the other units did not: a doctor naming a
configured, priced service, and the charge being raised there and then
(rule 24). The patient walks to the counter already billed rather than being
asked what they were sent for.

It is `laboratory.services.add_tests` at the size imaging actually needs, and
it reuses the same billing chokepoint rather than repeating it:
`billing.services.add_charge` raises every charge, `billing.departments`
attributes it, and `billing.notifications.announce` tells the cash desk once
about the whole request rather than once per examination.
"""
from decimal import Decimal

from django.db import transaction

from apps.billing.departments import department_for_source
from apps.billing.notifications import announce
from apps.billing.services import add_charge, refresh_ledger

from .models import RouteService

#: What a route of each purpose orders from the price list. A purpose that is
#: not here orders nothing, which is every purpose that worked this way
#: before: a vitals hand-off and a consultation are not billable services, and
#: the laboratory raises its own charges through its order (rule 24).
PURPOSE_CATEGORY = {
    "ultrasound": "ultrasound",
}

#: How the charge describes itself on the patient's bill. The unit, then what
#: was done — the same shape as the laboratory's "Laboratory: FBC", because
#: it is the line the patient reads at the window.
CATEGORY_LABEL = {
    "ultrasound": "Ultrasound",
    "eye": "Eye clinic",
    "procedure": "Procedure",
}


def category_for(route):
    """The price-list category this referral may order from, or None."""
    return PURPOSE_CATEGORY.get(route.purpose)


@transaction.atomic
def request_services(*, route, items, author=None, notify=True):
    """
    Put the examinations a clinician asked for onto a referral, and bill them.

    Two things happen at once and both are about time — the same two the
    laboratory settled. The catalogue row is **snapshotted** onto the request,
    so re-pricing the examination tomorrow cannot rewrite what this patient
    was quoted. And the **charge is raised now**, because ordering the study
    is what creates the debt; nobody should have to retype it at the counter,
    and a scan cannot be run without appearing on a bill.

    Asking for the same examination twice on one referral adds nothing: the
    row already exists, so the patient is not billed twice for a mis-click.

    Returns the rows created.
    """
    category = category_for(route)
    added, raised = [], []
    for item in items:
        if category and item.category != category:
            # A referral orders from its own unit's list. Anything else is a
            # miscategorised request, and billing it here would attribute the
            # money to the wrong department.
            continue
        row, made = RouteService.objects.get_or_create(
            route=route, item=item,
            defaults={"name": item.name, "unit_price": item.price, "requested_by": author},
        )
        if not made:
            continue
        charge = _raise_charge_for(row, category=item.category, author=author)
        if charge is not None:
            raised.append(charge)
        added.append(row)

    if raised and notify:
        patient = route.visit.patient
        label = CATEGORY_LABEL.get(category, "Service")
        announce(
            event="charge_raised", patient=patient,
            amount=sum((charge.amount for charge in raised), Decimal("0")),
            actor=author or route.routed_by,
            detail=f"{label} — {len(raised)} service(s) requested",
            outstanding=refresh_ledger(patient).outstanding_balance,
        )
    return added


def _raise_charge_for(row, *, category, author=None):
    """
    The money side of ordering a study: one charge, priced from the snapshot.

    Per examination rather than per referral, because a patient can settle the
    abdominal scan today and the Doppler on Friday, and the desk has to be
    able to take them separately. `notify=False` on each: the caller announces
    the request once, so a referral for three scans is one line on the cash
    desk's bell rather than three.
    """
    if row.charge_id or row.unit_price <= 0:
        return None
    route = row.route
    charge = add_charge(
        patient=route.visit.patient,
        description=f"{CATEGORY_LABEL.get(category, 'Service')}: {row.name}",
        amount=row.unit_price,
        created_by=author or route.routed_by,
        # Resolved from the source type at the chokepoint anyway (rule 33);
        # named here so the call site reads as what it is.
        department=department_for_source(category),
        source_type=category,
        source_id=row.pk,
        notify=False,
    )
    row.charge = charge
    row.save(update_fields=["charge", "updated_at"])
    return charge
