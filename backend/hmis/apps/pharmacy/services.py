"""
Prescribing and dispensing.

The two are deliberately separate steps: a doctor writes the prescription,
and a pharmacist fills it. Stock only ever moves inside
`dispense_prescription()` — it holds the whole deduction in one
`transaction.atomic` block with `select_for_update()` on the batches, so two
pharmacists filling the last units at the same time can't both succeed, and
every unit that leaves a batch has a matching StockMovement audit row.

Nothing outside this module should write to Batch.quantity for a
prescription; add a function here instead of routing around these.
"""
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.inventory.models import Batch, StockMovement
from apps.pharmacy.models import Prescription
from apps.billing.services import add_charge


class OutOfStockError(ValidationError):
    pass


class AlreadyDispensedError(ValidationError):
    pass


def available_quantity(item):
    """Units on the shelf that haven't expired."""
    return sum(
        b.quantity for b in Batch.objects.filter(item=item, quantity__gt=0).exclude(expiry_date__lt=_today())
    )


@transaction.atomic
def create_prescription(*, patient, doctor, item, quantity, dosage_instructions=""):
    """
    A doctor's request to the pharmacy. Stock is checked so a doctor is told
    straight away that a drug can't be filled, but nothing is deducted or
    reserved here — the pharmacist re-checks under lock when they dispense.
    """
    if quantity < 1:
        raise ValidationError("Quantity must be at least 1.")
    available = available_quantity(item)
    if available < quantity:
        raise OutOfStockError(
            f"Only {available} unit(s) of {item.name} in stock — cannot prescribe {quantity}."
        )
    return Prescription.objects.create(
        patient=patient, doctor=doctor, item=item, quantity=quantity,
        dosage_instructions=dosage_instructions, status="pending",
    )


@transaction.atomic
def create_prescriptions(*, patient, doctor, lines):
    """
    A whole script in one act. `lines` is [{item, quantity,
    dosage_instructions}, …].

    All-or-nothing on purpose: a doctor writing five drugs and having the
    third rejected for stock must not leave two queued at the pharmacy and
    three lost — they would have no way to tell which. The transaction rolls
    the lot back and the message names the drug that failed.
    """
    if not lines:
        raise ValidationError("Add at least one drug to the prescription.")

    seen = set()
    for line in lines:
        item = line["item"]
        if item.pk in seen:
            raise ValidationError(
                f"{item.name} is on this prescription twice — put the whole amount on one line."
            )
        seen.add(item.pk)

    return [
        create_prescription(
            patient=patient, doctor=doctor, item=line["item"], quantity=line["quantity"],
            dosage_instructions=line.get("dosage_instructions", ""),
        )
        for line in lines
    ]


@transaction.atomic
def dispense_prescription(*, prescription, pharmacist):
    """
    Hand the drugs over: deduct FEFO (earliest-expiring non-expired batch
    first), log a StockMovement per batch touched, and raise the charge the
    pharmacy then collects against.
    """
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if prescription.status != "pending":
        raise AlreadyDispensedError(
            f"This prescription is already {prescription.get_status_display().lower()}."
        )

    batches = (
        Batch.objects.select_for_update()
        .filter(item=prescription.item, quantity__gt=0)
        .exclude(expiry_date__lt=_today())
        .order_by("expiry_date")
    )

    available = sum(b.quantity for b in batches)
    if available < prescription.quantity:
        raise OutOfStockError(
            f"Only {available} unit(s) of {prescription.item.name} available — "
            f"cannot dispense {prescription.quantity}."
        )

    remaining = prescription.quantity
    dispensed_value = 0
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        batch.quantity -= take
        batch.save(update_fields=["quantity"])
        StockMovement.objects.create(
            batch=batch, change=-take, reason="prescription",
            performed_by=pharmacist, reference=f"prescription:{prescription.id}",
        )
        dispensed_value += take * batch.sale_price
        remaining -= take

    prescription.status = "dispensed"
    prescription.dispensed_by = pharmacist
    prescription.dispensed_at = timezone.now()
    prescription.dispensed_value = dispensed_value
    prescription.save(update_fields=["status", "dispensed_by", "dispensed_at", "dispensed_value"])

    # The charge is created only after stock movement succeeds, keeping stock
    # and the patient ledger in the same transaction.
    add_charge(
        patient=prescription.patient, description=f"Medication: {prescription.item.name}",
        amount=dispensed_value, created_by=pharmacist,
        source_type="prescription", source_id=prescription.id,
    )
    return prescription


@transaction.atomic
def cancel_prescription(*, prescription, actor, reason=""):
    """Pull an unfilled prescription. Dispensed stock can't be un-dispensed."""
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if prescription.status != "pending":
        raise ValidationError(
            f"Cannot cancel a prescription that is {prescription.get_status_display().lower()}."
        )
    prescription.status = "cancelled"
    prescription.cancelled_reason = reason
    prescription.save(update_fields=["status", "cancelled_reason"])
    return prescription


@transaction.atomic
def create_prescription_and_dispense(*, patient, doctor, item, quantity, dosage_instructions=""):
    """
    Write and fill in one step, for the counter sale case where the same
    person does both. Normal ward flow goes through `create_prescription()`
    and then `dispense_prescription()` so the pharmacist is the one who
    releases the stock.
    """
    prescription = create_prescription(
        patient=patient, doctor=doctor, item=item, quantity=quantity,
        dosage_instructions=dosage_instructions,
    )
    return dispense_prescription(prescription=prescription, pharmacist=doctor)


def _today():
    return timezone.now().date()
