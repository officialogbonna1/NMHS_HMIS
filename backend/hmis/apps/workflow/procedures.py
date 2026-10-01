"""
The Procedure Department: who works a procedure referral, and the materials a
procedure record documents.

**Not a second workflow.** A procedure is a `PatientRoute` with
`purpose="procedure"` — the doctor's existing referral (`refer/`), the existing
queue transitions, the existing `record-result`, the permanent `MedicalTest`
copy on the patient's record, the chart's Procedures tab, the existing bell,
audit log and printing. What this module adds is the two things the route did
not already know.

**1. Who the work is waiting on.** Every other unit is a role (rule 16): a lab
request is the laboratory's. A procedure is done by whoever is posted to the
procedure room — a doctor one day, a nurse doing dressings the next — so it is
**the department's**: the doctors and nurses an administrator has put on the
Procedure department (`Department.staff`, read through
`accounts.departments.works_in`, the hospital's one definition of where a
member of staff is authorised). That is the existing department authorisation,
not a new role and not a new membership table, and it is deliberately narrow:

* a role outside `TEAM_ROLES` gains nothing from being listed there — a cashier
  posted to theatre is still a cashier;
* a doctor or nurse who is *not* listed gains no procedure work, however many
  procedures the hospital does;
* an administrator passes, as everywhere.

Every reader in `workflow/` — the queue, the board, the bell, `accept`,
`may_work`, the named-assignee checks, the printable document — asks
`in_team`, so they cannot disagree about who the procedure staff are.

**2. Materials used.** A procedure record documents what went into it: the
item, how much was received, used, left and wasted, and in what unit.
**Clinical documentation, never stock**: nothing here reads or moves
inventory, and the rows live on the route's own `result_data` beside the report
sections (rule 53's arrangement) because nothing filters, groups or joins on
them. The identity a hospital auditor checks is enforced here, on the server:

    quantity received = quantity used + quantity remaining + wastage
"""
from decimal import Decimal, InvalidOperation

PURPOSE = "procedure"

#: The Procedure department (`departments/0007`) — its own department, never
#: Theatre and never a combined one: Theatre membership grants nothing here,
#: and Procedure membership grants nothing there. Read by its stable code,
#: never its name, so renaming it keeps every authorisation working.
DEPARTMENT_CODE = "procedure"

#: The roles that may work procedures *when posted to the department*.
TEAM_ROLES = ("doctor", "nurse")


def department():
    """The Procedure Department row, or None if an administrator retired it."""
    from apps.departments.models import Department
    return Department.objects.filter(code=DEPARTMENT_CODE, is_active=True).first()


def in_team(user):
    """
    Is this person procedure staff? A doctor or nurse authorised in the
    Procedure Department. Administrators are let through by the callers, the
    way every `RoleRequired` check lets them through.
    """
    from apps.accounts.departments import works_in

    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    return getattr(user, "role", None) in TEAM_ROLES and works_in(user, DEPARTMENT_CODE)


def team():
    """Every active member of procedure staff — who an unclaimed referral reaches."""
    from apps.accounts.departments import staff_of
    from apps.accounts.models import User

    dept = department()
    return staff_of(dept, roles=TEAM_ROLES) if dept else User.objects.none()


def may_be_named(user):
    """May this person be the named assignee on a procedure referral?"""
    return bool(user) and user.is_active and (getattr(user, "is_admin", False) or in_team(user))


# ------------------------------------------------------------------ materials

#: The quantity columns, in the order the identity reads.
QUANTITIES = ("quantity_received", "quantity_used", "quantity_remaining", "wastage")
TEXT_FIELDS = {"name": 120, "category": 60, "unit": 30, "notes": 255}
MAX_ROWS = 50
TWO_PLACES = Decimal("0.01")

LABELS = {
    "name": "Item / material", "category": "Category", "unit": "Unit", "notes": "Notes",
    "quantity_received": "Received", "quantity_used": "Used",
    "quantity_remaining": "Remaining", "wastage": "Wastage",
}


