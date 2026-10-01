"""
The structured half of an imaging report.

**Why a structure at all.** Every unit that works from a `PatientRoute` writes
its finding into `result`, one block of prose, and for a dressing or a
refraction that is the right size. A scan report is not prose: it is a
technique, what was seen, the measurements taken and a conclusion, and a
sonographer typing all four into one box produces something nobody can read
back in six months — the reason the laboratory has parameters and the eye
doctor has an examination.

**Why not a laboratory-shaped one.** A `LabParameter` is a measurement with a
reference range and a flag; almost nothing in an ultrasound report is. So this
is the eye examination's shape rather than the bench's: a fixed catalogue of
sections, defined once here, validated on the server and served to the form
(`GET /api/patient-routes/report-fields/`), so the screen and the rules cannot
drift apart.

**What it does not do.** It stores no new record. The sections live on
`PatientRoute.result_data`, beside the rendered text in `result` — which every
existing reader already uses: the chart, the printed report, the permanent
`MedicalTest` copy, the doctor's notification. A unit that writes plain prose
is untouched, and nothing here is mandatory: a blank section is dropped, and a
report that is one line of findings is a report (the laboratory's rule 20,
applied to imaging).

Who signed it and when is `result_by` / `result_at`, and whether it has been
released is the route's own status — neither is repeated here, because two
columns that must agree are two columns free to disagree.
"""
import json

from django.core.exceptions import ValidationError

class ReportError(ValidationError):
    """
    A refused report, keeping its errors' shape — sections are lists of
    messages, a procedure's materials are nested by row
    (`{"materials": {"0": {"quantity_received": [...]}}}`), which Django's own
    `ValidationError` cannot hold. `message_dict` is what the views answer with.
    """

    def __init__(self, errors):
        super().__init__("The report could not be saved.")
        self._errors = errors

    @property
    def message_dict(self):
        return self._errors


SHORT, TEXT = "short", "text"
MAX_LENGTH = {SHORT: 255, TEXT: 4000}

#: purpose -> the sections its report is written in. A purpose that is not
#: here keeps the single free-text finding it has always had.
REPORTS = {
    "ultrasound": [
        ("technique", "Technique", SHORT,
         "How the study was performed — e.g. transabdominal, full bladder."),
        ("findings", "Findings", TEXT,
         "What was seen, organ by organ."),
        ("measurements", "Measurements", TEXT,
         "The figures taken — e.g. GA 28w3d by BPD, AFI 12cm, RK 10.2cm."),
        ("impression", "Impression", TEXT,
         "The conclusion the referring doctor reads first."),
    ],
    # The Procedure Department (`workflow/procedures.py`). Who performed it and
    # when are `result_by` / `result_at`, and who asked is `routed_by` — none
    # of them is repeated as a section.
    "procedure": [
        ("procedure_performed", "Procedure performed", SHORT,
         "What was done — e.g. wound debridement and dressing, left leg."),
        ("clinical_notes", "Procedure notes", TEXT,
         "How it was done: anaesthesia, technique, anything that happened."),
        ("findings", "Findings", TEXT,
         "What was found."),
        ("outcome", "Outcome", TEXT,
         "How the patient tolerated it and the state they left in."),
        ("follow_up", "Follow-up", TEXT,
         "What happens next — e.g. review in 48 hours, change dressing daily."),
    ],
}

#: Purposes whose report also carries a list of materials used
#: (`workflow/procedures.clean_materials`). Every other purpose refuses one.
MATERIAL_PURPOSES = {"procedure"}


def sections_for(purpose):
    """The sections this purpose's report has, or None for free prose."""
    return REPORTS.get(purpose)


def schema(purpose):
    """The definition as the station's form reads it."""
    sections = sections_for(purpose)
    if sections is None:
        return None
    data = {
        "purpose": purpose,
        "sections": [
            {"key": key, "label": label, "kind": kind, "hint": hint,
             "max_length": MAX_LENGTH[kind]}
            for key, label, kind, hint in sections
        ],
    }
    if purpose in MATERIAL_PURPOSES:
        from . import procedures
        # The materials table, from the same definition the server validates
        # against, so the form holds no copy of its columns or limits.
        data["materials"] = {
            "max_rows": procedures.MAX_ROWS,
            "text_fields": [{"key": key, "label": procedures.LABELS[key], "max_length": limit}
                            for key, limit in procedures.TEXT_FIELDS.items()],
            "quantities": [{"key": key, "label": procedures.LABELS[key]}
                           for key in procedures.QUANTITIES],
            "rule": "Received = Used + Remaining + Wastage",
        }
    return data


def clean(purpose, value):
    """
    The report as it may be stored, or None when there is nothing in it.

    Refuses — with `ValidationError` keyed by section — a body that is not an
    object, a key this purpose's report does not have, and text that is too
    long. A blank section is dropped rather than refused: the bench fills in
    what it has.
    """
    sections = sections_for(purpose)
    if value in (None, "", {}):
        return None
    if sections is None:
        raise ValidationError("This referral's report is written as a single finding.")
    if not isinstance(value, dict):
        raise ValidationError("The report must be a set of sections.")

    # A procedure's materials ride beside its sections. A multipart post (the
    # one carrying an uploaded document) cannot nest a list, so it sends the
    # rows as one JSON string in `report.materials`; JSON sends the list.
    value = dict(value)
    materials, material_errors = None, {}
    if "materials" in value:
        raw = value.pop("materials")
        if purpose not in MATERIAL_PURPOSES:
            material_errors = {"materials": ["This report does not record materials."]}
        else:
            from . import procedures
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw) if raw.strip() else []
                except ValueError:
                    raw = None
                    material_errors = {"materials": ["The materials could not be read."]}
            if not material_errors:
                try:
                    materials = procedures.clean_materials(raw)
                except procedures.MaterialsError as exc:
                    material_errors = {"materials": exc.errors}

    allowed = {key: kind for key, _, kind, _ in sections}
    errors, cleaned = {}, {}
    for key, raw in value.items():
        kind = allowed.get(key)
        if kind is None:
            errors[key] = ["Not a section of this report."]
            continue
        if raw is None:
            continue
        if not isinstance(raw, str):
            errors[key] = ["Enter this section as text."]
            continue
        text = raw.strip()
        if not text:
            continue
        if len(text) > MAX_LENGTH[kind]:
            errors[key] = [f"Keep this to {MAX_LENGTH[kind]} characters."]
            continue
        cleaned[key] = text
    errors.update(material_errors)
    if errors:
        raise ReportError(errors)
    if materials:
        cleaned["materials"] = materials
    return cleaned or None


def render(purpose, report, *, include_materials=True):
    """
    The report as the one line of text every existing reader already shows.

    `result` stays the record the chart, the printed sheet, the permanent
    `MedicalTest` copy and the doctor's notification read, so nothing had to
    learn about sections to keep working. Empty sections are left out, for the
    reason rule 45 gives: a heading with nothing under it reads as "looked,
    found nothing".
    """
    sections = sections_for(purpose)
    if not report or sections is None:
        return ""
    labels = {key: label for key, label, _, _ in sections}
    blocks = [f"{labels[key]}:\n{report[key]}" for key, _, _, _ in sections if report.get(key)]
    if include_materials and report.get("materials"):
        from . import procedures
        blocks.append(procedures.render_materials(report["materials"]))
    return "\n\n".join(blocks)
