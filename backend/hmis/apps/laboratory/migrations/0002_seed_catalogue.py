"""
The starting catalogue: ~66 tests across haematology, chemistry,
microbiology, parasitology, urinalysis, serology and endocrinology, with
their parameters, plus the panels that reference them.

Seeded rather than hard-coded so the hospital owns it from day one — every
row here is editable, retirable and extendable from the Laboratory Catalogue
page. `seed_catalogue` never overwrites an existing row, so this is also
what the `seed_lab_catalogue` command runs when a later release adds tests.
"""
from django.db import migrations

from apps.laboratory.catalog import seed_catalogue


def load(apps, schema_editor):
    seed_catalogue(
        LabTest=apps.get_model("laboratory", "LabTest"),
        LabParameter=apps.get_model("laboratory", "LabParameter"),
        LabPanel=apps.get_model("laboratory", "LabPanel"),
    )


def unload(apps, schema_editor):
    """
    Only the seeded rows, and only where nothing has been ordered against
    them — a test somebody has used is the hospital's record, not ours.
    """
    from apps.laboratory.catalog import CATALOGUE, PANELS

    LabTest = apps.get_model("laboratory", "LabTest")
    LabPanel = apps.get_model("laboratory", "LabPanel")
    LabPanel.objects.filter(code__in=[p["code"] for p in PANELS]).delete()
    LabTest.objects.filter(
        code__in=[t["code"] for t in CATALOGUE], order_items__isnull=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("laboratory", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(load, unload),
    ]
