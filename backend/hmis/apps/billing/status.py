"""
Where a *service's* money stands, rendered once.

Rule 12 already says what a charge is worth and `Charge.settlement_status`
already decides what that means — unpaid, partial, paid, waived, deferred,
cancelled — from the charge's own figures rather than a stored flag. Nothing
here re-decides any of that, and nothing here writes anything.

What this module is for is the *second* half of that sentence: several
departments have to read the answer, and each one had started spelling it
out for itself. `laboratory.serializers.LabOrderTestSerializer.get_billing`
and `workflow.serializers.RouteServiceSerializer.get_billing` were the same
twenty lines twice, and the next unit to order a priced service would have
been the third copy. A bench and an imaging station disagreeing about what
"₦8,000, ₦5,000 paid" is called is exactly the confusion the patient pays
for.

So: one shape on the wire, one vocabulary, read off the existing charge.

    {"billed": true, "charge": 41, "status": "partial",
     "label": "PARTIALLY PAID", "requires_payment": true,
     "amount": "8000.00", "discounted": "0.00", "waived": "0.00",
     "payable": "8000.00", "paid": "5000.00", "outstanding": "3000.00",
     "deferred": false}

`requires_payment` is **derived every time it is read** — it is `status`
restated for a screen, not a new piece of state, and there is deliberately
no column, no cache and no `can_proceed` flag behind it (rule 7 of the brief
this was written to, and the same reasoning rule 25 applies to
`settlement_status`). Money moves through `billing/services.py`; the answer
here changes because the charge did.

It is a **reading, never a gate**. The laboratory runs a sample that has been
drawn and imaging scans the patient on the couch; the cash desk chases the
balance (rules 24 and 51). What the unit gets from this is that it knows,
rather than assumes.
"""
from decimal import Decimal

#: What each settlement state is called wherever a department reads it. The
#: wording is the one `frontend/src/components/refundPolicy.js` already put on
#: the Service Cancellations desk — "PARTIALLY PAID", not "Part paid" on one
#: screen and "Partial" on the next — and `components/billingStatus.js` is the
#: frontend's copy of this same table.
LABELS = {
    "unbilled": "NOT BILLED",
    "unpaid": "UNPAID",
    "partial": "PARTIALLY PAID",
    "paid": "PAID",
    # A waiver is not an unpaid bill and must never read as one: the hospital
    # has decided nobody will collect it, so there is nothing for the patient
    # to do and nothing for the unit to wait for.
    "waived": "NO PAYMENT REQUIRED",
    # Owed, but somebody with the authority said the patient may proceed
    # (rule 26). Still money on the books — never a kind of paid.
    "deferred": "PAY LATER",
    "cancelled": "CANCELLED",
}

#: Money is still owed on this service and nobody has authorised proceeding.
#: This is the only state a screen should shout about.
OWING = frozenset({"unpaid", "partial"})

#: Nothing further will be collected: paid, written off, or withdrawn.
SETTLED = frozenset({"paid", "waived", "cancelled"})


def label_for(status):
    return LABELS.get(status, str(status or "").upper())


def _money(value):
    return f"{Decimal(str(value or 0)):.2f}"


def unbilled(amount=0):
    """
    A service that raised no charge — the eye clinic, a procedure, a route
    with nothing priced behind it, or a catalogue row priced at zero.

    Deliberately *not* "unpaid": there is no bill, so there is nothing owed
    and nothing for the counter to collect. The amount is carried anyway,
    because the snapshot on the request is still what it would have cost.
    """
    return {
        "billed": False, "status": "unbilled", "label": LABELS["unbilled"],
        "requires_payment": False, "amount": _money(amount), "discounted": "0.00",
        "waived": "0.00", "payable": _money(amount), "paid": "0.00",
        "outstanding": "0.00", "deferred": False,
        # Carried at the same keys as a billed service so a screen reads one
        # shape whether or not a charge was raised.
        "description": "", "department": "", "billed_at": None,
    }


