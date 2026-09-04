"""
Laboratory services — the rules about results live here, not in a view.

Two of them matter more than the rest:

1. **A blank is not a failure.** `save_values` skips every parameter the
   bench left empty and deletes any row that is cleared. Nothing raises,
   nothing is stored as an empty string, and the report has no blank lines
   to hide. This is the whole point of the module: a lab that ran three of
   fourteen indices files three results.

2. **A flag is arithmetic, not a diagnosis.** `flag_for` compares a number
   with the parameter's own bounds and says low / normal / high. It never
   names a condition, and a scientist's manual flag is never overwritten by
   a later recalculation.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.billing.services import add_charge

from .models import (
    LabOrder, LabOrderTest, LabParameter, LabResultAmendment, LabResultValue, TYPE_OPTIONS,
)


def as_number(value):
    """The numeric reading behind a typed value, or None if there isn't one."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    # "< 5", "> 90", "≥ 1.5" are real answers on a bench sheet. The comparison
    # is what the bench meant, so the bare number is not the reading and
    # nothing is flagged from it.
    if text[0] in "<>≤≥":
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def flag_for(parameter, value):
    """
    Low / normal / high for a numeric result with bounds. Empty for anything
    else — a text finding, a select, or a number the catalogue has no range
    for. A missing bound only disables that side of the comparison.

    Takes either a `LabParameter` or a snapshot dict, because a result is
    always flagged against the range **it was ordered under**, not whatever
    the catalogue says today.
    """
    spec = parameter if isinstance(parameter, dict) else {
        "result_type": parameter.result_type,
        "ref_low": parameter.ref_low, "ref_high": parameter.ref_high,
    }
    if spec.get("result_type") != "numeric":
        return ""
    low, high = _bound(spec.get("ref_low")), _bound(spec.get("ref_high"))
    if low is None and high is None:
        return ""
    number = as_number(value)
    if number is None:
        return ""
    if low is not None and number < low:
        return "low"
    if high is not None and number > high:
        return "high"
    return "normal"


def _bound(value):
    if value is None or value == "":
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


@transaction.atomic
def save_values(*, order_test, entries, author, reason=""):
    """
    Write what the bench actually entered.

    `entries` is a list of {"parameter": id, "value": str, "flag": str,
    "comment": str}. Anything with a blank value is not a result:
    * never stored,
    * never validated,
    * and if a row exists from an earlier save it is removed, because the
      scientist has just said that reading is not theirs to report.

    Changing a value that was already filed leaves a `LabResultAmendment`
    behind. A result can be corrected — it must not be corrected invisibly.

    Returns (written, cleared).
    """
    # The snapshot decides what this test's form is — not the catalogue as it
    # reads today. A parameter retired since the order was placed is still
    # answerable here, and one added since is not.
    specs = {spec["id"]: spec for spec in order_test.parameters if spec.get("id")}
    parameters = {p.id: p for p in LabParameter.objects.filter(id__in=specs)}
    existing = {v.parameter_id: v for v in order_test.values.select_related("parameter")}
    written = cleared = 0

    for entry in entries or []:
        parameter_id = _as_int(entry.get("parameter"))
        spec = specs.get(parameter_id)
        parameter = parameters.get(parameter_id)
        if spec is None or parameter is None:
            # Not a parameter of this ordered test. Ignored rather than fatal:
            # the rest of the bench sheet is still worth saving.
            continue

        # Coerced rather than assumed to be a string: a client sending JSON
        # numbers is entitled to, and `0 or ""` would silently drop a
        # basophil count of zero — a real reading, not a blank.
        raw = entry.get("value")
        value = ("" if raw is None else str(raw)).strip()
        row = existing.get(parameter.id)

        if not value:
            if row:
                _record_amendment(order_test, parameter, row.value, "", author, reason)
                row.delete()
                cleared += 1
            continue

        manual_flag = str(entry.get("flag") or "").strip()
        flag = manual_flag or flag_for(spec, value)
        comment = str(entry.get("comment") or "").strip()[:255]

        if row:
            if row.value != value:
                _record_amendment(order_test, parameter, row.value, value, author, reason)
            row.value = value[:255]
            row.flag = flag
            row.flag_is_manual = bool(manual_flag)
            row.comment = comment
            row.recorded_by = author
            row.save(update_fields=["value", "flag", "flag_is_manual", "comment",
                                    "recorded_by", "updated_at"])
        else:
            LabResultValue.objects.create(
                order_test=order_test, parameter=parameter, value=value[:255],
                flag=flag, flag_is_manual=bool(manual_flag), comment=comment,
                recorded_by=author,
                # From the order-time snapshot, not the live catalogue: a
                # range edited next year cannot rewrite what this report said.
                unit_at_entry=spec.get("unit", ""),
                reference_at_entry=spec.get("reference_range", ""),
            )
        written += 1

    return written, cleared


