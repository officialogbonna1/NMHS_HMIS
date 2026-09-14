"""
Physical stock counts as a spreadsheet.

    Export → count the shelves → fill in "Counted Qty" → import → preview → confirm

A convenience layer over the count the inventory already has, never a second
way to set a quantity. Confirming an import posts ordinary `StockCount`
documents — one per location — through `services.post_stock_count`, so every
difference is a `StockMovement` with reason "Physical stock count", the count
sheet keeps what the system believed and what was found, and the movement's
reference names the import (`IMP-000004 · CNT-000019: B123`). System 100,
counted 92 is a −8 movement; counted 108 is +8. Nothing is overwritten.

Five rules keep it safe:

* **Nothing is applied from a file with a single problem in it.** Every row is
  validated first; an import with errors is saved only so the preview can show
  them, and it can never be confirmed.
* **Stock that moved since the export is a conflict, not a correction.** Each
  row carries the system quantity it was exported with. If the shelf has sold
  or received since, the physical count no longer describes today's number, and
  applying it would silently undo that sale or delivery — so the row is refused
  and must be recounted. The same check runs again, under lock, at confirm.
* **A person counts only where they may.** `countable_locations` — Administration
  and the inventory manager anywhere, the pharmacy its dispensing shelf (rule
  29). Checked at preview for the uploader and again at confirm for whoever
  confirms.
* **An import applies once.** Confirming is a compare-and-set on its status
  inside the same transaction as the adjustments, and uploading the exact bytes
  of a file already applied is refused at preview.
* **A category is recognised, never invented.** The Category column is checked
  against the categories already on file — ignoring case and punctuation, so
  "Pain Relief", "pain relief" and "Pain-Relief" are one group — and a cell
  that resolves to nothing is reported by name rather than quietly becoming a
  twenty-fourth category. Nothing about a *product* is ever written by this
  import: the file counts stock, and a product filed under the wrong category
  is corrected under Products, not in a spreadsheet.
"""
import csv
import hashlib
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.permissions import ADMIN_ROLES

from .models import ItemCategory, StockCountImport, StockLocation, StockRecord
from .services import post_stock_count

HEADERS = ["Line ID", "SKU", "Product", "Category", "Unit", "Location", "Batch", "Expiry",
           "System Qty", "Counted Qty", "Difference"]
REQUIRED = ["Line ID", "Product", "Location", "Batch", "Expiry", "System Qty", "Counted Qty"]
MAX_ROWS = 5000
#: A count above this is a slip — an extra zero, a barcode pasted into the
#: wrong column — not a shelf.
MAX_COUNT = 1_000_000
# ISO first; then day-first, which is how a spreadsheet in Nigeria re-saves a date.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def countable_locations(user):
    """
    Where this person may count from a spreadsheet.

    Administration and the inventory manager count anywhere. A pharmacist counts
    the dispensing shelf — the shelf the pharmacy's own count tab is pinned to —
    so a file that also lists the Main Store is refused on those rows rather
    than posted against a store the pharmacy does not keep. Nobody else counts.
    """
    locations = StockLocation.objects.filter(is_active=True)
    role = getattr(user, "role", None)
    if role in ADMIN_ROLES or role == "inventory_manager":
        return locations
    if role == "pharmacist":
        return locations.filter(is_dispensing_point=True)
    return locations.none()


def normalise_category(name):
    """
    "Pain Relief", "pain relief" and "Pain-Relief" are one category.

    Case and punctuation go; the words stay. Deliberately not fuzzy — "Pain
    Relif" resolves to nothing and is reported, because a category matched by
    guesswork is how four spellings silently become four groups.
    """
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def category_index():
    """Every category on file, by normalised name. Built once per import."""
    return {normalise_category(name): (pk, name)
            for pk, name in ItemCategory.objects.values_list("pk", "name")}