def service_billing(charge, *, fallback_amount=0):
    """
    One service's money, read off the charge it raised.

    `fallback_amount` is the order-time price the request itself carries, used
    only when no charge was raised — never to *price* a charge that exists,
    because re-pricing the catalogue must not rewrite an old bill (rule 21).
    """
    if charge is None:
        return unbilled(fallback_amount)
    status = charge.settlement_status
    return {
        "billed": True,
        "charge": charge.pk,
        "status": status,
        "label": label_for(status),
        "requires_payment": status in OWING,
        "amount": _money(charge.amount),
        "discounted": _money(charge.amount_discounted),
        "waived": _money(charge.amount_waived),
        "payable": _money(charge.payable),
        "paid": _money(charge.amount_paid),
        "outstanding": _money(max(charge.outstanding, Decimal("0"))),
        "deferred": charge.active_deferral is not None,
        "description": charge.description,
        "department": charge.department.name if charge.department_id else "",
        "billed_at": charge.created_at,
    }


def _overall(charges, outstanding):
    """
    One word for a basket of services, and it is the *worst* of them.

    A patient who has paid for two tests and not the third has not paid: an
    order that read "PAID" because most of it was would be exactly the
    mistake that lets an unpaid service through. Each service keeps its own
    status beside this (rule 12 of the brief: never collapse them into one
    boolean) — this is only the headline.
    """
    if not charges:
        return "unbilled"
    states = {charge.settlement_status for charge in charges}
    if outstanding > 0:
        if states & OWING:
            return "partial" if any(c.amount_paid > 0 for c in charges) else "unpaid"
        # Everything still owing is authorised to be paid later.
        return "deferred"
    if states == {"cancelled"}:
        return "cancelled"
    if states <= {"waived", "cancelled"}:
        return "waived"
    return "paid"


def summarise(charges):
    """
    The money on a whole request — an order of five tests, a referral for
    three scans — summed from the charges it raised and nothing else.

    Cancelled charges are left out of the arithmetic (the hospital withdrew
    the bill, rule 38) but still counted, so a station can say so.
    """
    charges = [charge for charge in charges if charge is not None]
    live = [charge for charge in charges if charge.status != "cancelled"]
    total = sum((c.amount for c in live), Decimal("0"))
    paid = sum((c.amount_paid for c in live), Decimal("0"))
    discounted = sum((c.amount_discounted for c in live), Decimal("0"))
    waived = sum((c.amount_waived for c in live), Decimal("0"))
    outstanding = sum((max(c.outstanding, Decimal("0")) for c in live), Decimal("0"))
    deferred = [c for c in live if c.active_deferral is not None]
    status = _overall(charges, outstanding)
    return {
        "billed": bool(charges),
        "status": status,
        "label": label_for(status),
        "requires_payment": status in OWING,
        "count": len(charges),
        "cancelled_count": len(charges) - len(live),
        "total": _money(total),
        "paid": _money(paid),
        "discounted": _money(discounted),
        "waived": _money(waived),
        "outstanding": _money(outstanding),
        "settled": bool(charges) and outstanding <= 0,
        "deferred": bool(deferred),
    }


def charges_for_route(route):
    """
    Every charge this one referral raised, whichever unit raised it.

    Two units order priced services off a route today and they keep their
    orders in different places for good reasons (rule 51): imaging's is a
    `RouteService` row on the route, the laboratory's is a `LabOrderTest` on
    the order the route opened. A station should not have to know which — it
    asks the route what it cost the patient.
    """
    charges = [service.charge for service in route.services.all() if service.charge_id]
    order = getattr(route, "lab_order", None)
    if order is not None:
        charges += [item.charge for item in order.items.all() if item.charge_id]
    return charges


def route_billing(route):
    """The headline figure a queue row shows: what this referral costs and
    whether the patient has settled it."""
    return summarise(charges_for_route(route))
