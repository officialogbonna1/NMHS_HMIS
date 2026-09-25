"""
**Where a member of staff is authorised to work.**

One definition, because the hospital already had two and they disagreed.

`Department.staff` is a `ManyToManyField` to `User` and has been since
`departments/0001` — so the *relation* for multi-department authorisation was
already here, and nothing about it needed inventing. What was missing was a
name for the question, so five call sites each asked it their own way:

    route.department.staff.filter(pk=user.pk).exists()
      or route.department.name.lower() == (user.department or "").lower()

and one of those five spelled the fallback `department__code__iexact` while
another spelled it `department__name__iexact`, so a user whose text said
"maternity" was authorised in one place and not in the other.

**Two columns, two meanings, both kept** (the safest reading of the existing
architecture — separating the concerns rather than breaking ~8 consumers):

- `User.department` is a free-text **primary/organisational** label. It is on
  `/auth/me/`, on the Users admin page and in the staff directory, and a
  hospital types things like "Front Desk" into it that are not departments at
  all. It is *not* the authorisation.
- `Department.staff` is the **authorised departments** relation — where this
  person may work, several at once, set by an administrator.

The legacy text is still honoured as a fallback, because an existing account
whose only record of its department is that string must not lose access on the
day this ships (and `accounts/0007` backfills the relation from it, so the
fallback matters only for text nobody has reconciled).

**This grants nothing on its own.** Authorisation in this application is
always role **and** department: `RoleRequired` decides what a person may do
and this decides where. A doctor authorised for Maternity is a doctor in
Maternity — not a midwife, not a pharmacist, and not authorised anywhere else.
"""
from django.db import models


def authorized_departments(user):
    """
    Every department this user may work in, as a queryset.

    The relation, plus the department the legacy text names where one matches.
    An admin is **not** special-cased here: this answers "where is this person
    posted", and an administrator's reach comes from their role, which every
    `RoleRequired` check already lets through.
    """
    from apps.departments.models import Department

    if not getattr(user, "is_authenticated", False):
        return Department.objects.none()
    return Department.objects.filter(_membership_q(user)).distinct()


def works_in(user, department):
    """
    Is this user authorised in `department`?

    `department` may be a `Department`, a code or a name — the three things
    call sites actually hold. Anything else is not authorised rather than an
    error, because an authorisation helper that raises is one somebody will
    wrap in a bare `except` (rule 47).
    """
    from apps.departments.models import Department

    if department is None or not getattr(user, "is_authenticated", False):
        return False
    if isinstance(department, Department):
        return Department.objects.filter(pk=department.pk).filter(
            _membership_q(user)).exists()
    named = str(department).strip()
    if not named:
        return False
    return Department.objects.filter(
        models.Q(code__iexact=named) | models.Q(name__iexact=named),
    ).filter(_membership_q(user)).exists()


def works_in_code(user, code):
    """`works_in` for a department named by its stable code (rule 34)."""
    return works_in(user, code)


def staff_of(department, *, roles=None):
    """
    Everyone authorised in `department`, optionally narrowed to some roles —
    the mirror of `authorized_departments`, read the other way round.

    This is what a "who may be named for this work" selector asks, so the
    people it offers are exactly the people the server will accept.
    """
    from apps.accounts.models import User

    if department is None:
        return User.objects.none()
    people = models.Q(department_memberships=department)
    for field in ("code", "name"):
        value = getattr(department, field, None)
        if value:
            people |= models.Q(department__iexact=value)
    query = User.objects.filter(people, is_active=True)
    if roles is not None:
        query = query.filter(role__in=list(roles))
    return query.distinct().order_by("last_name", "first_name", "username")


def _membership_q(user):
    """
    The relation, plus the legacy text. Read as a `Q` against `Department` so
    a caller can compose it, and so the two spellings of the fallback — the
    code and the name — can never drift apart again.
    """
    named = (getattr(user, "department", "") or "").strip()
    query = models.Q(staff=user)
    if named:
        query |= models.Q(code__iexact=named) | models.Q(name__iexact=named)
    return query