def _quantity(raw):
    """A non-negative amount to two places, None when blank; raises on anything else."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise ValueError("Enter a number.")
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a number.")
    if not value.is_finite():
        raise ValueError("Enter a number.")
    if value < 0:
        raise ValueError("Cannot be negative.")
    if value != value.quantize(TWO_PLACES):
        raise ValueError("Use at most two decimal places.")
    if value > Decimal("999999"):
        raise ValueError("That is too large to be a quantity used in one procedure.")
    return value.quantize(TWO_PLACES)


class MaterialsError(ValueError):
    """Refused rows: `errors` is a list-level message list, or `{row: {field: [...]}}`."""

    def __init__(self, errors):
        super().__init__("The materials could not be saved.")
        self.errors = errors


def clean_materials(raw):
    """
    The materials list as it may be stored — or `MaterialsError` keyed by row.

    Each row needs a name and at least one quantity. Where `quantity_received`
    is given the identity must hold exactly, a blank used / remaining / wastage
    counting as zero — the record is never quietly corrected, because a figure
    that does not add up is the thing an auditor is looking for. Without a
    received figure there is nothing to balance, so used, remaining and
    wastage are simply recorded.

    Quantities are stored as strings (`"2.50"`) so the JSON keeps the exact
    decimal, not a float.
    """
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise MaterialsError(["Send the materials as a list of rows."])
    if len(raw) > MAX_ROWS:
        raise MaterialsError([f"Record at most {MAX_ROWS} items on one procedure."])

    errors, rows = {}, []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            errors[str(index)] = {"__all__": ["Each item is a set of fields."]}
            continue
        problems, clean = {}, {}
        unknown = set(row) - set(TEXT_FIELDS) - set(QUANTITIES)
        for key in sorted(unknown):
            problems[key] = ["Not a field of a material row."]
        for key, limit in TEXT_FIELDS.items():
            value = row.get(key)
            if value is None:
                value = ""
            if not isinstance(value, str):
                problems[key] = ["Enter this as text."]
                continue
            value = value.strip()
            if len(value) > limit:
                problems[key] = [f"Keep this to {limit} characters."]
                continue
            if value:
                clean[key] = value
        if "name" not in clean and "name" not in problems:
            problems["name"] = ["Name the item or material."]
        amounts = {}
        for key in QUANTITIES:
            try:
                amounts[key] = _quantity(row.get(key))
            except ValueError as exc:
                problems[key] = [str(exc)]
        if not problems:
            if all(value is None for value in amounts.values()):
                problems["quantity_used"] = ["Record how much was used (or received)."]
            elif amounts["quantity_received"] is not None:
                accounted = sum((amounts[key] or Decimal("0")) for key in QUANTITIES[1:])
                if accounted != amounts["quantity_received"]:
                    problems["quantity_received"] = [
                        f"Received ({amounts['quantity_received']}) must equal used + remaining "
                        f"+ wastage ({accounted})."]
        if problems:
            errors[str(index)] = problems
            continue
        clean.update({key: f"{value}" for key, value in amounts.items() if value is not None})
        rows.append(clean)
    if errors:
        raise MaterialsError(errors)
    return rows


def _number(text):
    value = Decimal(text)
    return f"{value.normalize():f}" if value == value.to_integral() else f"{value}"


def render_materials(rows):
    """The materials as lines of text, for the readers that show `result` as prose."""
    if not rows:
        return ""
    lines = []
    for row in rows:
        unit = f" {row['unit']}" if row.get("unit") else ""
        parts = [f"{LABELS[key].lower()} {_number(row[key])}{unit}"
                 for key in ("quantity_used", "quantity_received", "quantity_remaining", "wastage")
                 if key in row]
        line = f"- {row['name']}"
        if row.get("category"):
            line += f" ({row['category']})"
        if parts:
            line += ": " + ", ".join(parts)
        if row.get("notes"):
            line += f" — {row['notes']}"
        lines.append(line)
    return "Materials used:\n" + "\n".join(lines)