def stock_lines(*, location=None, category=None, item=None, batch_no="", active_only=True,
                include_empty=False, locations=None):
    """The stock lines a count sheet lists — one per batch per location."""
    records = (StockRecord.objects
               .select_related("batch__item__category", "batch__item__unit", "location")
               .order_by("location__display_order", "location__name", "batch__item__name",
                         "batch__expiry_date", "batch_id"))
    if locations is not None:
        records = records.filter(location__in=locations)
    if location is not None:
        records = records.filter(location=location)
    if category is not None:
        records = records.filter(batch__item__category=category)
    if item is not None:
        records = records.filter(batch__item=item)
    if batch_no:
        records = records.filter(batch__batch_no__iexact=batch_no.strip())
    if active_only:
        records = records.filter(batch__item__is_active=True)
    if not include_empty:
        records = records.filter(quantity__gt=0)
    return records


def export_csv(records):
    """
    The sheet itself. Counted Qty and Difference are left for the counter.

    Written with a byte-order mark so a spreadsheet opens "₦" and accented
    names correctly; the import reads the file either way.
    """
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for record in records:
        batch, item = record.batch, record.batch.item
        writer.writerow([record.pk, item.sku or "", item.name, item.category_name,
                         item.unit_label, record.location.name, batch.batch_no,
                         batch.expiry_date.isoformat(), record.quantity, "", ""])
    return buffer.getvalue()


def _text(row, column):
    return str(row.get(column) or "").strip()


def _whole(value):
    """'12' and '12.0' are 12; anything else is None."""
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


