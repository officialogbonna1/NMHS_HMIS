"""
The clinics a maternity hospital starts with.

Seeded rather than hard-coded as model choices, because which clinics a
hospital runs is the hospital's decision — rule 31's test for configuration —
and an administrator can add, rename, reorder or retire any of these
afterwards without a deployment.

It follows the seeding rules the department and category seeds already set
(rules 34 and 41): **adopt by code, never overwrite**. A row whose `name`,
order or active state somebody has edited is left exactly as they left it, and
a retired one is not brought back to life on the next deploy. Reversing is a
no-op: after this runs the list belongs to the administrator.
"""
from django.db import migrations

# code, name, description, is_booking, order
VISIT_TYPES = [
    ("anc-booking", "ANC — first visit (booking)",
     "Opens the pregnancy record and takes the obstetric history.", True, 10),
    ("anc-followup", "ANC — follow-up",
     "A routine return visit in an established pregnancy.", False, 20),
    ("maternity-consultation", "Maternity consultation",
     "A non-routine review inside an active pregnancy.", False, 30),
    ("labour-assessment", "Labour assessment",
     "She has presented in labour, or thinks she may be.", False, 40),
    ("maternity-emergency", "Emergency maternity assessment",
     "An urgent obstetric complaint — bleeding, reduced fetal movements, severe headache.",
     False, 50),
    ("postnatal", "Postnatal / postpartum",
     "Care of mother and baby after delivery.", False, 60),
    ("maternity-followup", "Maternity follow-up",
     "A review arranged after any of the above.", False, 70),
]


def seed(apps, schema_editor):
    MaternityVisitType = apps.get_model("maternity", "MaternityVisitType")
    for code, name, description, is_booking, order in VISIT_TYPES:
        MaternityVisitType.objects.get_or_create(
            code=code,
            defaults={"name": name, "description": description,
                      "is_booking": is_booking, "display_order": order},
        )


def leave_the_configuration_alone(apps, schema_editor):
    """Reversing the schema must not delete clinics the hospital has used."""


class Migration(migrations.Migration):

    dependencies = [("maternity", "0001_initial")]

    operations = [migrations.RunPython(seed, leave_the_configuration_alone)]
