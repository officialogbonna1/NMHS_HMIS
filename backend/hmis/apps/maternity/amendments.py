"""
Correcting a maternity record — **what may change, what may never, and what
it costs to change it.**

This is `clinical/services.py` applied to maternity. The consultation note's
amendment rules are the HMIS's established answer to "a clinician typed the
wrong thing": archive what the record said *before* touching it, record why,
stamp who and when, and derive the diff for display rather than storing it
(rule 45). Nothing here invents a second framework — it is that pattern, over
maternity's records, with one trail instead of five.

**Vitals is not that pattern, and cannot be.** `clinical.Vitals` uses
`LockedRecordMixin` and has no edit path at all: rule 2 says a correction is a
*new reading*, and rule 11 repeats it for the ward. So a maternity blood
pressure entered as 120/80 is never amended to 125/80 — a second reading is
taken and both stay on the chart, which is why the triage tab shows a history.
That is deliberate and this module does not change it.

**Three kinds of field.**

- `IMMUTABLE` — identity and event facts. Which patient, which pregnancy,
  which labour, who recorded it, when it was filed. These are what make the
  record *this* record; changing one does not correct a mistake, it turns the
  row into a different row. Refused for everybody, including an admin, on
  every path. A record filed against the wrong pregnancy is cancelled and
  re-entered, the way a wrong charge is cancelled rather than edited (rule 38).
- `AMENDABLE` — the clinical findings somebody can genuinely mistype. Changed
  only through the amendment flow: a reason, a snapshot, an audit row.
- Everything else is already read-only on the serializer and stays so.
"""
from apps.core.services import audit_event

from .models import (Delivery, LabourEpisode, MaternityAmendment, MaternityEncounter,
                     Pregnancy)

#: The answer the API gives when a correction arrives with no reason, so the
#: form recognises it rather than parsing prose. The consultation note and the
#: released laboratory result already answer with this exact code (rules 22,
#: 45) — one vocabulary for one refusal.
REASON_REQUIRED = "amendment_reason_required"

#: And when a request tries to change a fact that identifies the record.
IMMUTABLE_FIELD = "immutable_field"

#: What each record lets a correction touch, and what it never does.
#:
#: Read as: {model: {"immutable": (...), "amendable": (...)}}. A field in
#: neither is read-only on the serializer already — `number`, `status`,
#: `outcome` and `ended_on` move through `close/`, not through a form.
RULES = {
    Pregnancy: {
        # Whose pregnancy and which one. `patient` was writable, so a PATCH
        # could move Dora's pregnancy — and its labour, delivery and babies —
        # onto another patient's record.
        "immutable": ("patient", "number", "opened_by"),
        # The dates and the obstetric history she reported, which are exactly
        # the things a booking clerk mistypes.
        "amendable": ("lmp", "edd", "gravida", "para", "notes"),
    },
    MaternityEncounter: {
        "immutable": ("pregnancy", "visit_type", "visit", "seen_on", "recorded_by"),
        "amendable": ("summary", "provider", "status"),
    },
    LabourEpisode: {
        "immutable": ("pregnancy", "opened_by"),
        "amendable": ("onset", "started_at", "admission", "notes"),
    },
    Delivery: {
        "immutable": ("labour", "recorded_by"),
        "amendable": ("delivered_at", "delivery_type", "outcome", "complications",
                      "doctor", "midwife", "estimated_blood_loss_ml",
                      "placenta_complete", "placenta_notes", "maternal_condition",
                      "notes"),
    },
}


def rules_for(instance):
    return RULES.get(type(instance), {"immutable": (), "amendable": ()})


def immutable_fields(instance):
    return set(rules_for(instance)["immutable"])


def amendable_fields(instance):
    return set(rules_for(instance)["amendable"])


#: Fields whose name does not read well capitalised. Everything else is its
#: own name with the underscores taken out.
LABELS = {
    "lmp": "LMP", "edd": "EDD",
    "gravida": "Gravida", "para": "Para",
    "estimated_blood_loss_ml": "Estimated blood loss (ml)",
    "apgar_1_min": "Apgar (1 min)", "apgar_5_min": "Apgar (5 min)",
}


def label_for(field):
    return LABELS.get(field, field.replace("_", " ").capitalize())


