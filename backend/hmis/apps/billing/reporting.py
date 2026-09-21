"""
The hospital's money, aggregated in the database.

Everything the financial dashboards show is computed here, from the rows that
already are the source of truth — `Charge` for what was billed, `Payment` for
what was collected, `PaymentAllocation` for which charge each payment settled,
and `PatientLedger` for what is still owed. Nothing is recomputed on the
client, and no figure is derived from a display value.

**Two bases, never blended.** A financial period can be read two ways and this
module answers both, separately and with the basis named:

- **Collections (cash basis)** — money that actually arrived between the two
  dates. `Payment.created_at` decides. This is Total Revenue: a discount is
  not revenue, a waiver is not revenue, and a bill raised today that nobody
  paid is not revenue.
- **Charges (cohort basis)** — the bills *raised* between the two dates and
  what has become of them since. `Charge.created_at` decides. This is the only
  basis on which the reconciliation closes exactly:

      gross − discounts − waivers            = net due
      net due − collected against those bills = outstanding

  Mixing the two (this month's bills against this month's cash) is how a
  reconciliation ends up two thousand naira out with nobody able to say why,
  so the two blocks are kept apart and each says which it is.

**Where a department comes from.** `Charge.department` is nullable and, in
practice, almost never set — the laboratory and the pharmacy raise their
charges through services that never filled it in. What *is* always set is
`Charge.source_type`: the automatic paths stamp `lab_test` and `prescription`,
and the billing counter posts the `BillingItem` category it billed from. So
the origin of the money is the source type, with the department foreign key as
a fallback for anything unmapped and "Other / Unclassified" behind that. A
charge is never silently dropped from the breakdown; the Total row is the
whole period either way.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import models
from django.db.models import Case, Count, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from .departments import DEPARTMENT_CODES, REVENUE_DEPARTMENTS, SOURCE_DEPARTMENT
from .models import (Adjustment, Charge, Payment, PaymentAllocation, PaymentDeferral,
                     PatientLedger, Refund, RefundAllocation)

ZERO = Decimal("0.00")


# --------------------------------------------------------------------------
# The period
# --------------------------------------------------------------------------

PRESETS = ["today", "yesterday", "week", "month", "last_month", "year", "custom"]


@dataclass(frozen=True)
class Period:
    """A closed range of days, plus the half-open datetime bounds to filter on.

    The bounds are what the queries use: `created_at__gte=start` /
    `created_at__lt=end` reads a b-tree index, where `created_at__date__range`
    wraps the column in a function and cannot. At 26 rows that is invisible;
    at 260,000 it is the difference between a dashboard and a timeout.
    """
    preset: str
    start: date
    end: date          # inclusive, the way a person reads a date range
    label: str

    @property
    def bounds(self):
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(self.start, time.min), tz)
        end = timezone.make_aware(datetime.combine(self.end + timedelta(days=1), time.min), tz)
        return start, end

    def filter(self, queryset, field="created_at"):
        start, end = self.bounds
        return queryset.filter(**{f"{field}__gte": start, f"{field}__lt": end})

    def as_dict(self):
        return {"preset": self.preset, "label": self.label,
                "from": self.start.isoformat(), "to": self.end.isoformat()}


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f"'{value}' is not a date. Use YYYY-MM-DD.")


def _month_label(day):
    return day.strftime("%B %Y")


def resolve_period(preset=None, date_from=None, date_to=None, *, today=None):
    """
    Turn a preset (or a pair of dates) into the range every figure is filtered
    by. An unknown preset falls back to today rather than to "everything" —
    a mistyped parameter must never quietly widen a financial report.
    """
    today = today or timezone.localdate()
    preset = (preset or "today").strip().lower()

    start = _parse_date(date_from)
    end = _parse_date(date_to)
    if preset == "custom" or (start and end and preset not in PRESETS):
        preset = "custom"

    if preset == "custom":
        if not start or not end:
            raise ValueError("A custom range needs both a from and a to date.")
        if end < start:
            start, end = end, start
        label = (f"{start:%d %b %Y}" if start == end
                 else f"{start:%d %b %Y} – {end:%d %b %Y}")
        return Period("custom", start, end, label)

    if preset == "yesterday":
        day = today - timedelta(days=1)
        return Period("yesterday", day, day, f"Yesterday, {day:%d %b %Y}")
    if preset == "week":
        monday = today - timedelta(days=today.weekday())
        return Period("week", monday, today, f"This week, from {monday:%d %b}")
    if preset == "month":
        first = today.replace(day=1)
        return Period("month", first, today, _month_label(today))
    if preset == "last_month":
        last_day = today.replace(day=1) - timedelta(days=1)
        return Period("last_month", last_day.replace(day=1), last_day, _month_label(last_day))
    if preset == "year":
        return Period("year", today.replace(month=1, day=1), today, f"{today.year}")

    return Period("today", today, today, f"Today, {today:%d %b %Y}")


# --------------------------------------------------------------------------
# Departments
# --------------------------------------------------------------------------

# The registry itself lives in `billing/departments.py`, because the same
# seven units are what `add_charge` attributes a new charge to and what the
# seed migration creates. Reporting reads it rather than keeping a second
# copy — a report grouping by one list while charges are attributed by another
# is how a department's revenue quietly goes missing.
DEPARTMENTS = REVENUE_DEPARTMENTS

OTHER = ("other", "Other / Unclassified")

# The order the breakdown is presented in, so a table does not reshuffle
# itself between two periods because one department happened to earn more.
DEPARTMENT_ORDER = [*DEPARTMENT_CODES, OTHER[0]]


def department_for(source_type, department_code=None, department_name=None):
    """
    Which department a charge's money belongs to, for reporting.

    **The same precedence `add_charge` applies when it writes the row**, so a
    charge raised today reports under the department stored on it, and the two
    can never disagree:

    1. the authoritative `source_type` map;
    2. the department foreign key — which now covers `investigation`, where
       the diagnostics catalogue's own department is better than a guess, and
       any charge the counter attributed by hand;
    3. "Other / Unclassified".

    Step 2 stays because the FK is the only attribution the **19 historical
    charges** have, and because a department the map has never heard of gets a
    bucket of its own rather than being folded into "Other" — a hospital that
    adds a unit should see it appear. Nothing here infers a department from a
    description, a route, a timestamp or a naming convention: a charge with
    neither field stays "Other / Unclassified", which is the truth about it.
    """
    mapped = SOURCE_DEPARTMENT.get((source_type or "").strip().lower())
    if mapped:
        return mapped
    if department_code:
        mapped = SOURCE_DEPARTMENT.get(department_code.strip().lower())
        if mapped:
            return mapped
        return (f"department:{department_code}", department_name or department_code)
    return OTHER


def _blank_department(key, label):
    return {
        "key": key, "label": label,
        # Cohort: bills raised in the period.
        # `returns` is goods a registered patient brought back to the POS: it
        # is summed below and subtracted from `net_due`, so every row needs it.
        "gross": ZERO, "discounts": ZERO, "waivers": ZERO, "returns": ZERO, "net_due": ZERO,
        "collected": ZERO, "part_payments": ZERO, "outstanding": ZERO, "charges": 0,
        # Cash: money that arrived in the period, whenever the bill was raised,
        # and what of it went back out again.
        "received": ZERO, "refunded": ZERO, "net_received": ZERO,
    }


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

def _money(value):
    return (value or ZERO).quantize(Decimal("0.01")) if isinstance(value, Decimal) else Decimal(value or 0)


def collections(period, *, received_by=None):
    """
    Money that actually arrived between the two dates — Total Revenue.

    A discount or a waiver never appears here: nobody handed anything over.
    Part payments do, because the money is real; they are counted again
    separately so "how much of today's takings left a bill still open?" has an
    answer.
    """
    payments = period.filter(Payment.objects.all())
    totals = payments.aggregate(
        total=Coalesce(Sum("amount"), ZERO), transactions=Count("id"),
    )

    by_method = [
        {"key": row["method"], "label": dict(Payment.METHOD).get(row["method"], row["method"]),
         "amount": _money(row["amount"]), "count": row["count"]}
        for row in payments.values("method").annotate(
            amount=Coalesce(Sum("amount"), ZERO), count=Count("id")).order_by("-amount")
    ]
    by_channel = [
        {"key": row["channel"], "label": dict(Payment.CHANNEL).get(row["channel"], row["channel"]),
         "amount": _money(row["amount"]), "count": row["count"]}
        for row in payments.values("channel").annotate(
            amount=Coalesce(Sum("amount"), ZERO), count=Count("id")).order_by("-amount")
    ]

    # A part payment is money taken against a bill that is *still* not settled.
    # `Charge.status` is maintained by billing.services on every allocation,
    # discount and waiver, so "unpaid or partial" is exactly "still owing".
    allocations = period.filter(PaymentAllocation.objects.all(), field="payment__created_at")
    part = allocations.filter(charge__status__in=["unpaid", "partial"]).aggregate(
        total=Coalesce(Sum("amount"), ZERO), count=Count("charge", distinct=True))

    # Money handed back in the period. A refund is not a negative payment —
    # the `Payment` row it answers is untouched and still counted in `total`,
    # which is the gross the drawer actually took. `net` is what the hospital
    # kept, and is the figure a period is judged on:
    #
    #     gross payments received − refunds = net revenue retained
    #
    # Never blended into `total`: a reconciliation that cannot see both halves
    # cannot explain a shortfall.
    refunded = period.filter(Refund.objects.all()).aggregate(
        total=Coalesce(Sum("amount"), ZERO), count=Count("id"))

    block = {
        "total": _money(totals["total"]),
        "transactions": totals["transactions"],
        "refunds": _money(refunded["total"]),
        "refunds_count": refunded["count"],
        "net": _money(totals["total"]) - _money(refunded["total"]),
        "part_payments": _money(part["total"]),
        "part_paid_charges": part["count"],
        "by_method": by_method,
        "by_channel": by_channel,
    }
    if received_by is not None:
        block["my_desk"] = _money(payments.filter(received_by=received_by).aggregate(
            v=Coalesce(Sum("amount"), ZERO))["v"])
        block["my_desk_transactions"] = payments.filter(received_by=received_by).count()
    return block


def _charge_rows(period):
    """
    One grouped query over the charges raised in the period.

    Grouped by source type and department, which is a handful of rows however
    many charges there are — the folding into departments below happens over
    that handful, never over the charges themselves.

    Cancelled charges are excluded: they were withdrawn, and the ledger has
    never counted them either.
    """
    still_owing = Q(status__in=["unpaid", "partial"])
    return (
        period.filter(Charge.objects.exclude(status="cancelled"))
        .values("source_type", "department__code", "department__name")
        .annotate(
            gross=Coalesce(Sum("amount"), ZERO),
            discounts=Coalesce(Sum("amount_discounted"), ZERO),
            waivers=Coalesce(Sum("amount_waived"), ZERO),
            # Goods brought back to the pharmacy POS by a registered patient
            # (`Charge.amount_returned`). Zero on every other kind of bill.
            returns=Coalesce(Sum("amount_returned"), ZERO),
            collected=Coalesce(Sum("amount_paid"), ZERO),
            # Money already taken on a bill that is still open.
            part_payments=Coalesce(Sum(Case(
                When(still_owing, then=F("amount_paid")),
                default=Value(ZERO), output_field=models.DecimalField(max_digits=14, decimal_places=2),
            )), ZERO),
            charges=Count("id"),
        )
    )


def _received_rows(period):
    """Cash collected in the period, grouped by the department it settled."""
    return (
        period.filter(PaymentAllocation.objects.all(), field="payment__created_at")
        .values("charge__source_type", "charge__department__code", "charge__department__name")
        .annotate(received=Coalesce(Sum("amount"), ZERO))
    )


def _refunded_rows(period):
    """
    Cash handed back in the period, grouped by the department that had taken
    it — read off the charge each `RefundAllocation` reopened, so a refund is
    attributed exactly where the payment that it answers was attributed.
    """
    return (
        period.filter(RefundAllocation.objects.all(), field="refund__created_at")
        .values("charge__source_type", "charge__department__code", "charge__department__name")
        .annotate(refunded=Coalesce(Sum("amount"), ZERO))
    )


UNATTRIBUTED = ("unattributed", "Unattributed")


def departments(period, *, collected_total=None):
    """
    The department breakdown and its Total row.

    Every column but `received` is on the charge cohort, so the arithmetic
    closes per department and down the Total row alike. `received` is the cash
    that arrived in the period and is what the revenue chart plots — it is the
    one figure here that answers "what did this department take this month",
    as against "what became of this month's bills".

    `collected_total` is the period's payment total. Where it exceeds what the
    allocations account for, the difference is shown as **Unattributed**
    rather than quietly dropped: money taken before allocations were recorded
    has no charge to point at, and a chart whose bars silently fall short of
    the headline revenue figure is worse than one that says which part it
    cannot place. Going forward the gap is nil — every payment is allocated as
    it is taken — so this row only ever shows history.
    """
    buckets = {}

    def bucket(key, label):
        if key not in buckets:
            buckets[key] = _blank_department(key, label)
        return buckets[key]

    for row in _charge_rows(period):
        key, label = department_for(
            row["source_type"], row["department__code"], row["department__name"])
        target = bucket(key, label)
        for field in ("gross", "discounts", "waivers", "returns", "collected", "part_payments"):
            target[field] += _money(row[field])
        target["charges"] += row["charges"]

    for row in _received_rows(period):
        key, label = department_for(
            row["charge__source_type"], row["charge__department__code"],
            row["charge__department__name"])
        bucket(key, label)["received"] += _money(row["received"])

    for row in _refunded_rows(period):
        key, label = department_for(
            row["charge__source_type"], row["charge__department__code"],
            row["charge__department__name"])
        bucket(key, label)["refunded"] += _money(row["refunded"])

    # A walk-in POS customer's payment has no charge for an allocation to
    # place it on, but the sale it settled is the pharmacy's — so that is where
    # it is counted, rather than reading as "Unattributed" beside the chart.
    walk_in_received, walk_in_refunded = _walk_in_received(period), _walk_in_refunded(period)
    if walk_in_received or walk_in_refunded:
        target = bucket(*department_for(POS_SOURCE))
        target["received"] += walk_in_received
        target["refunded"] += walk_in_refunded

    for target in buckets.values():
        target["net_due"] = (target["gross"] - target["discounts"] - target["waivers"]
                             - target["returns"])
        target["outstanding"] = target["net_due"] - target["collected"]
        # What this unit actually kept in the period.
        target["net_received"] = target["received"] - target["refunded"]

    def sort_key(row):
        try:
            return (0, DEPARTMENT_ORDER.index(row["key"]))
        except ValueError:
            return (1, row["label"])

    rows = sorted(buckets.values(), key=sort_key)

    if collected_total is not None:
        placed = sum((row["received"] for row in rows), ZERO)
        unplaced = _money(collected_total) - placed
        if unplaced > 0:
            leftover = _blank_department(*UNATTRIBUTED)
            leftover["received"] = unplaced
            rows.append(leftover)

    total = _blank_department("total", "Total")
    for row in rows:
        for field in ("gross", "discounts", "waivers", "returns", "net_due", "collected",
                      "part_payments", "outstanding", "received", "refunded",
                      "net_received"):
            total[field] += row[field]
        total["charges"] += row["charges"]
    return rows, total


#: A registered patient's purchase at the pharmacy POS till (`sales/services.py`).
POS_SOURCE = "pos_sale"


def _walk_in_received(period):
    """
    Cash a walk-in POS customer paid in the period. No patient, so no charge
    and no allocation — the sale it is linked to is what says it is the
    pharmacy's.
    """
    return _money(period.filter(
        Payment.objects.filter(patient__isnull=True, pos_sale__isnull=False)
    ).aggregate(v=Coalesce(Sum("amount"), ZERO))["v"])


def _walk_in_refunded(period):
    """What went back to walk-in POS customers in the period, through POS returns."""
    return _money(period.filter(
        Refund.objects.filter(patient__isnull=True, payment__pos_sale__isnull=False)
    ).aggregate(v=Coalesce(Sum("amount"), ZERO))["v"])


PHARMACY_CHANNELS = (
    ("dispensing", "Prescription dispensing"),
    ("pos_registered", "POS — registered patients"),
    ("pos_walk_in", "POS — walk-in customers"),
    ("other", "Other pharmacy charges"),
)


def pharmacy_sales(period):
    """
    The Pharmacy department's takings in the period, split by where the money
    came from.

    Not a second revenue figure. It reads the same allocation and refund rows
    `departments()` reads, attributes them with the same `department_for`, and
    adds the same walk-in POS payments — so `total` equals the Pharmacy row of
    the department table, which is made of the same `Payment` and `Refund`
    rows Total Facility Revenue sums. Nothing is counted twice: a registered
    patient's POS payment is placed by its allocation, a walk-in's by its sale.
    """
    pharmacy_key = department_for(POS_SOURCE)[0]
    channels = {key: {"key": key, "label": label, "received": ZERO, "refunded": ZERO, "net": ZERO}
                for key, label in PHARMACY_CHANNELS}

    def channel_for(source_type):
        source = (source_type or "").strip().lower()
        if source == "prescription":
            return "dispensing"
        if source == POS_SOURCE:
            return "pos_registered"
        return "other"

    for row in _received_rows(period):
        key, _ = department_for(row["charge__source_type"], row["charge__department__code"],
                                row["charge__department__name"])
        if key == pharmacy_key:
            channels[channel_for(row["charge__source_type"])]["received"] += _money(row["received"])
    for row in _refunded_rows(period):
        key, _ = department_for(row["charge__source_type"], row["charge__department__code"],
                                row["charge__department__name"])
        if key == pharmacy_key:
            channels[channel_for(row["charge__source_type"])]["refunded"] += _money(row["refunded"])
    channels["pos_walk_in"]["received"] += _walk_in_received(period)
    channels["pos_walk_in"]["refunded"] += _walk_in_refunded(period)

    total = {"key": "total", "label": "Pharmacy total", "received": ZERO, "refunded": ZERO,
             "net": ZERO}
    for channel in channels.values():
        channel["net"] = channel["received"] - channel["refunded"]
        for field in ("received", "refunded", "net"):
            total[field] += channel[field]
    return {"channels": list(channels.values()), "total": total}


def pos_sales(period):
    """
    The pharmacy POS till in the period, as its own block — beside the charge
    cohort and the collections, never blended into either:

        gross − discounts = paid        (what the till charged and took)
        paid − returned   = net         (what the POS kept)

    `paid` is read off the completed sales and `payments` off the `Payment` rows
    those sales wrote, both over the payment's own timestamp, so they are the
    same money counted from each end — `reconciles` says they agree, and
    `payments` is exactly the POS share of `collections.total`. Returns are the
    `Refund` rows POS returns wrote, by when they were paid out.

    A walk-in sale has no charge, so its gross and its discount appear nowhere
    else in the report; a registered patient's also sits in the charge cohort as
    a Pharmacy charge, which is why this block is never added to that one.
    """
    from apps.sales.models import Sale

    rows = (period.filter(Sale.objects.filter(status="completed", payment__isnull=False),
                          field="payment__created_at")
            .values("customer_type")
            .annotate(count=Count("id"), gross=Coalesce(Sum("subtotal"), ZERO),
                      discounts=Coalesce(Sum("discount_amount"), ZERO),
                      paid=Coalesce(Sum("total_amount"), ZERO)))
    returned = (period.filter(Refund.objects.filter(pos_return__isnull=False))
                .values("pos_return__sale__customer_type")
                .annotate(amount=Coalesce(Sum("amount"), ZERO), count=Count("id")))
    payments = _money(period.filter(Payment.objects.filter(pos_sale__isnull=False))
                      .aggregate(v=Coalesce(Sum("amount"), ZERO))["v"])

    def blank(key, label):
        return {"key": key, "label": label, "count": 0, "gross": ZERO, "discounts": ZERO,
                "paid": ZERO, "returned": ZERO, "returns_count": 0, "net": ZERO}

    blocks = {"walk_in": blank("walk_in", "Walk-in customers"),
              "patient": blank("registered", "Registered patients")}
    for row in rows:
        target = blocks.get(row["customer_type"])
        if target is not None:
            target["count"] += row["count"]
            for field in ("gross", "discounts", "paid"):
                target[field] += _money(row[field])
    for row in returned:
        target = blocks.get(row["pos_return__sale__customer_type"])
        if target is not None:
            target["returned"] += _money(row["amount"])
            target["returns_count"] += row["count"]

    total = blank("total", "All POS sales")
    for block in blocks.values():
        block["net"] = block["paid"] - block["returned"]
        for field in ("count", "gross", "discounts", "paid", "returned", "returns_count", "net"):
            total[field] += block[field]
    total.update({
        "walk_in": blocks["walk_in"], "registered": blocks["patient"], "payments": payments,
        "reconciles": (total["paid"] == payments
                       and total["gross"] - total["discounts"] == total["paid"]),
    })
    return total


def adjustments(period):
    """
    The write-off register: what was approved in the period, by kind. Never
    money received — a discount and a waiver are both the hospital deciding
    not to collect.

    This is a *different question* from the `discounts` and `waivers` columns
    in the charge block, and the two can honestly disagree. Those columns are
    `Charge.amount_discounted` / `amount_waived` on the bills raised in the
    period; these are `Adjustment` rows approved in the period. A discount
    granted today against a bill raised last month appears here and not there.

    So does an adjustment with no charge behind it — for instance a row written
    through `POST /api/adjustments/` before that endpoint refused unlinked
    discounts and waivers, and refunds altogether: it credits the patient's
    ledger without reducing any single bill, so no charge column can ever show
    it. `unlinked` is how much
    of the period's register is in that state — reported rather than hidden,
    because it is the one figure that can make the register and the cohort
    disagree with nobody able to say why.
    """
    rows = period.filter(Adjustment.objects.all()).values("kind").annotate(
        amount=Coalesce(Sum("amount"), ZERO), count=Count("id"))
    by_kind = {row["kind"]: row for row in rows}

    def figure(kind, field="amount"):
        row = by_kind.get(kind)
        return _money(row[field]) if row and field == "amount" else (row[field] if row else 0)

    unlinked = period.filter(Adjustment.objects.filter(charge__isnull=True)).aggregate(
        amount=Coalesce(Sum("amount"), ZERO), count=Count("id"))

    return {
        "discounts": figure("discount"), "discounts_count": figure("discount", "count"),
        "waivers": figure("waiver"), "waivers_count": figure("waiver", "count"),
        "refunds": figure("refund"), "refunds_count": figure("refund", "count"),
        # Approved against the patient's ledger rather than against one bill.
        "unlinked": _money(unlinked["amount"]), "unlinked_count": unlinked["count"],
    }


def cancellations(period):
    """
    Bills withdrawn in the period — dated by when they were cancelled, not by
    when they were raised, because "what did we cancel this month?" is the
    question. Reported beside the charge cohort and never inside it: a
    cancelled bill is not revenue that was billed, and the cohort already
    excludes it. A cancellation that predates `cancelled_at` has no date to
    fall in and is not counted in any period.
    """
    totals = period.filter(Charge.objects.filter(status="cancelled"), field="cancelled_at").aggregate(
        count=Count("id"), value=Coalesce(Sum("amount"), ZERO))
    return {"count": totals["count"], "value": _money(totals["value"])}


def outstanding_now():
    """
    What the hospital is owed, in total, as of now — every open balance
    regardless of when it was raised. The debtors list on `/outstanding` is
    the same figure broken down by patient.
    """
    return _money(PatientLedger.objects.aggregate(
        v=Coalesce(Sum(F("total_charges") - F("total_payments") - F("total_adjustments")),
                   ZERO, output_field=models.DecimalField(max_digits=14, decimal_places=2)))["v"])


def transactions(period, *, limit=50):
    """
    The period's charges, one row each, with what has been settled against
    them and how it was paid.

    Patients are identified by UUID and hospital number only — the integer
    primary key is the database's business and never the browser's.

    Everything a row needs is prefetched: the payment methods off the
    allocations, and the open pay-later authorisation, which
    `Charge.settlement_status` would otherwise look up once per row. A
    fifty-row table costs four queries, not fifty-one.
    """
    limit = max(1, min(int(limit or 50), 200))
    queryset = (
        period.filter(Charge.objects.exclude(status="cancelled"))
        .select_related("patient", "department")
        .prefetch_related(
            models.Prefetch(
                "allocations",
                queryset=PaymentAllocation.objects.select_related("payment").order_by("created_at"),
            ),
            models.Prefetch(
                "deferrals",
                queryset=PaymentDeferral.objects.filter(released_at__isnull=True),
                to_attr="open_deferrals",
            ),
        )
        .order_by("-created_at")[:limit]
    )

    methods = dict(Payment.METHOD)
    rows = []
    for charge in queryset:
        key, label = department_for(
            charge.source_type,
            charge.department.code if charge.department_id else None,
            charge.department.name if charge.department_id else None)
        paid_methods = []
        for allocation in charge.allocations.all():
            name = methods.get(allocation.payment.method, allocation.payment.method)
            if name not in paid_methods:
                paid_methods.append(name)
        rows.append({
            "id": charge.pk,
            "time": charge.created_at,
            "patient": charge.patient.display_name,
            "patient_uuid": str(charge.patient.uuid),
            "patient_number": charge.patient.patient_number,
            "department": label,
            "department_key": key,
            "service": charge.description,
            "amount_due": _money(charge.amount),
            "discount": _money(charge.amount_discounted),
            "waiver": _money(charge.amount_waived),
            "amount_paid": _money(charge.amount_paid),
            "balance": _money(charge.balance),
            "methods": paid_methods,
            # The prefetched deferral, so the rule is applied without a query
            # per row. `settlement_with` is the same rule the serializer uses.
            "status": charge.settlement_with(
                charge.open_deferrals[0] if charge.open_deferrals else None),
        })
    return rows


def facility_revenue():
    """
    Total Facility Revenue — all time, the whole facility:

        every successful payment received − every refund processed

    Read from the two authoritative money rows and nothing else. A `Payment`
    is money that arrived: the model has no status, no void and no
    cancellation, the API never edits or deletes one, and a part payment is
    simply a smaller row. A `Refund` is money that went back: every refund path
    (`refund_payment`, `refund_charge`, `cancel_and_refund`) writes one through
    `refund_payment`, and partial refunds are several rows that add up.

    What is deliberately absent: discounts and waivers (never received, so
    never subtracted — taking them off payments would count them twice), bills
    still owed, and services cancelled before anyone paid. Those live on
    `Charge` and `Adjustment`, which this does not read.

    No period, no desk, no user: it is the same figure whatever range the page
    is showing and whoever is looking. Two aggregate queries, whatever the size
    of the history — and over any range that covers all of history it equals
    `collections(period)["net"]`, because it is the same arithmetic.
    """
    received = _money(Payment.objects.aggregate(v=Coalesce(Sum("amount"), ZERO))["v"])
    refunded = _money(Refund.objects.aggregate(v=Coalesce(Sum("amount"), ZERO))["v"])
    return {"received": received, "refunded": refunded, "total": received - refunded}


def financial_report(*, period, received_by=None, transaction_limit=50):
    """
    The whole dashboard payload, in about a dozen aggregate queries whatever
    the size of the transaction history.
    """
    collected = collections(period, received_by=received_by)
    given_away = adjustments(period)
    # The department rows carry the period's takings, so they are given the
    # collected total and can account for every naira of it.
    department_rows, total = departments(period, collected_total=collected["total"])

    charges_block = {
        "gross": total["gross"], "discounts": total["discounts"], "waivers": total["waivers"],
        # Goods a registered patient brought back to the POS (rule 25's own
        # column). Part of the identity: `net_due` already subtracts it.
        "returns": total["returns"],
        "net_due": total["net_due"], "collected": total["collected"],
        "part_payments": total["part_payments"], "outstanding": total["outstanding"],
        "count": total["charges"],
    }

    return {
        "period": period.as_dict(),
        "generated_at": timezone.now(),
        # Cash basis: what came in.
        "collections": collected,
        # Cohort basis: what was billed, and what has become of it.
        "charges": charges_block,
        # What was written off in the period — money nobody will collect, and
        # never part of revenue.
        "adjustments": given_away,
        # Bills withdrawn in the period, by when they were cancelled. Beside
        # the cohort, never in it — a cancelled bill was not revenue billed.
        "cancellations": cancellations(period),
        # Money handed back in the period, as its own category beside
        # payments, discounts and waivers. `net` is gross takings less this.
        "refunds": {
            "total": collected["refunds"], "count": collected["refunds_count"],
            "net_revenue": collected["net"],
        },
        "outstanding_now": outstanding_now(),
        # All time and the whole facility — deliberately given no period, so
        # the range the page is showing never moves it.
        "facility_revenue": facility_revenue(),
        # The pharmacy POS till as it recorded itself: a walk-in sale has no
        # charge, so its gross and discount appear nowhere else in the report.
        "pos_sales": pos_sales(period),
        "departments": department_rows,
        "totals": total,
        # The identity a reader can check the table against, spelled out
        # rather than left for them to work out.
        "reconciliation": {
            "gross": charges_block["gross"],
            "less_discounts": charges_block["discounts"],
            "less_waivers": charges_block["waivers"],
            "less_returns": charges_block["returns"],
            "net_due": charges_block["net_due"],
            "less_collected": charges_block["collected"],
            "outstanding": charges_block["outstanding"],
            # Returns are in the identity because `net_due` subtracts them: a
            # check that left them out read False the day a registered patient
            # brought POS goods back.
            "balances": (charges_block["gross"] - charges_block["discounts"]
                         - charges_block["waivers"] - charges_block["returns"]
                         - charges_block["collected"] == charges_block["outstanding"]),
            # The cash-basis identity, kept beside the cohort one and never
            # mixed with it: what came in, what went back, what was kept.
            "gross_collected": collected["total"],
            "less_refunds": collected["refunds"],
            "net_collected": collected["net"],
        },
        "transactions": transactions(period, limit=transaction_limit),
    }
