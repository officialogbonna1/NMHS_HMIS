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

A finding is one of five kinds. `SHORT` and `TEXT` are free text of a sane
length; `NUMBER` is a pressure a tonometer can give; `CHOICE` is one value off
that field's own list and `MULTI` is several — the presenting complaint, the
ocular history — each field's list named in `OPTIONS`. A row in a section is
asked of both eyes and stores `<name>_right` / `<name>_left`; a field is asked
once.

Nothing is mandatory — the doctor fills in what they examined. A blank is not
a finding (the same rule the laboratory keeps, rule 20): blanks are dropped, an
empty tick-list is dropped, and a note whose examination is entirely blank
stores none.

The general medical history, the drugs and the allergies are **not** here:
they are the patient's health record (the nine tiles), read on the chart. The
consultation's own fields carry the assessment and the plan. `describe()` at
the foot reads a stored examination back through the catalogue's labels — and
still shows a key the catalogue no longer names, because the note keeps what
was recorded whatever this file says later.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

SHORT, TEXT, NUMBER, CHOICE, MULTI = "short", "text", "number", "choice", "multi"

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

# What the eye doctor may tick. A list is a *finding*, so the options are
# stored on the note as the values below and read back through these labels —
# the note keeps what was ticked, whatever this list says later.
EYE_COMPLAINTS = [
    ("blurred_vision", "Blurred vision"),
    ("eye_pain", "Eye pain"),
    ("redness", "Redness"),
    ("itching", "Itching"),
    ("discharge", "Discharge"),
    ("watering", "Watering"),
    ("photophobia", "Photophobia"),
    ("foreign_body", "Foreign body sensation"),
    ("double_vision", "Double vision"),
    ("poor_night_vision", "Poor night vision"),
    ("floaters", "Floaters"),
    ("flashes", "Flashes"),
    ("headache", "Headache"),
    ("trauma", "Trauma"),
]

OCULAR_HISTORY = [
    ("previous_eye_disease", "Previous eye disease"),
    ("previous_eye_surgery", "Previous eye surgery"),
    ("previous_eye_trauma", "Previous eye trauma"),
    ("previous_eye_infection", "Previous eye infection"),
    ("glaucoma", "Glaucoma"),
    ("cataract", "Cataract"),
    ("retinal_disease", "Retinal disease"),
    ("refractive_error", "Refractive error"),
    ("contact_lens_use", "Contact lens use"),
    ("previous_glasses", "Previous glasses"),
]

ONSET = [("sudden", "Sudden"), ("gradual", "Gradual"), ("unknown", "Not known")]
SEVERITY = [("mild", "Mild"), ("moderate", "Moderate"), ("severe", "Severe")]
# "Not examined" is a finding in its own right and the reason nothing here is
# mandatory: a blank means the box was never filled, this means it was looked
# at and not assessed.
NORMALITY = [("normal", "Normal"), ("abnormal", "Abnormal"), ("not_assessed", "Not assessed")]
PRESENCE = [("present", "Present"), ("absent", "Absent"), ("not_assessed", "Not assessed")]

# Field base name -> its options. A choice or multi-choice field without an
# entry here would have nothing to offer, so `_fields()` refuses to build one.
OPTIONS = {
    "iop_method": IOP_METHODS,
    "complaints": EYE_COMPLAINTS,
    "ocular_history": OCULAR_HISTORY,
    "complaint_onset": ONSET,
    "complaint_severity": SEVERITY,
    "pupil": NORMALITY,
    "pupil_reaction": NORMALITY,
    "rapd": PRESENCE,
}

MAX_LENGTH = {SHORT: 60, TEXT: 1000}

# (key, label, [paired right/left rows], [single fields])
# A paired row stores `<name>_right` and `<name>_left`.
SECTIONS = [
    ("complaint", "Presenting complaint", [], [
        ("complaints", "What brought them in", MULTI),
        ("complaint_other", "Other complaint", SHORT),
        ("complaint_duration", "Duration", SHORT),
        ("complaint_onset", "Onset", CHOICE),
        ("complaint_severity", "Severity", CHOICE),
        ("associated_symptoms", "Associated symptoms", TEXT),
    ]),
    # The *ocular* history only. General medical history, drugs and allergies
    # are the patient's health record (the nine tiles) and are read there —
    # repeating them here would be a second place to keep them in step.
    ("ocular_history", "Ocular history", [], [
        ("ocular_history", "Past eye history", MULTI),
        ("ocular_history_notes", "Details", TEXT),
    ]),
    ("vision", "Visual acuity", [
        ("distance", "Distance (unaided)", SHORT),
        ("corrected", "With correction", SHORT),
        ("pinhole", "Pinhole", SHORT),
        ("near", "Near", SHORT),
    ], []),
    # Written the way a prescription is written — "+1.25", "-0.50", "180" —
    # so the boxes are text. A number input silently discards a leading "+".
    ("refraction", "Refraction", [
        ("sphere", "Sphere", SHORT),
        ("cylinder", "Cylinder", SHORT),
        ("axis", "Axis", SHORT),
        ("add", "Add", SHORT),
        ("refraction", "As written", SHORT),
    ], []),
    ("pressure", "Eye pressure", [
        ("iop", "IOP (mmHg)", NUMBER),
    ], [
        ("iop_method", "Measurement method", CHOICE),
    ]),
    ("pupils", "Pupils", [
        ("pupil", "Pupil", CHOICE),
        ("pupil_reaction", "Reaction to light", CHOICE),
    ], [
        ("rapd", "RAPD", CHOICE),
    ]),
    ("anterior", "Anterior segment", [
        ("lids", "Lids", TEXT),
        ("conjunctiva", "Conjunctiva", TEXT),
        ("sclera", "Sclera", TEXT),
        ("cornea", "Cornea", TEXT),
        ("anterior_chamber", "Anterior chamber", TEXT),
        ("iris_pupil", "Iris / pupil", TEXT),
        ("lens", "Lens", TEXT),
    ], []),
    ("posterior", "Posterior segment", [
        ("optic_disc", "Optic disc", TEXT),
        ("macula", "Macula", TEXT),
        ("vessels", "Vessels", TEXT),
        ("retina", "Retina", TEXT),
        ("posterior_other", "Other", TEXT),
    ], []),
    ("other", "Other", [], [
        ("eye_movements", "Eye movements", TEXT),
        ("visual_fields", "Visual fields", TEXT),
        ("follow_up", "Follow-up", TEXT),
    ]),
]