def blocked_changes(instance, attrs):
    """
    The identity facts this update is trying to change, if any.

    One definition, called twice: the view asks first so the refusal carries a
    **flat** `code` the frontend can read (rule 55 — DRF wraps a serializer's
    codes in lists), and the serializer asks again so any other caller meets
    the same rule. Two callers, one answer.

    A field named with the value it already holds is not a change: a form that
    round-trips the whole record must not be refused for saying nothing.
    """
    protected = immutable_fields(instance)
    return sorted(field for field, value in (attrs or {}).items()
                  if field in protected and value != getattr(instance, field, None))


def reason_choices():
    """The reasons a maternity record may be corrected for, as the wire
    carries them — the consultation note's shape, so one form renders both."""
    return [{"value": value, "label": label}
            for value, label in MaternityAmendment.REASONS]


def is_valid_reason(reason):
    return (reason or "").strip() in dict(MaternityAmendment.REASONS)


def may_amend(user, instance):
    """
    Who may correct a maternity record: **the person who recorded it, or an
    administrator** — `clinical.serializers.may_amend`'s rule, unchanged.

    Deliberately *not* everyone the department authorises. Working in Maternity
    is what lets a midwife open the ward's records (rule: the team is the
    department); correcting somebody else's entry is a different act, and the
    consultation note has always drawn that line at the author. An assigned
    nurse or doctor gets nothing extra here either — responsibility is not a
    permission (rule: Phase 3.2).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    author = getattr(instance, "recorded_by_id", None) or getattr(instance, "opened_by_id", None)
    return author is not None and author == user.pk


def snapshot_of(instance):
    """
    What the record says right now, as JSON.

    A dict rather than a column per field: five models with a dozen amendable
    fields each would be sixty columns nothing ever filters, groups or joins
    on — the reason `PatientRoute.result_data` is JSON too (rule 53). It is
    read back through `changes_for`, never queried into.
    """
    snapshot = {}
    for field in sorted(amendable_fields(instance)):
        snapshot[field] = _plain(instance, field)
    return snapshot


def _plain(instance, field):
    """One field as JSON: an id for a relation, a string for a date, the value
    otherwise. It has to survive a related row being renamed or retired."""
    model_field = instance._meta.get_field(field)
    if model_field.many_to_many:
        return sorted(str(obj) for obj in getattr(instance, field).all())
    value = getattr(instance, field, None)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def archive(*, instance, actor, reason, detail=""):
    """
    Keep what the record says **before** the caller changes it.

    `instance` must be the pre-edit state, read fresh from the database — an
    instance a serializer has already written over records the new values as
    though they were the old ones, which is worse than no trail at all. That
    is the mistake `clinical.services.note_before()` exists to avoid, and the
    caller here is held to the same rule.
    """
    return MaternityAmendment.objects.create(
        record=instance, amended_by=actor,
        reason=(reason or "").strip(), detail=(detail or "").strip(),
        previous=snapshot_of(instance),
    )


def changes_for(amendment, instance):
    """
    What this amendment alone changed: its snapshot against what the record
    says now, field by field.

    **Derived, never stored** (rule 45). An amendment holds what the record
    said *before*; what it said after is the next amendment's snapshot, or the
    record itself for the newest one — so a stored diff would be a third copy
    free to disagree with both.
    """
    after = snapshot_of(instance)
    changes = []
    for field, was in (amendment.previous or {}).items():
        now = after.get(field)
        if was != now:
            changes.append({"field": field, "label": label_for(field),
                            "from": was, "to": now})
    return changes


def audit_amended(*, instance, actor, reason, request=None):
    """The event, on the existing `AuditLog` — there is no second audit system."""
    patient = _patient_of(instance)
    return audit_event(
        actor=actor, action="maternity.record_amended", instance=instance,
        request=request,
        details={"record": type(instance).__name__,
                 "patient": patient.display_name if patient else None,
                 "patient_number": patient.patient_number if patient else None,
                 "reason": (reason or "").strip()})


def _patient_of(instance):
    for path in ("patient", "pregnancy.patient", "labour.pregnancy.patient"):
        target = instance
        for part in path.split("."):
            target = getattr(target, part, None)
            if target is None:
                break
        if target is not None and hasattr(target, "patient_number"):
            return target
    return None
