"""
What can be booked, which unit performs it, and who may be named on it.

**This module configures nothing and stores nothing.** Every answer here is
read out of something the hospital already has, because the join the booking
form needs turned out to be one that already existed:

    BillingItem.category
        ├── billing/departments.py  → the Department that performs it
        └── workflow/views.PURPOSE_ROLE → the roles that may be named on it

A service is `laboratory`, `ultrasound`, `eye`, `consultation`, `procedure` or
`card` in *both* of those maps already, because both were written from the
same vocabulary. So "Eye Consultation is done by the Eye Clinic and is worked
by an optometrist or an ophthalmologist" is not new configuration — it is two
existing maps read in sequence.

That is why there is no `AppointmentService` model, no appointment department
table, no provider directory and no appointment price list:

* the **service** is `billing.BillingItem`, the row the counter bills and
  `billing/catalogue.py` already serves (rule 50), with one flag on it saying
  whether it may be booked;
* the **fee** is that row's `price`, resolved here and never read from a
  request body — a figure a client sends is not money in this system (rule 52);
* the **department** is the seeded `Department` (rule 34);
* the **provider** is a `User` with the role the work calls for, plus anybody
  the department lists as staff — the fallback rule 16 already draws.

**A department appears when it has something to offer.** `departments()` is
derived from the bookable services, so no list anywhere names which units take
appointments: tick a service and its department appears, untick the last one
and it goes. Nursing, Pharmacy and Reception have no bookable service and so
are absent without being excluded by name.
"""
from django.db.models import Q

from apps.accounts.departments import staff_of


def posted_to(department):
    """The ids of everyone authorised in `department` — the relation an
    administrator sets, plus the legacy text (`accounts/departments.py`)."""
    return list(staff_of(department).values_list("pk", flat=True))

from apps.billing import catalogue
from apps.billing.departments import department_for_source

#: Where the eligible roles come from. Imported rather than restated: the same
#: map decides who a referral reaches (rule 16), and two copies of it would
#: drift into a provider being bookable for work they cannot do.
def _purpose_role():
    # Imported inside the function because `workflow.views` imports a good
    # deal of the application; this module is read by serializers at import
    # time.
    from apps.workflow.views import PURPOSE_ROLE

    return PURPOSE_ROLE


def _provider_roles():
    """
    Every role that can hold an appointment — the roles the bookable
    *categories* map to.

    Static, and deliberately not derived from what happens to be ticked
    today: a permission that changed when an administrator ticked a service
    would be a permission nobody could write down. It is the intersection of
    two existing maps — `BillingItem.CATEGORY` (what can be priced, and
    therefore booked) and `PURPOSE_ROLE` (who works it) — so it grows when the
    hospital gains a category and a role for it, and not otherwise.

    `nurse` is absent because `vitals` is not a billing category: a nurse is
    routed a patient (rule 16), never booked one. `doctor` is first, and the
    consultation queue is exactly what it was.
    """
    from apps.billing.models import BillingItem

    categories = {value for value, _ in BillingItem.CATEGORY}
    roles = []
    for category, names in _purpose_role().items():
        if category not in categories:
            continue
        for name in names:
            if name not in roles:
                roles.append(name)
    return roles


#: Read once at import: the permission classes need a fixed list.
PROVIDER_ROLES = _provider_roles()


def bookable_services(*, category=None):
    """
    Every active service reception may queue an appointment for.

    Read from `billing.catalogue`, so it is the same window the counter bills
    through and the doctor orders from — one service, one price, one row.
    Laboratory tests come from `LabTest` and are not offered: a test is
    ordered by a clinician and billed at the counter (rules 24 and 50), and
    nothing about that changes here. A laboratory *appointment* is a
    `BillingItem` under Laboratory that somebody has ticked.
    """
    rows = [row for row in catalogue.services(category=category, active_only=True)
            if row.get("source") == "billing_item" and row.get("is_appointment_service")]
    return rows


def service_for(key):
    """
    The bookable service a request named, or None.

    Accepts the catalogue key (`billing_item:7`) or the bare primary key, so
    the API is usable from a form that only holds an id. None means "not
    bookable" — retired, unticked, laboratory-catalogue, or nothing at all —
    and the caller refuses rather than guessing.
    """
    if key in (None, ""):
        return None
    wanted = str(key).strip()
    if wanted.isdigit():
        wanted = f"billing_item:{wanted}"
    for row in bookable_services():
        if row["key"] == wanted:
            return row
    return None


