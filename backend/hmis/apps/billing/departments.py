"""
Which department a charge's money belongs to.

The hospital's revenue-generating units, and the `Charge.source_type` values
that belong to each. This module is the single definition; three things read
it and must never each keep their own copy:

- `billing.services.add_charge` resolves a department from it, so every new
  charge is attributed at the one chokepoint every charge passes through.
- `billing.reporting` groups by it.
- `departments/migrations/0002_seed_revenue_departments.py` seeds a
  `Department` row per entry. A migration has to be self-contained, so it
  repeats the codes and names as literals rather than importing this file —
  and `apps/departments/tests/test_department_registry.py` fails if the two
  ever drift apart.

**`code` is the machine identity, `name` is a label.** An administrator may
rename "Radiology / Ultrasound" to "Imaging" and every attribution keeps
working; the seed never overwrites a name that has been edited.

**Only revenue units belong here.** A department that never raises a charge —
Nursing, seeded as `clinicals` by `departments/migrations/0003` so a vitals
route has somewhere to be filed — must not be added to `REVENUE_DEPARTMENTS`.
It would put a permanently empty column into every financial report, and
`apps/departments/tests/test_department_registry.py` holds this list at seven
for that reason. A department is a unit of the hospital; this registry is the
subset of them that takes money.

**What is deliberately absent.** `investigation` is not mapped. The
diagnostics app already passes `catalog.department` explicitly, and that
catalogue row knows which unit performs the study better than a single
hard-coded guess could — mapping the source type here would override real
data with a default. `other` and `""` are not mapped either: a write-in
charge has no department the system can know, and inventing one would be
exactly the guesswork this attribution exists to replace.
"""
from django.core.exceptions import ObjectDoesNotExist

# code, display name, the Charge.source_type values that resolve to it.
#
# The source types are the ones the system actually writes: the billing
# counter posts a `BillingItem.category` (card, consultation, laboratory,
# ultrasound, eye, procedure), the laboratory stamps `lab_test`, the pharmacy
# stamps `prescription`, and the appointment booking stamps `appointment`.
# The extra aliases cost nothing and mean a future caller spelling it the
# other way still lands in the right place.
REVENUE_DEPARTMENTS = (
    ("reception", "Reception", ("card", "registration", "reception")),
    ("consultation", "Consultation", ("consultation", "appointment")),
    ("laboratory", "Laboratory", ("laboratory", "lab_test")),
    # `pos_sale` is a registered patient's purchase at the pharmacy POS till.
    ("pharmacy", "Pharmacy", ("prescription", "pharmacy", "medication", "pos_sale")),
    ("radiology", "Radiology / Ultrasound", ("ultrasound", "radiology", "imaging")),
    ("eye", "Eye Clinic", ("eye", "optometry", "ophthalmology")),
    # A procedure is theatre work in this hospital's workflow — `procedure` is
    # what both the billing catalogue and the referral purposes call it.
    ("theatre", "Theatre / Procedures", ("procedure", "theatre", "surgery")),
    # **Maternity joined the registry when it got a price list.**
    #
    # It was deliberately left out while the ward raised no charges of its own
    # (rule 56: "a nurse raises no charge, so an entry here would be a
    # permanently empty column in every financial report"). That reasoning was
    # conditional on the ward having nothing to bill, and it no longer holds:
    # the hospital has configured a booking visit, a delivery package, a
    # postnatal check. The registry is "the subset of departments that takes
    # money", so a department that now takes money belongs in it.
    #
    # Without this entry a ₦45,000 delivery package would post
    # `source_type="maternity"`, match no department, and be reported as
    # "Other / Unclassified" — the hospital's largest maternity charge landing
    # in the bucket that exists for charges nobody can place.
    #
    # The department row itself is seeded by `departments/0006`, not by
    # `departments/0002`, which is why the literal-drift test reads both.
    ("maternity", "Maternity", ("maternity", "obstetrics", "antenatal")),
)

# The seeded codes, in the order a report presents them.
DEPARTMENT_CODES = [code for code, _, _ in REVENUE_DEPARTMENTS]

# source_type -> (code, name). The authoritative map.
SOURCE_DEPARTMENT = {
    source: (code, name)
    for code, name, sources in REVENUE_DEPARTMENTS
    for source in sources
}

# source_type -> code, for the resolution below.
SOURCE_DEPARTMENT_CODE = {source: code for source, (code, _) in SOURCE_DEPARTMENT.items()}


def normalise_source(source_type):
    return (source_type or "").strip().lower()


def is_authoritative(source_type):
    """
    Does the backend already know which department this charge belongs to?

    True for a source type this hospital's workflow pins to one unit —
    `prescription` is pharmacy work wherever the request came from. False for
    a write-in, a blank, or `investigation`, where the caller's own department
    is the better answer.
    """
    return normalise_source(source_type) in SOURCE_DEPARTMENT_CODE


def department_code_for(source_type):
    """The department code this source type belongs to, or None."""
    return SOURCE_DEPARTMENT_CODE.get(normalise_source(source_type))


def department_for_source(source_type):
    """
    The `Department` row this source type belongs to, or None.

    None means one of two different things, and both are correct: the source
    type is not one the backend can attribute, or it is but the row has not
    been seeded — a deployment whose migrations have run always has it. Either
    way the caller falls back rather than raising: an unattributed charge is a
    reporting gap, and refusing to bill a patient over one would be worse.
    """
    code = department_code_for(source_type)
    if not code:
        return None
    # Imported here rather than at module scope: this module is read by
    # `reporting`, which must stay importable without touching the database.
    from apps.departments.models import Department
    try:
        return Department.objects.get(code=code)
    except (Department.DoesNotExist, ObjectDoesNotExist):
        return None