def _date(value):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def preview_import(*, content, filename, user):
    """
    Read and validate an uploaded count file and keep the result as a preview.
    Nothing moves. Returns the saved `StockCountImport`.
    """
    checksum = hashlib.sha256(content).hexdigest()
    errors, rows = [], []

    def problem(row, message, column=None):
        errors.append({"row": row, "column": column, "message": message})

    already = StockCountImport.objects.filter(checksum=checksum, status="applied").first()
    if already is not None:
        when = timezone.localtime(already.applied_at).strftime("%d %b %Y %H:%M") if already.applied_at else ""
        problem(None, f"This exact file was already applied as {already.reference} {when}. "
                      f"Export a fresh sheet for a new count.")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
        problem(None, 'The file is not a UTF-8 CSV. Save it as "CSV UTF-8" and import it again.')

    raw_rows = []
    if text is not None:
        try:
            reader = csv.DictReader(io.StringIO(text))
            headers = [str(name or "").strip() for name in (reader.fieldnames or [])]
        except csv.Error:
            headers = []
        reader.fieldnames = headers
        missing = [name for name in REQUIRED if name not in headers]
        if missing:
            problem(None, f"Missing column(s): {', '.join(missing)}. Import the sheet with its "
                          f"columns as they were exported.")
        else:
            try:
                raw_rows = list(reader)
            except csv.Error as exc:
                problem(None, f"The file is not a readable CSV ({exc}).")
                raw_rows = []
            if len(raw_rows) > MAX_ROWS:
                problem(None, f"{len(raw_rows)} rows is more than one import takes ({MAX_ROWS}). "
                              f"Export by location or category and import each part.")
                raw_rows = []

    parsed = []
    for number, raw in enumerate(raw_rows, start=2):
        if not any(str(value or "").strip() for value in raw.values() if not isinstance(value, list)):
            continue  # a blank line a spreadsheet left behind
        line_id = _whole(_text(raw, "Line ID"))
        if line_id is None or line_id < 1:
            problem(number, "Line ID must be the number from the export — do not edit or add lines.",
                    "Line ID")
            continue
        parsed.append((number, line_id, raw))

    allowed = set(countable_locations(user).values_list("pk", flat=True))
    categories = category_index()
    unknown_categories = []
    records = (StockRecord.objects.select_related("batch__item__category", "location")
               .in_bulk([line_id for _, line_id, _ in parsed]))
    first_seen, not_counted = {}, 0
    for number, line_id, raw in parsed:
        if line_id in first_seen:
            problem(number, f"Line {line_id} appears twice (rows {first_seen[line_id]} and {number}). "
                            f"Each stock line is counted once.", "Line ID")
            continue
        first_seen[line_id] = number
        record = records.get(line_id)
        if record is None:
            problem(number, f"Line {line_id} is not a stock line in this system.", "Line ID")
            continue
        batch, item, location = record.batch, record.batch.item, record.location
        if location.pk not in allowed:
            problem(number, f"You are not authorised to count {location.name}.", "Location")
            continue
        before = len(errors)

        product = _text(raw, "Product")
        if product.lower() != item.name.lower():
            problem(number, f'Product "{product}" does not match line {line_id} ({item.name}).',
                    "Product")
        sku = _text(raw, "SKU")
        if sku and sku.lower() != (item.sku or "").lower():
            problem(number, f'SKU "{sku}" is not the SKU of {item.name}.', "SKU")
        # The Category cell is checked, never acted on: this file is a count of
        # stock, not a product edit, so it can no more re-file a product than it
        # can rename it. A cell that does not resolve to a category already on
        # file is reported by name and **nothing is created from it** — that is
        # how "Pain Relief", "Pain-Relief" and "Pain Relif" stay one category
        # and one typo rather than three groups. Blank is fine; the column is
        # informational and a sheet without it still imports.
        category_text = _text(raw, "Category")
        if category_text:
            resolved = categories.get(normalise_category(category_text))
            if resolved is None:
                if category_text not in unknown_categories:
                    unknown_categories.append(category_text)
                problem(number, f'Category "{category_text}" is not a category in this system. '
                                f'Correct the spelling, or add the category under '
                                f'Administration → Product categories first — importing a count '
                                f'never creates one.', "Category")
            elif resolved[0] != item.category_id:
                held = item.category_name or "no category"
                problem(number, f'{item.name} is filed under {held}, not "{category_text}". '
                                f'A count sheet does not change a product\'s category — '
                                f'change it under Products, then export a fresh sheet.',
                        "Category")
        where = _text(raw, "Location")
        if where.lower() not in {location.name.lower(), location.code.lower()}:
            problem(number, f'Location "{where}" is not where line {line_id} stands ({location.name}).',
                    "Location")
        batch_no = _text(raw, "Batch")
        if batch_no.lower() != batch.batch_no.lower():
            problem(number, f'Batch "{batch_no}" does not match line {line_id} (batch {batch.batch_no}).',
                    "Batch")
        expiry_text = _text(raw, "Expiry")
        expiry = _date(expiry_text)
        if expiry is None:
            problem(number, f'Expiry "{expiry_text}" is not a date — use YYYY-MM-DD.', "Expiry")
        elif expiry != batch.expiry_date:
            problem(number, f"Expiry {expiry.isoformat()} does not match batch {batch.batch_no} "
                            f"({batch.expiry_date.isoformat()}).", "Expiry")
        system = _whole(_text(raw, "System Qty"))
        if system is None:
            problem(number, "System Qty must be left exactly as exported.", "System Qty")
        elif system != record.quantity:
            problem(number, f"Stock on this line moved after the export (the system now holds "
                            f"{record.quantity}, the file says {system}). Re-export and recount it.",
                    "System Qty")

        counted_text = _text(raw, "Counted Qty")
        counted = None
        if counted_text:
            counted = _whole(counted_text)
            if counted is None or counted < 0:
                problem(number, f'Counted Qty "{counted_text}" must be a whole number, 0 or more.',
                        "Counted Qty")
                counted = None
            elif counted > MAX_COUNT:
                problem(number, f"Counted Qty {counted} is not a believable count for one batch — "
                                f"check the cell.", "Counted Qty")
                counted = None

        if len(errors) > before:
            continue
        if counted is None:
            not_counted += 1  # left blank: not counted, not changed
            continue
        rows.append({
            "row": number, "line": record.pk, "batch": batch.pk, "location": location.pk,
            "location_name": location.name, "item_name": item.name, "sku": item.sku or "",
            "batch_no": batch.batch_no, "expiry": batch.expiry_date.isoformat(),
            "category": item.category_id, "category_name": item.category_name,
            "system_quantity": record.quantity, "counted_quantity": counted,
            "difference": counted - record.quantity,
        })

    if not errors and not rows:
        problem(None, "No line has a Counted Qty. Fill in what you counted and import again.")

    increases = [row for row in rows if row["difference"] > 0]
    decreases = [row for row in rows if row["difference"] < 0]
    summary = {
        "rows_read": len(parsed),
        "counted": len(rows),
        "not_counted": not_counted,
        "unchanged": sum(1 for row in rows if row["difference"] == 0),
        "increase_lines": len(increases),
        "units_added": sum(row["difference"] for row in increases),
        "decrease_lines": len(decreases),
        "units_removed": -sum(row["difference"] for row in decreases),
        "locations": sorted({row["location_name"] for row in rows}),
        "categories": sorted({row["category_name"] for row in rows if row["category_name"]}),
        # Named, never created. The preview lists these so whoever uploaded the
        # sheet can see which spellings the system did not recognise.
        "unknown_categories": unknown_categories,
        "error_count": len(errors),
    }
    return StockCountImport.objects.create(filename=str(filename or "")[:255], checksum=checksum,
                                           rows=rows, errors=errors, summary=summary,
                                           uploaded_by=user)


