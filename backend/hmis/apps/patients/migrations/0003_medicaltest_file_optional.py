from django.db import migrations, models


class Migration(migrations.Migration):
    """
    A test result is not always a document. A lab value read over the phone
    is typed into `impressions` and is still the record of that test, so the
    file stops being mandatory; MedicalTestSerializer.validate is what keeps
    a result with neither from being saved.
    """

    dependencies = [
        ("patients", "0002_patient_file_number"),
    ]

    operations = [
        migrations.AlterField(
            model_name="medicaltest",
            name="file",
            field=models.FileField(blank=True, null=True, upload_to="medical_tests/%Y/%m/"),
        ),
    ]
