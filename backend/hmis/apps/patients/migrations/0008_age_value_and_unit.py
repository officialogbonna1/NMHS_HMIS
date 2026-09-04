from django.db import migrations, models


class Migration(migrations.Migration):
    """
    `age_years` could only say "0" for a three-day-old, which on a maternity
    ward is most of the register. The number keeps its rows (renamed, so
    nothing is lost) and gains a unit beside it.
    """

    dependencies = [
        ("patients", "0007_patient_emergency_contact_address_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="patient",
            old_name="age_years",
            new_name="age_value",
        ),
        migrations.AddField(
            model_name="patient",
            name="age_unit",
            # Existing rows were years, and that is the default, so they are
            # already correct without a data migration.
            field=models.CharField(
                blank=True, default="years", max_length=10,
                choices=[("days", "Days"), ("weeks", "Weeks"), ("months", "Months"), ("years", "Years")],
            ),
        ),
    ]