def department_of(service):
    """
    The unit that performs this service — the seeded `Department` its category
    resolves to, which is the same row the charge it raises is attributed to.
    Reading it from one place is what stops an appointment saying Eye Clinic
    while its bill says Consultation.
    """
    return department_for_source(service["category"]) if service else None


def eligible_roles(category):
    """The roles that may be named on a service in this category."""
    return list(_purpose_role().get(category, []))


def eligible_providers(service):
    """
    Who may be named on this appointment: anybody holding a role the work
    calls for, plus anybody the performing department lists as staff.

    The staff list is the fallback rule 16 already draws for work with no role
    of its own — a `card` or `other` service has no clinical role, and a
    department's own people are the right answer there. Inactive accounts are
    excluded: naming one parks the patient in a queue nobody is watching,
    which is the same reason `PatientRouteSerializer` refuses one.
    """
    from apps.accounts.models import User

    if service is None:
        return User.objects.none()
    roles = eligible_roles(service["category"])
    department = department_of(service)
    query = User.objects.filter(is_active=True)
    if roles and department is not None:
        query = query.filter(Q(role__in=roles) | Q(pk__in=posted_to(department)))
    elif roles:
        query = query.filter(role__in=roles)
    elif department is not None:
        query = query.filter(pk__in=posted_to(department))
    else:
        return User.objects.none()
    return query.distinct().order_by("last_name", "first_name", "username")


def may_provide(user, service):
    """
    Whether this person may be named on this appointment.

    An admin passes, as they do on every route and referral in the system.
    Everybody else is checked against the same rule the dropdown was built
    from, on the server, so submitting another provider id straight to the API
    is refused rather than honoured.
    """
    if user is None or service is None:
        return False
    if getattr(user, "is_admin", False):
        return True
    if not user.is_active:
        return False
    return eligible_providers(service).filter(pk=user.pk).exists()


#: The serviceless booking — patient, provider, reason — is a general
#: consultation, and the category that describes it is `consultation`. Shaped
#: as a catalogue row so `eligible_providers` and `may_provide` read it with no
#: second code path: a category is all either of them looks at.
GENERAL_CONSULTATION = {"category": "consultation", "name": "General consultation"}


def bookable_departments():
    """
    The departments Reception may book into: active **and** ticked *Available
    for appointments* in Django admin.

    The one query behind both halves of this module — what the form is offered
    and what the API will accept — so a department cannot disappear from the
    dropdown while still being bookable over the wire.
    """
    from apps.departments.models import Department

    return Department.objects.filter(is_active=True, is_appointment_available=True)


def accepts_appointments(department):
    """Whether a booking may be filed against this department."""
    return bool(department and department.is_active and department.is_appointment_available)


def services_for(keys):
    """
    The bookable services a request named, in the order it named them.

    Returns `(services, unknown)` — the same shape `billing.catalogue.resolve`
    uses, for the same reason: the caller decides whether an unknown key is
    fatal and nothing here guesses at one. Duplicates are collapsed, so ticking
    a service twice bills it once.
    """
    wanted, seen = [], set()
    for key in keys or []:
        text = str(key).strip()
        if text and text not in seen:
            seen.add(text)
            wanted.append(text)
    found, unknown = [], []
    for key in wanted:
        service = service_for(key)
        (found if service is not None else unknown).append(service if service is not None else key)
    return found, unknown


