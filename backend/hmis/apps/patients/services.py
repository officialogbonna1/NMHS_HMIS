"""
Purging a patient — the Super Admin's deliberate way past rule 37, and only
from Django admin.

`DELETE /api/patients/<uuid>/` still refuses a patient with history (409,
`PROTECTED_HISTORY`), and every HMIS screen keeps that answer. This is the
other door, for a registration that was never a real patient — a demo, a
screenshot, a run through the till — whose bills and payments are as
fictional as the person. It removes the patient and everything the hospital
would otherwise keep about them, in one transaction, and says so in the audit
log first.

It cannot be undone, and it changes the past: a purged payment leaves the
collections of the period it was taken in, and a purged charge leaves its
department's column. That is the point of it, and the reason it is the Super
Admin's alone.

What goes is every row in `PURGE_ORDER` and whatever cascades from them —
allocations, result values and amendments, routes, bed transfers and discharge
summaries — then everything `Patient.delete()` has always taken: the ledger,
vitals, notes, the nine tiles, appointments and prescriptions.

What stays: sales (their `patient` is SET_NULL), every stock movement (none
points at a prescription, so a shelf and its log still agree), the beds (an
admission going frees its bed, because occupancy is read off the admissions),
and the audit row.
"""
from decimal import Decimal

from django.contrib.admin.utils import NestedObjects
from django.core.exceptions import PermissionDenied
from django.db import router, transaction
from django.db.models import Sum

from apps.accounts.permissions import is_super_admin
from apps.core.config import references_to
from apps.core.services import audit_event

from .models import Patient

#: Every PROTECT relation a patient has, in an order that deletes cleanly —
#: `Refund.payment` is PROTECT as well, so refunds go before payments. A new
#: PROTECT foreign key to `Patient` belongs here; `test_admin_purge.py` walks
#: the model and fails until it is.
PURGE_ORDER = (
    "refunds", "adjustments", "deferrals", "payments", "charges",
    "lab_orders", "investigation_orders", "admissions", "visits",
)


def purge_history(patient):
    """`{"charges": 4, "payments": 3}` — the kept history a purge would remove."""
    return references_to(patient, PURGE_ORDER)


def purge_preview(patient):
    """
    Everything a purge would delete besides the patient, as
    `[(verbose name plural, count)]`. Built by Django's own deletion collector,
    so the confirmation page cannot promise less than what actually goes.
    """
    collector = NestedObjects(using=router.db_for_write(Patient))
    collector.collect([patient])
    # PROTECT stops the collector at the kept history; collect that too.
    for name in PURGE_ORDER:
        collector.collect(list(getattr(patient, name).all()))
    rows = [(str(model._meta.verbose_name_plural), len(objs))
            for model, objs in collector.model_objs.items() if model is not Patient]
    return sorted(rows)


def money_on_record(patient):
    """What was charged, paid and refunded — the figures a purge takes out of the reports."""
    def total(rows):
        return rows.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return {"charged": total(patient.charges.all()),
            "paid": total(patient.payments.all()),
            "refunded": total(patient.refunds.all())}


def purge_patient(*, patient, actor, reason, confirmation, request=None):
    """
    Delete a patient and their whole record. Returns the history counts removed.

    Refused before anything is written unless the actor is the Super Admin, the
    confirmation is the patient's own hospital number and there is a reason.
    """
    if not is_super_admin(actor):
        raise PermissionDenied("Only the Super Admin can purge a patient.")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Give a reason for purging this patient.")
    if str(confirmation or "").strip().upper() != (patient.patient_number or "").upper():
        raise ValueError("Type the patient's hospital number to confirm.")

    with transaction.atomic():
        patient = Patient.objects.select_for_update().get(pk=patient.pk)
        removed = purge_history(patient)
        money = money_on_record(patient)
        # Written first, with the identity in `details`: `object_id` would
        # point at nothing afterwards — the same shape as `patients.deleted`.
        audit_event(
            actor=actor, action="patients.purged", request=request,
            details={"patient_number": patient.patient_number, "uuid": str(patient.uuid),
                     "name": patient.display_name, "sex": patient.sex,
                     "registered_at": patient.created_at.isoformat(),
                     "reason": reason, "removed": removed,
                     # Quantized: SQLite's Sum hands back Decimal("8500").
                     "money": {key: str(value.quantize(Decimal("0.01")))
                               for key, value in money.items()}},
        )
        for name in PURGE_ORDER:
            getattr(patient, name).all().delete()
        patient.delete()
    return removed
