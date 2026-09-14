"""
Read-only checks on the money.

Written to answer one question properly — *can a charge hold money that no
`PaymentAllocation` accounts for?* — and to keep answering it after every
deployment, rather than once in a conversation. Nothing here writes.
`manage.py billing_integrity` prints it; `tests/test_payment_allocation_integrity.py`
holds that no production path creates what it looks for.

The comparisons are made in Python on quantised `Decimal`s, not in SQL: SQLite
does its arithmetic in floating point, so `1499.99 + 500.00 > 1999.99` can be
true in the database and would report a charge that is perfectly in order.
"""
from decimal import Decimal

from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import Charge, PatientLedger, Payment, PaymentAllocation, Refund, RefundAllocation
from .services import ledger_totals

CENT = Decimal("0.01")
_MONEY = DecimalField(max_digits=14, decimal_places=2)


def _money(value):
    return Decimal(str(value or 0)).quantize(CENT)


def _sum_per(model, field):
    """What `model` rows add up to per outer row, as one correlated subquery."""
    rows = (model.objects.filter(**{field: OuterRef("pk")}).values(field)
            .annotate(total=Sum("amount")).values("total")[:1])
    return Coalesce(Subquery(rows, output_field=_MONEY), Value(Decimal("0"), output_field=_MONEY))


def untraceable_paid_charges():
    """
    Charges whose `amount_paid` is more than their payment allocations, less
    what has been refunded off them, can explain.

    `has_allocation` separates the two shapes: no allocation at all (the money
    predates allocations entirely), or some allocation but not enough (the
    backfill in `billing/0014` reconstructed less than the charge shows).
    """
    found = []
    queryset = (Charge.objects.filter(amount_paid__gt=0)
                .annotate(allocated=_sum_per(PaymentAllocation, "charge"),
                          refunded=_sum_per(RefundAllocation, "charge"))
                .order_by("pk"))
    for charge in queryset:
        paid = _money(charge.amount_paid)
        allocated = _money(charge.allocated)
        traceable = allocated - _money(charge.refunded)
        if paid > traceable:
            found.append({
                "charge": charge.pk, "patient": charge.patient_id, "status": charge.status,
                "amount_paid": paid, "traceable": traceable, "untraceable": paid - traceable,
                "has_allocation": allocated > 0,
            })
    return found


def unallocated_payments():
    """Payments whose allocations fall short of the amount that was taken."""
    found = []
    # A walk-in POS customer's payment has no patient and so no charge to be
    # allocated to — by design, not a gap (`record_pos_payment`).
    queryset = (Payment.objects.exclude(patient__isnull=True)
                .annotate(allocated=_sum_per(PaymentAllocation, "payment")).order_by("pk"))
    for payment in queryset:
        amount, allocated = _money(payment.amount), _money(payment.allocated)
        if allocated < amount:
            found.append({"payment": payment.pk, "patient": payment.patient_id,
                          "amount": amount, "allocated": allocated,
                          "unallocated": amount - allocated,
                          "taken_at": payment.created_at})
    return found


def negative_ledgers():
    """Patients the ledger says are in credit — something rule 12 says no path may do."""
    return [
        {"patient": ledger.patient_id, "outstanding": _money(ledger.outstanding_balance)}
        for ledger in PatientLedger.objects.order_by("patient_id")
        if _money(ledger.outstanding_balance) < 0
    ]


def ledger_drift():
    """Ledgers whose stored totals differ from what their rows add up to now."""
    drifted = []
    for ledger in PatientLedger.objects.select_related("patient").order_by("patient_id"):
        stored = tuple(_money(v) for v in (ledger.total_charges, ledger.total_payments,
                                            ledger.total_adjustments))
        actual = tuple(_money(v) for v in ledger_totals(ledger.patient))
        if stored != actual:
            drifted.append({"patient": ledger.patient_id, "stored": stored, "actual": actual})
    return drifted


def report():
    """Every check, plus the counts a reader needs to put the findings in scale."""
    untraceable = untraceable_paid_charges()
    return {
        "charges": Charge.objects.count(),
        "paid_charges": Charge.objects.filter(amount_paid__gt=0).count(),
        "cancelled_charges": Charge.objects.filter(status="cancelled").count(),
        "payments": Payment.objects.count(),
        "refunds": Refund.objects.count(),
        "untraceable_paid_charges": untraceable,
        "allocation_less_paid_charges": [row for row in untraceable if not row["has_allocation"]],
        "unallocated_payments": unallocated_payments(),
        "negative_ledgers": negative_ledgers(),
        "ledger_drift": ledger_drift(),
    }
