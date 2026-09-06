"""
Gives a patient the two identifiers described in `apps/core/identifiers.py`.

Nothing here drops or rebuilds a column that anything points at:

* `uuid` is added nullable, filled a row at a time, and only then made unique.
  Adding a unique column with a callable default in one step would write the
  *same* value to every existing row and then fail on the constraint.
* `file_number` is **renamed** to `patient_number`, not replaced. A rename
  carries the data and the unique index with it, so no patient loses the
  number already printed on their card and no second identifier field appears
  beside the first.
* The reformat only prefixes the register letter: `NMHS-000001` becomes
  `NMHS-P000001`, keeping the six figures a patient may be holding on paper.
  It is reversible, so this migration can be rolled back without data loss.
"""

import uuid as uuid_module

from django.db import migrations, models

from apps.core import identifiers


def issue_uuids(apps, schema_editor):
    """
    A fresh UUID per existing row. Every row is rewritten, not just the empty
    ones: `AddField` with a callable default evaluates it once and writes that
    single value to the whole table, so after the add they all hold the *same*
    UUID and none of them is null.
    """
    Patient = apps.get_model("patients", "Patient")
    for patient in Patient.objects.order_by("pk").only("pk"):
        Patient.objects.filter(pk=patient.pk).update(uuid=uuid_module.uuid4())


def adopt_patient_numbers(apps, schema_editor):
    Patient = apps.get_model("patients", "Patient")
    for patient in Patient.objects.order_by("pk").only("pk", "patient_number"):
        adopted = identifiers.adopt_number(
            patient.patient_number, identifiers.PATIENT, patient.pk
        )
        if adopted != patient.patient_number:
            Patient.objects.filter(pk=patient.pk).update(patient_number=adopted)


def restore_legacy_numbers(apps, schema_editor):
    Patient = apps.get_model("patients", "Patient")
    for patient in Patient.objects.order_by("pk").only("pk", "patient_number"):
        legacy = identifiers.strip_kind(patient.patient_number, identifiers.PATIENT)
        if legacy != patient.patient_number:
            Patient.objects.filter(pk=patient.pk).update(patient_number=legacy)


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0008_age_value_and_unit"),
    ]

    operations = [
        migrations.AddField(
            model_name="patient",
            name="uuid",
            field=models.UUIDField(default=uuid_module.uuid4, editable=False, null=True),
        ),
        migrations.RunPython(issue_uuids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="patient",
            name="uuid",
            field=models.UUIDField(default=uuid_module.uuid4, editable=False, unique=True),
        ),
        migrations.RenameField(
            model_name="patient",
            old_name="file_number",
            new_name="patient_number",
        ),
        migrations.RunPython(adopt_patient_numbers, restore_legacy_numbers),
    ]