def apply_import(*, stock_import, user):
    """
    Post a previewed import: all of it, once, or none of it.
    """
    with transaction.atomic():
        locked = StockCountImport.objects.select_for_update().get(pk=stock_import.pk)
        if locked.status == "applied":
            raise ValidationError(f"{locked.reference} has already been applied — it cannot be applied twice.")
        if locked.status != "previewed":
            raise ValidationError(f"{locked.reference} was discarded. Import the file again.")
        if locked.errors:
            raise ValidationError("This import has errors. Correct the file and import it again — "
                                  "nothing was applied.")
        if not locked.rows:
            raise ValidationError("Nothing was counted in this file.")
        # Whoever confirms must be allowed to count every shelf in it — not only
        # whoever uploaded it.
        allowed = set(countable_locations(user).values_list("pk", flat=True))
        refused = sorted({row["location_name"] for row in locked.rows if row["location"] not in allowed})
        if refused:
            raise PermissionDenied(f"You are not authorised to count {', '.join(refused)}. "
                                   f"Nothing was applied.")
        other = (StockCountImport.objects.filter(checksum=locked.checksum, status="applied")
                 .exclude(pk=locked.pk).first())
        if other is not None:
            raise ValidationError(f"This file was already applied as {other.reference}.")
        now = timezone.now()
        claimed = StockCountImport.objects.filter(pk=locked.pk, status="previewed").update(
            status="applied", applied_by=user, applied_at=now, updated_at=now)
        if claimed != 1:
            raise ValidationError(f"{locked.reference} is already being applied.")

        by_location = {}
        for row in locked.rows:
            by_location.setdefault(row["location"], []).append(row)
        locations = StockLocation.objects.in_bulk(list(by_location))
        counts = []
        for location_id, rows in by_location.items():
            location = locations.get(location_id)
            if location is None:
                raise ValidationError(f"Row {rows[0]['row']}: that location no longer exists. "
                                      f"Nothing was applied.")
            records = (StockRecord.objects.select_for_update().select_related("batch")
                       .in_bulk([row["line"] for row in rows]))
            lines = []
            for row in rows:
                record = records.get(row["line"])
                if record is None or record.batch_id != row["batch"] or record.location_id != location_id:
                    raise ValidationError(f"Row {row['row']}: that stock line no longer exists as "
                                          f"previewed. Nothing was applied.")
                if record.quantity != row["system_quantity"]:
                    raise ValidationError(
                        f"Row {row['row']} ({row['item_name']}, batch {row['batch_no']}): stock "
                        f"moved after the preview (now {record.quantity}, previewed "
                        f"{row['system_quantity']}). Nothing was applied — re-export and recount.")
                lines.append({"batch": record.batch, "counted_quantity": row["counted_quantity"]})
            counts.append(post_stock_count(
                location=location, lines=lines, actor=user,
                note=f"CSV stock count {locked.reference}", reason="stock_count",
                reference_prefix=locked.reference))
        locked.counts.add(*counts)
    locked.refresh_from_db()
    return locked


def discard_import(*, stock_import):
    now = timezone.now()
    discarded = StockCountImport.objects.filter(pk=stock_import.pk, status="previewed").update(
        status="discarded", updated_at=now)
    if discarded != 1:
        raise ValidationError("Only a preview that has not been applied can be discarded.")
    stock_import.refresh_from_db()
    return stock_import