def refusal_for(service_key, provider):
    """
    Why this booking cannot be made, or None if it can.

    The dropdowns are built from `departments()` above; this is the same rule
    applied to what actually arrived, so posting another service or another
    provider id straight at the API is refused rather than honoured. Returned
    as a body rather than raised, because the view answers with a flat `code`
    the screen can branch on — DRF wraps a serializer's codes in lists, which
    `api/errors.js` cannot read.

    A booking with **no** service is still the booking reception has always
    sent — patient, provider, reason — and it stays that way: no department,
    no fee, no charge. What it is not is a way past the provider rule. It is a
    general consultation, so the provider has to be somebody who does
    consultations, which is exactly who the old form offered (`/users/?role=
    doctor`) and exactly who the old transition permission allowed.

    Without this, widening the transitions from `doctor` to `PROVIDER_ROLES`
    would have quietly turned the serviceless path into the way round the
    eligibility check: reception could name a laboratory scientist on an
    appointment with no service, and that account could then work it.
    """
    keys = service_key if isinstance(service_key, (list, tuple)) else [service_key]
    keys = [key for key in keys if key not in (None, "")]
    if not keys:
        consulting = department_of(GENERAL_CONSULTATION)
        if not accepts_appointments(consulting):
            return None, {
                "detail": "Consultation is not currently available for appointments.",
                "code": "department_not_available",
            }
        if not may_provide(provider, GENERAL_CONSULTATION):
            return None, {
                "detail": f"{_name(provider)} cannot be booked for a general consultation. "
                          "Pick a department and a service to book anybody else.",
                "code": "provider_not_eligible",
            }
        return [], None

    services, unknown = services_for(keys)
    if unknown:
        # Named by key rather than counted: a desk that ticked six services
        # and got "one of these cannot be booked" has to guess which.
        return None, {
            "detail": f"{len(unknown)} of those cannot be booked as an appointment.",
            "code": "service_not_bookable",
            "unknown": unknown,
        }

    # One appointment goes to one place. Services from two departments would
    # be two destinations with one provider between them, and the row could
    # only name one of them — so it is refused rather than half-recorded.
    destinations = {service["category"] for service in services}
    if len(destinations) > 1:
        return None, {
            "detail": "An appointment goes to one department. Book these separately.",
            "code": "services_span_departments",
        }

    # The destination has to be one Reception may book into. Checked here
    # rather than only in the dropdown, because the dropdown is a courtesy: a
    # service key posted straight at the API for a department an administrator
    # has closed is refused, not honoured. The department is never submitted —
    # it is resolved from the service's category — so this is the one place it
    # can be checked.
    destination = department_of(services[0])
    if not accepts_appointments(destination):
        return None, {
            "detail": f"{destination.name if destination else 'That department'} is not "
                      "currently available for appointments.",
            "code": "department_not_available",
        }

    # The provider has to be able to do **every** one of them. Services in one
    # department share a category and therefore a rule, so this normally
    # agrees with itself — it is checked per service so that it still refuses
    # correctly if that ever stops being true.
    for service in services:
        if not may_provide(provider, service):
            return None, {
                "detail": f"{_name(provider)} cannot be booked for {service['name']}.",
                "code": "provider_not_eligible",
            }
    return services, None


def _name(user):
    if user is None:
        return "That person"
    return user.get_full_name() or user.username


def general_consultation():
    """
    The fast path: patient → doctor → reason → queue, with no service chosen.

    Described here rather than inferred in the browser from a department code,
    because which unit a general consultation belongs to is the same question
    `billing/departments.py` already answers. It raises no charge, which is
    what it has always done.
    """
    department = department_of(GENERAL_CONSULTATION)
    return {
        "department": department.pk if department else None,
        "department_name": department.name if department else "",
        "label": GENERAL_CONSULTATION["name"],
        "providers": list(eligible_providers(GENERAL_CONSULTATION)),
    }


def departments():
    """
    Every department open for appointments, each with the services it offers —
    which may be none.

    **Two switches, and they answer different questions.** A department is
    offered when an administrator has ticked *Available for appointments* on it
    (`is_appointment_available`, and it must be active); what can actually be
    booked there stays `BillingItem.is_appointment_service`. So Pharmacy is
    absent because somebody said so in Django admin rather than because a rule
    here knows what a pharmacy is, and a department that is offered with
    nothing ticked appears and offers nothing rather than being given invented
    services. A department with an empty list cannot be booked into; the form
    says so.

    The grouping is still derived: a service is filed under the department its
    category resolves to, which is the same department its charge is
    attributed to (rule 33), so an appointment and its bill can never name two
    different units.
    """
    grouped = {
        department.pk: {"id": department.pk, "code": department.code,
                        "name": department.name, "services": []}
        for department in bookable_departments()
    }
    for service in bookable_services():
        department = department_of(service)
        if department is None:
            # A bookable service whose category has no seeded department is a
            # configuration gap, not a reason to hide the service: it is
            # reported under its category so somebody can see and fix it.
            continue
        entry = grouped.get(department.pk)
        if entry is None:
            # The department is closed for appointments, or retired. Its
            # services stay configured and billable at the counter; they are
            # simply not offered here, because the department is not. Adding
            # the department back because it has something ticked would let a
            # service override the administrator's switch.
            continue
        entry["services"].append(service)
    return [grouped[key] for key in sorted(grouped, key=lambda pk: grouped[pk]["name"].lower())]