def _record_amendment(order_test, parameter, previous, new, author, reason):
    """
    Only a change to something already reported is an amendment. A draft
    being corrected before it goes anywhere is just typing.
    """
    if order_test.status not in ("submitted", "verified") or previous == new:
        return
    LabResultAmendment.objects.create(
        order_test=order_test, parameter=parameter,
        previous_value=previous or "", new_value=new or "",
        reason=(reason or "")[:255], amended_by=author,
    )


@transaction.atomic
def mark_resulted(*, order_test, author, comments=None):
    """
    The bench is finished with this test. It does not check that anything was
    entered — a culture reported as "no growth" in the comment and nothing
    else is a complete result — but a test with no values and no comment is
    refused by the view, which is where that judgement belongs.
    """
    order_test.status = "submitted"
    if comments is not None:
        order_test.comments = comments
    order_test.performed_by = author
    order_test.performed_at = timezone.now()
    order_test.save(update_fields=["status", "comments", "performed_by", "performed_at",
                                   "updated_at"])
    touch_order(order_test.order, author)
    return order_test


def touch_order(order, author):
    """
    Move the order along with its tests: in progress while some are open,
    awaiting verification once every one of them is answered.
    """
    order.entered_by = author
    order.entered_at = timezone.now()

    live = order.items.exclude(status="cancelled")
    if live.exists() and not live.exclude(status__in=["submitted", "verified"]).exists():
        if order.status not in ("completed", "cancelled"):
            order.status = "awaiting_verification"
    elif order.status in ("requested", "collected"):
        order.status = "in_progress"

    order.save(update_fields=["status", "entered_by", "entered_at", "updated_at"])
    return order


@transaction.atomic
def verify_order(*, order, author):
    """
    A second pair of eyes releases the report. Verification is a signature —
    it is recorded against a person and a time, and it is what the printed
    report calls "verified by".
    """
    now = timezone.now()
    # Released per test as well as per order: a report can go out with the
    # malaria film released and the culture still incubating, and the chart
    # has to be able to tell the difference.
    order.items.filter(status="submitted").update(
        status="verified", verified_by=author, verified_at=now)
    order.verified_by = author
    order.verified_at = now
    order.status = "completed"
    order.save(update_fields=["verified_by", "verified_at", "status", "updated_at"])
    return order


def report_lines(order):
    """
    The report, as rows — and *only* rows that have a result.

    A test with nothing entered still appears if the bench wrote a comment
    against it (a culture's "no growth" lives there); a test with neither is
    left off entirely, because printing a page of empty parameters is how a
    reader stops trusting the page.
    """
    sections = []
    for item in order.items.select_related("test").exclude(status="cancelled"):
        values = list(
            item.values.select_related("parameter").order_by(
                "parameter__display_order", "parameter_id")
        )
        if not values and not (item.comments or "").strip():
            continue
        sections.append({
            "test": item.test,
            "order_test": item,
            "name": item.name,
            "comments": item.comments,
            "values": values,
        })
    return sections


