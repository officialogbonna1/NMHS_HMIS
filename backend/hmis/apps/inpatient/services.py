"""
Discharging a patient — the one implementation.

The ward has discharged patients since before this module existed
(`DischargeSummaryViewSet`, worked by WARD_ROLES and by the eye doctor for
their own patients, rule 43). That has not changed and is not meant to. What
this module does is lift the *rule* out of that viewset so the new Admin
Discharge workspace runs the same code rather than a second copy of it: one
discharge model, one service, two doors — the same shape rule 45 gives the
clinical note and rule 31 gives configuration.

Three things it fixed on the way out of the viewset, all of which were latent:

1. **The status was checked after the record was written.** `perform_create`
   saved the `DischargeSummary`, *then* asked whether the admission was still
   active — so the check could only ever raise after the fact and relied on the
   transaction to undo it.
2. **A second discharge was a 500.** `DischargeSummary.admission` is a
   `OneToOneField`, so discharging an already-discharged admission raised
   `IntegrityError` out of the database. It is now a 409 that names the
   existing discharge, which is what rule 9's "show the existing state" asks
   for — and which lets a screen offer to print the letter instead.
3. **Two people pressing at once could both pass the check.** The admission is
   taken under `select_for_update` and the status written compare-and-set, the
   same way rule 38 settles two cashiers on one charge.

**The email is not part of the transaction.** `notifications_email` queues on
commit, so a discharge that rolls back tells nobody, and a Resend failure
cannot undo a discharge. Reopening or reprinting a discharge sends nothing: the
notification is keyed to the `DischargeSummary` row, which is created once.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core import notifications_email as email_events
from apps.core.services import audit_event

from .models import Admission, DischargeSummary


class AlreadyDischarged(ValidationError):
    """
    This admission has been discharged already.

    Carries the existing discharge so the caller can show it rather than
    inventing a second one — a patient may be admitted again another day, and
    that is a new admission, never a second discharge of this one.
    """

    def __init__(self, admission, summary=None):
        self.admission = admission
        self.summary = summary or getattr(admission, "discharge_summary", None)
        reference = getattr(self.summary, "reference", "") or ""
        super().__init__(
            f"This admission was already discharged"
            + (f" ({reference})" if reference else "")
            + ". Open the existing discharge record to view or print it."
        )
        self.code = "already_discharged"


@transaction.atomic
def discharge_patient(*, admission, actor, diagnosis, summary, instructions="",
                      follow_up=None, condition="", notify=True):
    """
    File the discharge and release the bed.

    Returns the `DischargeSummary`. Raises `AlreadyDischarged` if this
    admission has been discharged before, and `ValidationError` if it was never
    active — a cancelled admission is not something anybody leaves.

    `actor` is recorded as `completed_by`: who signed the discharge. Nothing
    here decides *whether* they may — that is the caller's, and the two callers
    answer it differently on purpose (the ward by `BED_BOARD_ROLES` and their
    own patients, the admin workspace by Super Admin alone).
    """
    admission = (Admission.objects.select_for_update()
                 .select_related("patient", "bed", "bed__ward")
                 .get(pk=admission.pk))

    existing = DischargeSummary.objects.filter(admission=admission).first()
    if existing is not None:
        raise AlreadyDischarged(admission, existing)
    if admission.status != "admitted":
        if admission.status == "discharged":
            raise AlreadyDischarged(admission)
        raise ValidationError({"admission": "This admission is not active."})

    if not str(diagnosis or "").strip():
        raise ValidationError({"diagnosis": "A discharge diagnosis is required."})
    if not str(summary or "").strip():
        raise ValidationError({"summary": "A summary of the admission is required."})

    record = DischargeSummary.objects.create(
        admission=admission, completed_by=actor,
        diagnosis=str(diagnosis).strip(), summary=str(summary).strip(),
        instructions=str(instructions or "").strip(),
        follow_up=follow_up or None,
        condition=str(condition or "").strip(),
    )

    # Compare-and-set: only an admission still reading "admitted" is moved, so
    # two people pressing Discharge at the same moment discharge once even on
    # SQLite, where `select_for_update` above does nothing (rule 38).
    now = timezone.now()
    moved = (Admission.objects.filter(pk=admission.pk, status="admitted")
             .update(status="discharged", discharged_at=now, updated_at=now))
    if moved != 1:
        raise AlreadyDischarged(admission, record)
    admission.status, admission.discharged_at = "discharged", now

    audit_event(
        actor=actor, action="admission.discharged", instance=record,
        details={
            "patient": admission.patient.display_name,
            "patient_number": admission.patient.patient_number,
            "admission": admission.pk,
            "discharge_reference": record.reference,
            "ward": getattr(getattr(admission.bed, "ward", None), "name", ""),
            "bed": getattr(admission.bed, "number", ""),
            "admitted_at": admission.admitted_at.isoformat() if admission.admitted_at else None,
            "discharged_at": now.isoformat(),
        },
    )

    if notify:
        email_events.patient_discharged(
            summary=record, admission=admission, patient=admission.patient, actor=actor)

    return record


__all__ = ["AlreadyDischarged", "discharge_patient"]
