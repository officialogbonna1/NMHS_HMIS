"""
Shared behaviour for configuration endpoints.

Configuration is the hospital's own setup — departments, services, products,
categories, units, locations, wards, beds, the price list, the laboratory
catalogue. Two interfaces edit it, the HMIS administration screens and Django
admin, and both go through the same models. What this module holds is the
rule they must both obey:

**Delete what was never used; deactivate what history points at.**

A category nobody ever filed a product under is a typo, and deleting it is
the right answer. A category that fifty products and three years of stock
movements refer to is part of the record, and deleting it either fails at the
database (PROTECT) or takes the history with it (CASCADE). Neither is
acceptable, so the answer there is `is_active = False`: the row stays, every
old record still reads correctly, and it stops being offered for new work.

`ProtectedConfigMixin` puts that in front of the API, and
`ProtectedConfigAdmin` puts the same rule in front of Django admin, so the
two interfaces cannot disagree about what is safe to remove.
"""
from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.response import Response


def references_to(instance, relations):
    """
    Count what points at this row, per relation.

    `relations` is a list of accessor names — `("items", "batches")`. Returns
    `{"items": 12}` for the ones that are non-empty, which is what both the
    API's refusal message and the admin's warning are built from.
    """
    counts = {}
    for name in relations:
        manager = getattr(instance, name, None)
        if manager is None:
            continue
        count = manager.count() if hasattr(manager, "count") else 0
        if count:
            counts[name] = count
    return counts


def describe(counts):
    """"12 items, 3 batches" — for a message a person has to act on."""
    return ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in counts.items())


class ProtectedConfigMixin:
    """
    A configuration viewset that refuses a destructive delete.

    Set `protected_relations` to the accessors that would make a row part of
    the record. DELETE succeeds when none of them has anything — a mistake
    typed a minute ago is simply removed — and answers 409 with the counts
    when they do, naming deactivation as the way to retire it instead.

    `ProtectedError` is caught as well: a relation nobody listed still must
    not produce a 500.
    """
    protected_relations = ()
    # The field that retires a row instead of deleting it. Named here so the
    # refusal can tell the caller what to do rather than only what it cannot.
    deactivate_field = "is_active"

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        counts = references_to(instance, self.protected_relations)
        if counts:
            return Response(self._refusal(instance, counts), status=status.HTTP_409_CONFLICT)
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError as exc:
            protected = len(getattr(exc, "protected_objects", []) or [])
            return Response(
                {"detail": f"{instance} is referenced by {protected} other record(s) and "
                           f"cannot be deleted. Deactivate it instead.",
                 "code": "in_use"},
                status=status.HTTP_409_CONFLICT)

    def _refusal(self, instance, counts):
        can_deactivate = hasattr(instance, self.deactivate_field)
        detail = (f"{instance} is used by {describe(counts)}, so deleting it would break "
                  f"records that already exist.")
        if can_deactivate:
            detail += " Deactivate it instead — it stays on the old records and stops being offered."
        return {"detail": detail, "code": "in_use", "references": counts}


class ProtectedConfigAdmin:
    """
    The same rule for Django admin.

    Mixed into a ModelAdmin, it hides the delete action for a row history
    points at — so a technical administrator gets the same answer as the HMIS
    screen rather than a database error or, worse, a successful cascade.
    """
    protected_relations = ()

    def has_delete_permission(self, request, obj=None):
        if obj is not None and references_to(obj, self.protected_relations):
            return False
        return super().has_delete_permission(request, obj)
