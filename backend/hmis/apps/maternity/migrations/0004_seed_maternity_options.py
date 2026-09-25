"""
The maternity vocabulary a hospital starts with.

Delivery types, outcomes, complications, newborn states, maternal conditions
and family-planning methods — seeded rather than written into the models,
because which words a hospital uses is the hospital's decision (rule 31) and
an administrator can add, rename, reorder or retire any of them afterwards
without a deployment.

Same seeding rules as every other list here (rules 34 and 41): **adopt by
code, never overwrite**. A row somebody has edited is left as they left it, a
retired one is not brought back, and reversing is a no-op — after this runs
the vocabulary belongs to the administrator.
"""
from django.db import migrations

# kind, code, name, order
OPTIONS = [
    ("delivery_type", "svd", "Normal vaginal delivery", 10),
    ("delivery_type", "assisted-vaginal", "Assisted vaginal delivery", 20),
    ("delivery_type", "instrumental", "Instrumental (forceps / vacuum)", 30),
    ("delivery_type", "breech", "Breech delivery", 40),
    ("delivery_type", "caesarean-elective", "Caesarean section — elective", 50),
    ("delivery_type", "caesarean-emergency", "Caesarean section — emergency", 60),

    ("delivery_outcome", "live-birth", "Live birth", 10),
    ("delivery_outcome", "stillbirth-fresh", "Fresh stillbirth", 20),
    ("delivery_outcome", "stillbirth-macerated", "Macerated stillbirth", 30),
    ("delivery_outcome", "early-neonatal-death", "Early neonatal death", 40),

    ("complication", "pph", "Postpartum haemorrhage", 10),
    ("complication", "prolonged-labour", "Prolonged labour", 20),
    ("complication", "obstructed-labour", "Obstructed labour", 30),
    ("complication", "pre-eclampsia", "Pre-eclampsia", 40),
    ("complication", "eclampsia", "Eclampsia", 50),
    ("complication", "perineal-tear", "Perineal tear", 60),
    ("complication", "retained-placenta", "Retained placenta", 70),
    ("complication", "cord-prolapse", "Cord prolapse", 80),
    ("complication", "fetal-distress", "Fetal distress", 90),

    ("newborn_status", "alive-well", "Alive and well", 10),
    ("newborn_status", "alive-needs-care", "Alive — needs special care", 20),
    ("newborn_status", "resuscitated", "Resuscitated", 30),
    ("newborn_status", "stillborn", "Stillborn", 40),
    ("newborn_status", "neonatal-death", "Neonatal death", 50),

    ("mother_condition", "stable", "Stable", 10),
    ("mother_condition", "needs-observation", "Needs close observation", 20),
    ("mother_condition", "critical", "Critical", 30),

    ("family_planning", "none", "None / undecided", 10),
    ("family_planning", "counselled", "Counselled, deciding", 20),
    ("family_planning", "implant", "Implant", 30),
    ("family_planning", "iud", "IUD", 40),
    ("family_planning", "injectable", "Injectable", 50),
    ("family_planning", "pills", "Oral contraceptive pills", 60),
    ("family_planning", "condoms", "Condoms", 70),
    ("family_planning", "bilateral-tubal-ligation", "Bilateral tubal ligation", 80),
]


def seed(apps, schema_editor):
    MaternityOption = apps.get_model("maternity", "MaternityOption")
    for kind, code, name, order in OPTIONS:
        MaternityOption.objects.get_or_create(
            kind=kind, code=code,
            defaults={"name": name, "display_order": order},
        )


def leave_the_vocabulary_alone(apps, schema_editor):
    """Reversing the schema must not delete words the hospital has used."""


class Migration(migrations.Migration):

    dependencies = [("maternity", "0003_maternityoption_newborn_postpartumvisit_and_more")]

    operations = [migrations.RunPython(seed, leave_the_vocabulary_alone)]