def order_for_route(route, *, author=None):
    """
    The order behind a laboratory referral, created on first sight.

    A doctor's referral is already a `PatientRoute`; the order is the bench's
    side of that same errand, so it is made when the lab first opens the
    route rather than asking anyone to raise a second request.
    """
    if route.purpose not in ("laboratory", "investigation"):
        return None
    order = getattr(route, "lab_order", None)
    if order is not None:
        return order
    return LabOrder.objects.create(
        patient=route.visit.patient, visit=route.visit, route=route,
        requested_by=route.routed_by, priority=route.priority,
        clinical_notes=route.notes or "", created_by=author or route.routed_by,
    )


def snapshot_of(test):
    """
    The catalogue entry frozen as it stands right now.

    Everything needed to reproduce this test's form, its report and its price
    years later, when the catalogue itself has moved on. Deactivated
    parameters are left out — they are not on the form today — but a
    parameter retired *after* this snapshot is taken stays in it, which is
    the whole point.
    """
    rows = test.parameters.filter(is_active=True).order_by("display_order", "id")
    return [
        {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "group": p.group,
            "result_type": p.result_type,
            "unit": p.unit,
            "reference_range": p.reference_range,
            "ref_low": str(p.ref_low) if p.ref_low is not None else None,
            "ref_high": str(p.ref_high) if p.ref_high is not None else None,
            "normal_value": p.normal_value,
            "options": TYPE_OPTIONS.get(p.result_type) or list(p.options or []),
            "display_order": p.display_order,
            "is_required": p.is_required,
        }
        for p in rows
    ]


@transaction.atomic
def add_tests(*, order, tests, author=None, source="requested", bill=True):
    """
    Put tests on an order.

    Two things happen at once, and both are about time. The catalogue is
    **snapshotted** onto the line, so an edit to a reference range or a price
    tomorrow cannot rewrite what this result was read against or what this
    patient was charged. And the charge is **raised**, because ordering a test
    is what creates the debt — the doctor should not have to walk to the
    counter to make the bill exist.

    The charge goes through `billing.services.add_charge` like every other
    charge in the hospital: one ledger, one debtors list, one place payments
    are allocated. The laboratory creates a billable service; it does not
    keep books.
    """
    added = []
    for test in tests:
        item, made = LabOrderTest.objects.get_or_create(
            order=order, test=test,
            defaults={
                "source": source,
                "test_name": test.name,
                "test_category": test.category,
                "specimen_type": test.specimen_type,
                "container": test.container,
                "unit_price": test.charge_amount,
                "parameters_snapshot": snapshot_of(test),
            },
        )
        if made:
            if bill:
                raise_charge_for(item, author=author)
            added.append(item)
    if added and order.status == "requested":
        order.status = "in_progress"
        order.save(update_fields=["status", "updated_at"])
    return added


def raise_charge_for(order_test, *, author=None):
    """
    The money side of ordering a test: one charge, priced from the snapshot.

    Per test rather than per order, because a patient can pay for the malaria
    film today and the FBC on Friday, and the front desk has to be able to
    settle them separately. A test with no price in the catalogue raises
    nothing — a zero charge is noise on a bill — and the catalogue page says
    so instead.
    """
    if order_test.charge_id or order_test.unit_price <= 0:
        return None
    order = order_test.order
    charge = add_charge(
        patient=order.patient,
        description=f"Laboratory: {order_test.name}",
        amount=order_test.unit_price,
        # Attributed to whoever asked for it — the doctor ordering is what
        # creates the debt, and the bill has to say so.
        created_by=author or order.requested_by or order.created_by,
        source_type="lab_test",
        source_id=order_test.pk,
    )
    order_test.charge = charge
    order_test.save(update_fields=["charge", "updated_at"])
    return charge


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
