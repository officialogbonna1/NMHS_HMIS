from django.db import migrations, models


def backfill_file_numbers(apps, schema_editor):
    Patient = apps.get_model("patients", "Patient")
    for patient in Patient.objects.filter(file_number="").order_by("pk"):
        patient.file_number = f"NMHS-{patient.pk:06d}"
        patient.save(update_fields=["file_number"])


class Migration(migrations.Migration):

    dependencies = [
        ('patients', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='patient',
            name='file_number',
            field=models.CharField(blank=True, default='', editable=False, max_length=20),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_file_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='patient',
            name='file_number',
            field=models.CharField(blank=True, editable=False, max_length=20, unique=True),
        ),
    ]
