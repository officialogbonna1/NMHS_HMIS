"""
Who may actually work a route — one definition, read by the API and by the
screen, so the two cannot disagree.

The queue a station sees is now wider than the work it may do: a unit shares
its board, so a request a colleague has claimed stays on the list with their
name against it (`work_routes_for(..., include_unit=True)`). That makes "is
this mine?" a question the row has to answer, and answering it in two places —
once in `PatientRouteViewSet` and once in JavaScript — is how a button appears
that only 403s at the person who presses it. This module is the one answer,
the way `clinical.serializers.may_amend` is the one answer to "may I amend
this note?" (rule 3).
"""
from apps.accounts.departments import works_in


def may_work(user, route, *, role_purposes, purpose_role):
    """
    Whether this person is the one the work is waiting on.

    Admin always; the named assignee always; nobody else once somebody is
    named. Unclaimed work belongs to the role the purpose calls for, and falls
    back to department membership only for a purpose with no role of its own
    (rule 16).
    """
    if getattr(user, "is_admin", False) or route.assigned_to_id == user.id:
        return True
    if route.assigned_to_id:
        return False
    if route.purpose in role_purposes.get(getattr(user, "role", ""), []):
        return True
    if route.purpose in purpose_role:
        return False
    # Where this person is authorised to work — `accounts.departments.works_in`,
    # the one definition, which reads the `Department.staff` relation *and* the
    # legacy text. This used to compare the name only, so an account whose text
    # held the department's code was authorised on the queue and refused here.
    return works_in(user, route.department)


def claimed_by_somebody_else(user, route):
    """Whether this row is another person's work — what a queue shows as
    "With Bisi Ade" rather than an Accept button."""
    return bool(route.assigned_to_id) and route.assigned_to_id != user.id
