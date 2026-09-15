"""
The eye examination: the structured half of an eye doctor's consultation note.

It is not a clinical record of its own. It lives on `ConsultationNote
.eye_examination`, so it locks when the note locks (`LockedRecordMixin`), an
admin amendment snapshots it with the rest of the note
(`ConsultationNoteAmendment.previous_eye_examination`), and there is no second
locking or versioning system to keep in step. The consultation's own fields —
reason, chief complaint, notes, diagnosis, plan — carry the history, the
assessment and the plan; nothing here repeats them.

`SECTIONS` below is the one definition of what may be recorded. The server
validates against it (`clean_eye_examination`) and serves it to the note form
(`GET /api/notes/eye-examination-fields/`), so the form and the rules cannot
drift apart. Adding a finding is an entry here.

Nothing is mandatory — the doctor fills in what they examined. A blank is not
a finding (the same rule the laboratory keeps, rule 20): blanks are dropped,
and a note whose examination is entirely blank stores none.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

SHORT, TEXT, NUMBER, CHOICE = "short", "text", "number", "choice"

# Eye pressure in mmHg. Zero is hypotony and an acute angle closure can pass
# 60, so the bounds are "a reading a tonometer can give", not "normal".
IOP_MIN, IOP_MAX = Decimal("0"), Decimal("80")

IOP_METHODS = [
    ("applanation", "Applanation (Goldmann)"),
    ("non_contact", "Non-contact (air puff)"),
    ("rebound", "Rebound (iCare)"),
    ("tonopen", "Tono-Pen"),
    ("digital", "Digital palpation"),
    ("other", "Other"),
]

MAX_LENGTH = {SHORT: 60, TEXT: 1000}

# (key, label, [paired right/left rows], [single fields])
# A paired row stores `<name>_right` and `<name>_left`.
SECTIONS = [
    ("vision", "Vision", [
        ("distance", "Distance", SHORT),
        ("near", "Near", SHORT),
        ("pinhole", "Pinhole", SHORT),
        ("refraction", "Refraction", SHORT),
    ], []),
    ("pressure", "Eye pressure", [
        ("iop", "IOP (mmHg)", NUMBER),
    ], [
        ("iop_method", "Measurement method", CHOICE),
    ]),
    ("anterior", "Anterior segment", [
        ("lids", "Lids", TEXT),
        ("conjunctiva", "Conjunctiva", TEXT),
        ("cornea", "Cornea", TEXT),
        ("anterior_chamber", "Anterior chamber", TEXT),
        ("iris_pupil", "Iris / pupil", TEXT),
        ("lens", "Lens", TEXT),
    ], []),
    ("posterior", "Posterior segment", [
        ("optic_disc", "Optic disc", TEXT),
        ("macula", "Macula", TEXT),
        ("retina", "Retina", TEXT),
    ], []),
    ("other", "Other", [], [
        ("eye_movements", "Eye movements", TEXT),
        ("visual_fields", "Visual fields", TEXT),
        ("follow_up", "Follow-up", TEXT),
    ]),
]

EYES = (("right", "Right eye"), ("left", "Left eye"))


def _fields():
    fields = {}
    for _, section_label, paired, single in SECTIONS:
        for name, label, kind in paired:
            for eye, eye_label in EYES:
                fields[f"{name}_{eye}"] = {"label": f"{label} — {eye_label.lower()}", "kind": kind}
        for name, label, kind in single:
            fields[name] = {"label": label, "kind": kind}
    return fields


# Every key an examination may hold, with its kind.
FIELDS = _fields()


def schema():
    """The definition as the note form reads it."""
    def spec(kind):
        out = {"kind": kind}
        if kind in MAX_LENGTH:
            out["max_length"] = MAX_LENGTH[kind]
        if kind == NUMBER:
            out.update(min=float(IOP_MIN), max=float(IOP_MAX), unit="mmHg")
        if kind == CHOICE:
            out["choices"] = [{"value": value, "label": label} for value, label in IOP_METHODS]
        return out

    return {
        "eyes": [{"key": eye, "label": label} for eye, label in EYES],
        "sections": [
            {
                "key": key, "label": label,
                "rows": [{"name": name, "label": row_label,
                          **{eye: f"{name}_{eye}" for eye, _ in EYES}, **spec(kind)}
                         for name, row_label, kind in paired],
                "fields": [{"key": name, "label": field_label, **spec(kind)}
                           for name, field_label, kind in single],
            }
            for key, label, paired, single in SECTIONS
        ],
    }


def _blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _clean_number(value):
    # `True` is an int in Python; a checkbox value is not a pressure reading.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError
    if not number.is_finite() or not IOP_MIN <= number <= IOP_MAX:
        raise ValueError
    number = number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return int(number) if number == number.to_integral_value() else float(number)


def clean_eye_examination(value):
    """
    The examination as it may be stored, or None when there is nothing in it.

    Refuses — with `ValidationError` keyed by field — anything that is not an
    object, any key `FIELDS` does not name, text that is not text or is too
    long, a pressure outside what a tonometer reads, and a method not on the
    list. Blank values are dropped rather than refused.
    """
    if _blank(value) or value == {}:
        return None
    if not isinstance(value, dict):
        raise ValidationError("The eye examination must be a set of findings.")

    errors, cleaned = {}, {}
    for key, raw in value.items():
        spec = FIELDS.get(key)
        if spec is None:
            errors[key] = ["Not a field of the eye examination."]
            continue
        if _blank(raw):
            continue
        kind = spec["kind"]
        if kind == NUMBER:
            try:
                cleaned[key] = _clean_number(raw)
            except ValueError:
                errors[key] = [f"Enter a pressure between {IOP_MIN} and {IOP_MAX} mmHg."]
        elif kind == CHOICE:
            if raw not in dict(IOP_METHODS):
                errors[key] = ["Choose a measurement method from the list."]
            else:
                cleaned[key] = raw
        else:
            if not isinstance(raw, str):
                errors[key] = ["Enter this finding as text."]
                continue
            text = raw.strip()
            if len(text) > MAX_LENGTH[kind]:
                errors[key] = [f"Keep this to {MAX_LENGTH[kind]} characters."]
            else:
                cleaned[key] = text
    if errors:
        raise ValidationError(errors)
    return cleaned or None
