"""
The discharge record gains a reference and a discharge condition.

Two additive columns and an ordering. Nothing existing is rewritten: a
discharge filed before this keeps a null `reference` and a blank `condition`,
which reads correctly as "this record predates them" — the same precedent rule
33 sets for `Adjustment.charge` and rule 45 for the amendment reason.

`reference` is nullable rather than blank-defaulted because it is `unique`:
several rows cannot share an empty string, and back-filling one would mean
inventing a number for a discharge that never had one. New rows are numbered
by `DischargeSummary.save()` off the primary key.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inpatient', '0003_alter_admission_options_alter_bed_options_and_more'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='dischargesummary',
            options={'ordering': ['-created_at']},
        ),
        migrations.AddField(
            model_name='dischargesummary',
            name='condition',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='dischargesummary',
            name='reference',
            field=models.CharField(blank=True, editable=False, max_length=24, null=True, unique=True),
        ),
    ]
