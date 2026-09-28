"""
Test helpers for the pharmacy.

A prescription is billed when it is written and dispensed only once *its own*
bill lets it go (`services.dispensing_clearance`). A test about stock, FEFO or
concurrency — not about money — still has to settle that bill first, and this
is the one way it does: an ordinary `Payment` through
`billing.services.record_payment(charge=…)`, the same path the cash desk takes,
never a flag set by hand.
"""
from apps.billing.services import record_payment


def pay_for(prescription, *, by):
    """
    Settle this script line's bill in full at the counter. Returns it, refreshed.

    A line written straight into the table (`Prescription.objects.create`) has
    no bill — exactly a script written before prescriptions were billed — so it
    is billed first, the way the counter's "Raise bill" does.
    """
    from .services import bill_prescription

    prescription.refresh_from_db()
    if prescription.charge_id is None and prescription.status == "pending":
        bill_prescription(prescription=prescription, actor=by)
        prescription.refresh_from_db()
    charge = prescription.charge
    if charge is not None and charge.status in ("unpaid", "partial"):
        due = charge.amount - charge.amount_paid - charge.amount_discounted - charge.amount_waived
        if due > 0:
            record_payment(patient=prescription.patient, amount=due, received_by=by,
                           charge=charge)
    prescription.refresh_from_db()
    return prescription


def prescribe_and_pay(*, by, **kwargs):
    """`create_prescription(**kwargs)`, then its bill paid — ready to dispense."""
    from .services import create_prescription

    return pay_for(create_prescription(**kwargs), by=by)
