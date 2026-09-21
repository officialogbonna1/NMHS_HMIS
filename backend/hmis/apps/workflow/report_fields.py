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
from django.core.exceptions import ValidationError

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
}


def sections_for(purpose):
    """The sections this purpose's report has, or None for free prose."""
    return REPORTS.get(purpose)


def schema(purpose):
    """The definition as the station's form reads it."""
    sections = sections_for(purpose)
    if sections is None:
        return None
    return {
        "purpose": purpose,
        "sections": [
            {"key": key, "label": label, "kind": kind, "hint": hint,
             "max_length": MAX_LENGTH[kind]}
            for key, label, kind, hint in sections
        ],
    }


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
    if errors:
        raise ValidationError(errors)
    return cleaned or None


def render(purpose, report):
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
    return "\n\n".join(f"{labels[key]}:\n{report[key]}"
                       for key, _, _, _ in sections if report.get(key))
