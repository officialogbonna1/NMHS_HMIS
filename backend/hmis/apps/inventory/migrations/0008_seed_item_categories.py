"""
The default product categories a hospital pharmacy starts with.

`ItemCategory` already existed (migration 0006, which turned the free-text
column into rows) — what did not exist was a seed, so a fresh install opened
Administration → Product categories on an empty list and every hospital
retyped the same twenty-odd groups, which is exactly the drift that made
categories a table in the first place.

Three rules, all of them about not touching what is already there:

* **Nothing is renamed and nothing is reactivated.** A hospital that already
  has "Analgesics" keeps it, under that name, active or retired as they left
  it. The seed *adopts* it — `ALIASES` is how "Analgesics" and "Pain Killers"
  are recognised as the row "Pain Relief / Analgesics" would have been — so a
  second group for the same medicines is never created beside the first.
  Matching ignores case and punctuation, because "Pain Relief", "pain relief"
  and "Pain-Relief" are one category with three spellings.
* **No product is classified.** Rule 16 of the brief and plain sense: a
  product with no category keeps none until somebody who knows what it is
  says so. A seeded list is a vocabulary, not a guess at what the shelf holds.
* **It is re-runnable.** Run it against a hospital that has been using the
  system for a year and it adds only the names nobody has.

Reversing removes only the rows this seed created *and* that nothing points
at — a category somebody has since filed a product under stays, because
`Item.category` is PROTECT and history is not unwound by a downgrade.
"""
import re

from django.db import migrations

#: name, description, display order. The order is the grouping a pharmacist
#: reads down: the medicines they reach for most, then the rest of the
#: therapeutic classes, then supplies.
CATEGORIES = [
    ("Pain Relief / Analgesics", "Painkillers and anti-inflammatories.", 10),
    ("Antibiotics", "Antibacterial medicines.", 20),
    ("Antimalarials", "Malaria treatment and prophylaxis.", 30),
    ("Antihypertensives", "Blood-pressure medicines.", 40),
    ("Antidiabetics", "Insulin and oral hypoglycaemics.", 50),
    ("Antifungals", "Antifungal medicines.", 60),
    ("Antivirals", "Antiviral medicines.", 70),
    ("Gastrointestinal", "Antacids, antiemetics, antidiarrhoeals, laxatives.", 80),
    ("Respiratory", "Inhalers, bronchodilators, cough preparations.", 90),
    ("Cardiovascular", "Cardiac medicines other than antihypertensives.", 100),
    ("Vitamins & Supplements", "Vitamins, minerals, haematinics.", 110),
    ("Antiseptics / Disinfectants", "Skin and surface antiseptics.", 120),
    ("Dermatological", "Creams, ointments and other skin preparations.", 130),
    ("Eye Care", "Ophthalmic drops, ointments and washes.", 140),
    ("Ear, Nose & Throat", "ENT drops, sprays and lozenges.", 150),
    ("Emergency Medicines", "Resuscitation and emergency-trolley drugs.", 160),
    ("Wound Care / Dressings", "Gauze, bandages, plasters, dressing packs.", 170),
    ("Syringes & Needles", "Syringes, needles, cannulae and sharps.", 180),
    ("Surgical Supplies", "Theatre consumables and instruments.", 190),
    ("Medical Consumables", "Gloves, swabs, catheters and general consumables.", 200),
    ("Laboratory Consumables", "Reagents, sample bottles and bench consumables.", 210),
    ("Personal Care", "Toiletries and personal-care items.", 220),
    ("Other", "Anything the list above does not cover.", 900),
]

#: Spellings an existing row may already be using for one of the seeded
#: groups. The seeded name is always matched too — this is only the extra
#: vocabulary, so a hospital that has been typing "Analgesics" since day one
#: does not end up with that row *and* "Pain Relief / Analgesics".
ALIASES = {
    "Pain Relief / Analgesics": ["Analgesics", "Analgesic", "Pain Relief", "Painkillers",
                                 "Pain Killers", "Pain relief / analgesics", "NSAIDs"],
    "Antibiotics": ["Antibiotic", "Antibacterials", "Anti-biotics"],
    "Antimalarials": ["Antimalarial", "Anti-malarials", "Malaria"],
    "Antihypertensives": ["Antihypertensive", "Anti-hypertensives", "Hypertension"],
    "Antidiabetics": ["Antidiabetic", "Anti-diabetics", "Diabetes"],
    "Antifungals": ["Antifungal", "Anti-fungals"],
    "Antivirals": ["Antiviral", "Anti-virals"],
    "Gastrointestinal": ["GIT", "Gastro-intestinal", "Gastro Intestinal"],
    "Respiratory": ["Respiratory Medicines", "Anti-asthmatics"],
    "Cardiovascular": ["Cardiac", "Cardio-vascular"],
    "Vitamins & Supplements": ["Vitamins", "Supplements", "Vitamins and Supplements",
                              "Haematinics"],
    "Antiseptics / Disinfectants": ["Antiseptics", "Disinfectants",
                                    "Antiseptics and Disinfectants"],
    "Dermatological": ["Dermatology", "Skin", "Topical"],
    "Eye Care": ["Ophthalmic", "Eye", "Eye Drops"],
    "Ear, Nose & Throat": ["ENT", "Ear Nose and Throat", "Ear Nose & Throat"],
    "Emergency Medicines": ["Emergency", "Emergency Drugs"],
    "Wound Care / Dressings": ["Dressings", "Wound Care", "Wound Dressings"],
    "Syringes & Needles": ["Syringes", "Needles", "Syringes and Needles"],
    "Surgical Supplies": ["Surgical", "Theatre Supplies"],
    "Medical Consumables": ["Consumables", "Medical Consumable"],
    "Laboratory Consumables": ["Lab Consumables", "Laboratory Supplies", "Reagents"],
    "Personal Care": ["Toiletries"],
    "Other": ["Others", "Miscellaneous", "Uncategorised", "Uncategorized"],
}


def normalise(name):
    """
    "Pain Relief", "pain relief" and "Pain-Relief" are one name.

    Case and punctuation are dropped and runs of whitespace collapsed, so the
    comparison is on the words alone. Deliberately *not* fuzzy: "Pain Relif"
    is a different string and stays a different string — guessing at a
    misspelling is how the wrong medicines end up grouped together.
    """
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def seed(apps, schema_editor):
    ItemCategory = apps.get_model("inventory", "ItemCategory")
    # Every row already on file, by its normalised name — including retired
    # ones, so a category an administrator deliberately deactivated is not
    # quietly recreated as a second active row.
    existing = {normalise(name): pk for pk, name
                in ItemCategory.objects.values_list("pk", "name")}

    for name, description, order in CATEGORIES:
        spellings = [name] + ALIASES.get(name, [])
        if any(normalise(spelling) in existing for spelling in spellings):
            continue  # the hospital already has this group, under its own name
        created = ItemCategory.objects.create(
            name=name, description=description, display_order=order, is_active=True)
        existing[normalise(name)] = created.pk


def unseed(apps, schema_editor):
    """Take back only what this seed added and nothing has used since."""
    ItemCategory = apps.get_model("inventory", "ItemCategory")
    Item = apps.get_model("inventory", "Item")
    in_use = set(Item.objects.exclude(category=None)
                 .values_list("category_id", flat=True).distinct())
    for name, description, _order in CATEGORIES:
        row = ItemCategory.objects.filter(name=name, description=description).first()
        if row is not None and row.pk not in in_use:
            row.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0007_pos_identifiers_strength_and_count_imports"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
