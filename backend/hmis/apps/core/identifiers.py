"""
Two identifiers per person, doing two different jobs.

The database identifies a *row*: an integer primary key, plus `Patient.uuid`
for API callers who should never have to hold a guessable number. People
identify a *person* by a hospital number — short enough to read down a phone
line, stable enough to print on a card that outlives the visit:

    NMHS-P000001    a patient
    NMHS-S000001    a member of staff

The letter names the register the number belongs to, so a patient number and
a staff number can never be mistaken for one another on a form or in a search
box. Neither is derived from the UUID — a hospital number has to be readable
aloud — and neither is an authorisation: knowing it gets you nothing that the
role checks in `accounts/permissions.py` would not already have allowed.

The number is issued from the row's own primary key, so it is unique and
ordered by registration without a second counter to race on, and a deleted
row's number is never handed to somebody else (the sequence does not go
backwards).
"""

import re

HOSPITAL_PREFIX = "NMHS"

PATIENT = "P"
STAFF = "S"

#: `NMHS-P000001` / `NMHS-S000001` — the current shape.
_CURRENT = re.compile(rf"^{HOSPITAL_PREFIX}-([A-Z])(\d+)$")
#: `NMHS-000001` — the shape patients were registered under before the
#: register grew a second kind of person in it.
_LEGACY = re.compile(rf"^{HOSPITAL_PREFIX}-(\d+)$")


def format_number(kind, sequence):
    """`NMHS-P000001` for kind=PATIENT, sequence=1."""
    return f"{HOSPITAL_PREFIX}-{kind}{int(sequence):06d}"


def adopt_number(existing, kind, sequence):
    """
    The number this row should carry, preferring the one it already has.

    An already-current number is returned untouched — a number that moves is
    not an identifier. A legacy `NMHS-000001` keeps its digits and only gains
    the register letter, so the card in the patient's hand still reads the
    same six figures. Anything unrecognised (or missing) is issued fresh from
    the primary key.
    """
    if existing:
        current = _CURRENT.match(existing)
        if current and current.group(1) == kind:
            return existing
        legacy = _LEGACY.match(existing)
        if legacy:
            return f"{HOSPITAL_PREFIX}-{kind}{legacy.group(1)}"
    return format_number(kind, sequence)


def strip_kind(number, kind):
    """
    The reverse of `adopt_number`: `NMHS-P000001` back to `NMHS-000001`.

    Only used to make the migration that introduced the letter reversible.
    """
    if number:
        current = _CURRENT.match(number)
        if current and current.group(1) == kind:
            return f"{HOSPITAL_PREFIX}-{current.group(2)}"
    return number