EYES = (("right", "Right eye"), ("left", "Left eye"))


def _options_for(name, kind):
    """
    The options a choice or multi-choice field offers, by its base name.

    A field of either kind with no entry in `OPTIONS` would render an empty
    box and accept nothing, so it is a mistake worth failing on at import
    rather than at the bench.
    """
    if kind not in (CHOICE, MULTI):
        return None
    options = OPTIONS.get(name)
    if not options:
        raise RuntimeError(f"Eye examination field {name!r} is a {kind} with no options.")
    return options


def _fields():
    fields = {}
    for _, section_label, paired, single in SECTIONS:
        for name, label, kind in paired:
            options = _options_for(name, kind)
            for eye, eye_label in EYES:
                fields[f"{name}_{eye}"] = {"label": f"{label} — {eye_label.lower()}",
                                           "kind": kind, "options": options}
        for name, label, kind in single:
            fields[name] = {"label": label, "kind": kind, "options": _options_for(name, kind)}
    return fields


# Every key an examination may hold, with its kind.
FIELDS = _fields()


def schema():
    """The definition as the note form reads it."""
    def spec(name, kind):
        out = {"kind": kind}
        if kind in MAX_LENGTH:
            out["max_length"] = MAX_LENGTH[kind]
        if kind == NUMBER:
            out.update(min=float(IOP_MIN), max=float(IOP_MAX), unit="mmHg")
        options = _options_for(name, kind)
        if options:
            out["choices"] = [{"value": value, "label": label} for value, label in options]
        return out

    return {
        "eyes": [{"key": eye, "label": label} for eye, label in EYES],
        "sections": [
            {
                "key": key, "label": label,
                "rows": [{"name": name, "label": row_label,
                          **{eye: f"{name}_{eye}" for eye, _ in EYES}, **spec(name, kind)}
                         for name, row_label, kind in paired],
                "fields": [{"key": name, "label": field_label, **spec(name, kind)}
                           for name, field_label, kind in single],
            }
            for key, label, paired, single in SECTIONS
        ],
    }


def _blank(value):
    # An empty list is the multi-choice equivalent of an empty box: nothing
    # was ticked, so there is nothing to store.
    if value is None or value == []:
        return True
    return isinstance(value, str) and not value.strip()


def _clean_multi(value, options):
    """
    What was ticked, in the order the catalogue lists it, with blanks dropped
    and each value ticked once however many times it arrives. Anything not on
    the list is refused by name rather than quietly ignored — a complaint the
    server drops is one the doctor believes they recorded.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("Choose from the list.")
    allowed = [option for option, _ in options]
    chosen, unknown = [], []
    for item in value:
        if _blank(item):
            continue
        if not isinstance(item, str) or item not in allowed:
            unknown.append(str(item))
        elif item not in chosen:
            chosen.append(item)
    if unknown:
        raise ValueError(f"Not on the list: {', '.join(sorted(unknown))}.")
    return [option for option in allowed if option in chosen]


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
        elif kind == MULTI:
            try:
                chosen = _clean_multi(raw, spec["options"])
            except ValueError as exc:
                errors[key] = [str(exc)]
            else:
                if chosen:
                    cleaned[key] = chosen
        elif kind == CHOICE:
            if raw not in dict(spec["options"]):
                errors[key] = ["Choose one of the options."]
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


def describe(examination):
    """
    The examination as a person reads it: `[(label, value), …]` in the order
    `SECTIONS` defines, with a choice shown by its label and a ticked list
    joined up.

    One reader for every screen that has to show a stored examination back —
    Django admin today — so a finding is never displayed as its raw stored
    value. It reads the *stored* keys through the current catalogue and falls
    back to the raw value for anything the catalogue no longer names, because
    a note keeps what was recorded whatever the list says later (rule 28).
    """
    if not examination:
        return []
    described = []
    for key, spec in FIELDS.items():
        if key not in examination:
            continue
        value, options = examination[key], spec.get("options")
        labels = dict(options) if options else {}
        if spec["kind"] == MULTI and isinstance(value, list):
            shown = ", ".join(labels.get(item, item) for item in value)
        else:
            shown = labels.get(value, value)
        if shown not in (None, ""):
            described.append((spec["label"], str(shown)))
    # Anything the catalogue has since dropped is still shown, by its key —
    # silently hiding a recorded finding is the one thing this must not do.
    described += [(key, str(value)) for key, value in examination.items()
                  if key not in FIELDS and value not in (None, "", [])]
    return described
