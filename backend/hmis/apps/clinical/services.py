"""
The clinical note's amendment rules, in one place.

A consultation note can be corrected from two doors — the HMIS chart
(`ConsultationNoteViewSet`) and Django admin (`ConsultationNoteAdmin`) — and
rule 31's principle applies to behaviour as much as to data: two front doors
onto one model, never two implementations of what that model's rules are.

So the three things a correction must do live here, and both doors call them:
archive what the note said, record why, and write the audit row. The doors
differ only in how they collect the input (a DRF serializer, a ModelForm),
which is all they should differ in.

Nothing here saves the note. The caller does that, inside the same
transaction as `archive()`, because a refused save must never leave a trail
describing an amendment that did not happen.
"""
from apps.core.services import audit_event

from .eye_exam import FIELDS as EYE_FIELDS
from .models import ConsultationNote, ConsultationNoteAmendment

# What the API answers with when an amendment arrives without a reason, so the
# form can recognise it rather than parsing prose. The laboratory's released
# result already uses this shape (rule 22).
REASON_REQUIRED = "amendment_reason_required"


def reason_choices():
    """The reasons a note may be amended for, as the wire carries them."""
    return [{"value": value, "label": label}
            for value, label in ConsultationNoteAmendment.REASONS]


def is_valid_reason(reason):
    return (reason or "").strip() in dict(ConsultationNoteAmendment.REASONS)


def archive(*, note, actor, reason, detail=""):
    """
    Keep what `note` says **right now**, before the caller changes it.

    `note` must be the pre-edit state — read fresh from the database, not the
    instance a form has already mutated — or the trail records the new values
    as though they were the old ones, which is worse than no trail at all.
    """
    return ConsultationNoteAmendment.objects.create(
        note=note, amended_by=actor,
        reason=(reason or "").strip(), detail=(detail or "").strip(),
        **ConsultationNoteAmendment.snapshot_of(note),
    )


def _patient_details(note):
    return {"patient": note.patient.display_name, "patient_number": note.patient.patient_number}


def audit_created(*, note, actor, request=None, source="hmis"):
    """`source` says which door it came through — the only thing that differs."""
    return audit_event(
        actor=actor, action="note.created", instance=note, request=request,
        details={**_patient_details(note), "source": source,
                 "eye_examination": bool(note.eye_examination)},
    )


def audit_amended(*, note, actor, reason, detail="", request=None, source="hmis"):
    return audit_event(
        actor=actor, action="note.amended", instance=note, request=request,
        details={**_patient_details(note), "source": source,
                 # The author never moves, so the trail says whose note was
                 # corrected as well as who corrected it.
                 "author": (note.doctor.get_full_name() or note.doctor.username)
                 if note.doctor else None,
                 "reason": (reason or "").strip(), "detail": (detail or "").strip()},
    )


def note_before(note_or_pk):
    """
    The note as the database still holds it.

    Django admin hands `save_model` an instance the form has already written
    over, so the values needed for the snapshot are gone from it. This is how
    both doors get the same "before".
    """
    pk = getattr(note_or_pk, "pk", note_or_pk)
    return ConsultationNote.objects.select_related("patient", "doctor").get(pk=pk)


# ---------------------------------------------------------------- reading it back

# How each snapshotted field is labelled when the history is read.
FIELD_LABELS = {
    "reason_for_visit": "Reason for visit",
    "chief_complaint": "Chief complaint",
    "note_text": "Notes",
    "diagnosis": "Diagnosis",
    "plan": "Plan",
    "eye_examination": "Eye examination",
}


def describe_eye_value(key, value):
    """A stored eye-examination value as a person reads it — a ticked list
    joined up, a choice by its label, anything else as it stands."""
    if value in (None, "", []):
        return ""
    labels = dict((EYE_FIELDS.get(key) or {}).get("options") or [])
    if isinstance(value, list):
        return ", ".join(labels.get(item, item) for item in value)
    return str(labels.get(value, value))


def _eye_changes(before, after):
    """
    The eye examination is an object, so "it changed" answers nothing. Read it
    back field by field, through the catalogue's own labels, so the history
    says *which* finding moved.
    """
    before, after = before or {}, after or {}
    labels = {key: spec["label"] for key, spec in EYE_FIELDS.items()}
    changes = []
    for key in sorted(set(before) | set(after)):
        was, now = before.get(key), after.get(key)
        if was == now:
            continue
        changes.append({"field": key, "label": labels.get(key, key),
                        "previous": describe_eye_value(key, was),
                        "current": describe_eye_value(key, now)})
    return changes


def _after(amendment):
    """
    What the note said once `amendment` had been applied: the snapshot of the
    amendment written next, or the note as it stands for the most recent one.

    This is why no `new_*` column exists — storing both halves would be the
    same fact twice, free to disagree.
    """
    later = [a for a in amendment.note.amendments.all() if a.created_at > amendment.created_at]
    if later:
        nearest = min(later, key=lambda a: a.created_at)
        return {field: getattr(nearest, column)
                for column, field in ConsultationNoteAmendment.SNAPSHOT_FIELDS}
    note = amendment.note
    return {field: getattr(note, field)
            for _, field in ConsultationNoteAmendment.SNAPSHOT_FIELDS}


def changes_for(amendment):
    """
    What this one correction changed, as `[{field, label, previous, current}]`.

    Derived, never stored, and read by both doors: the chart's amendment
    history and Django admin's inline. Dumping the whole snapshot instead —
    which the admin did at first — buries a one-word correction under sixty
    unchanged eye findings.
    """
    after = _after(amendment)
    changes = []
    for column, field in ConsultationNoteAmendment.SNAPSHOT_FIELDS:
        was, now = getattr(amendment, column), after.get(field)
        if field == "eye_examination":
            changes.extend(_eye_changes(was, now))
            continue
        if (was or "") == (now or ""):
            continue
        changes.append({"field": field, "label": FIELD_LABELS.get(field, field),
                        "previous": was or "", "current": now or ""})
    return changes
