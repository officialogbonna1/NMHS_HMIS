"""
Every configured service the counter can bill, read from the catalogues that
already define them.

**The problem this exists to solve.** The hospital keeps its billable things
in more than one place, and it is right to: a laboratory test is not only a
price, it is a specimen, a container, a set of parameters and a reference
range, which is why `LabTest` exists and why the doctor orders from it. A
hospital card is a name and a price, which is what `BillingItem` is for.

What was wrong was not the catalogues but the *window onto them*. Reception's
billing panel read `BillingItem` alone, so the laboratory tab offered the one
row the price list happened to hold — "Full Blood Count" — while the doctor
ordered from sixty-six. The desk could not bill a test a doctor had just
ordered because the desk was reading a different book.

So this module composes one **read-only** list over both, and nothing else:

* it defines no service and stores no price — every row is somebody else's
  configuration, quoted;
* a service that exists in both catalogues is listed **once**, under the
  entry that owns it (a `LabTest` that points at a `BillingItem` *is* that
  priced row, so the row is not offered a second time on its own);
* `source_type` on each row is exactly what the billing counter already posts
  when it raises the charge (`Charge.source_type`), so the department
  attribution in `billing/departments.py` is unchanged and no new kind of
  charge is created.

Adding a catalogue here is adding a reader to `_SOURCES`. It is never a place
to add a service.
"""
from decimal import Decimal

from .models import BillingItem

#: The categories the counter bills under, in the order the screens show
#: them. `BillingItem.CATEGORY` is the definition; this is that list with its
#: labels, so a category added there appears here with nothing else to edit.
CATEGORIES = list(BillingItem.CATEGORY)
CATEGORY_LABELS = dict(CATEGORIES)


def _row(*, source, obj, name, category, price, detail="", is_active=True, extra=None):
    """
    One offerable service.

    `key` is what a picker uses for identity — it has to survive two
    catalogues whose primary keys collide. `source_type` is what the charge
    is posted with, and is the category for every row here: the counter has
    always billed `source_type=<category>`, and the laboratory's own
    `lab_test` source belongs to an order the lab raised, not to a line
    somebody typed at a window.
    """
    return {
        "key": f"{source}:{obj.pk}",
        "source": source,
        "id": obj.pk,
        "name": name,
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, category.title()),
        "price": f"{Decimal(price or 0):.2f}",
        "source_type": category,
        "detail": detail,
        "is_active": is_active,
        **(extra or {}),
    }


def _billing_items(*, active_only, spoken_for):
    """
    The price list itself — cards, consultation fees, imaging examinations,
    procedures and anything else the counter prices directly.

    `spoken_for` are the rows another catalogue already represents. They are
    left out rather than shown twice: one service, one price, one row to pick.
    """
    queryset = BillingItem.objects.all()
    if active_only:
        queryset = queryset.filter(is_active=True)
    return [
        _row(source="billing_item", obj=item, name=item.name, category=item.category,
             price=item.price, is_active=item.is_active)
        for item in queryset.order_by("category", "name")
        if item.pk not in spoken_for
    ]


def _lab_tests(*, active_only):
    """
    The laboratory catalogue, which is what a doctor orders from and
    therefore what the desk has to be able to bill.

    The price is `charge_amount` — the test's own rule, which prefers a
    linked `BillingItem` — so the counter can never quote a second figure.
    """
    from apps.laboratory.models import LabTest

    queryset = LabTest.objects.select_related("billing_item")
    if active_only:
        queryset = queryset.filter(is_active=True)
    rows = []
    for test in queryset.order_by("category", "display_order", "name"):
        detail = " · ".join(x for x in [test.get_category_display(), test.specimen_type] if x)
        rows.append(_row(source="lab_test", obj=test, name=test.name, category="laboratory",
                         price=test.charge_amount, detail=detail, is_active=test.is_active,
                         extra={"code": test.code}))
    return rows


def _lab_test_billing_item_ids():
    """The priced rows the laboratory catalogue already speaks for."""
    from apps.laboratory.models import LabTest

    return set(LabTest.objects.filter(billing_item__isnull=False)
               .values_list("billing_item_id", flat=True))


def services(*, category=None, search="", active_only=True):
    """
    Everything billable, optionally narrowed to one category or a search.

    Never paginated and never truncated: this is a picker over configuration,
    and a list that silently stops at the first page is how a desk concludes
    the hospital offers one laboratory test.
    """
    rows = _lab_tests(active_only=active_only) + _billing_items(
        active_only=active_only, spoken_for=_lab_test_billing_item_ids())
    if category:
        rows = [row for row in rows if row["category"] == category]
    term = (search or "").strip().lower()
    if term:
        rows = [row for row in rows
                if term in row["name"].lower() or term in row.get("detail", "").lower()]
    return sorted(rows, key=lambda row: (row["category"], row["name"].lower()))


def counts(*, active_only=True):
    """How many services each category offers — for a tab that says so."""
    tally = {category: 0 for category, _ in CATEGORIES}
    for row in services(active_only=active_only):
        tally[row["category"]] = tally.get(row["category"], 0) + 1
    return tally


def resolve(keys, *, active_only=True):
    """
    Turn the keys a screen sent back into the services they name, **priced
    here**.

    This is the half of the module that bills. A counter posts identities —
    `["lab_test:12", "billing_item:7"]` — and the price, the description and
    the source type all come from the catalogue row on this side of the wire.
    A figure typed into a request body is never money in this system.

    Returns `(services, unknown)`: the resolved rows in the order they were
    asked for, and the keys that named nothing billable — a retired service,
    a deleted row, a malformed key. The caller decides whether an unknown key
    is fatal; nothing here guesses at one.
    """
    wanted = []
    for key in keys or []:
        text = str(key).strip()
        if text and text not in wanted:
            wanted.append(text)
    available = {row["key"]: row for row in services(active_only=active_only)}
    found, unknown = [], []
    for key in wanted:
        row = available.get(key)
        (found if row is not None else unknown).append(row if row is not None else key)
    return found, unknown


def total_of(rows):
    """What the resolved services come to. The authoritative figure."""
    return sum((Decimal(row["price"]) for row in rows), Decimal("0"))
