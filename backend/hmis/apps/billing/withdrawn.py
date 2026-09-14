"""
Bills still standing for a service its own department has withdrawn.

The Service Cancellations desk leads with these, because they are the charges
that genuinely need somebody to act. The laboratory can take a test off an
order — ordered by mistake, the sample never came — and when nothing has been
paid it cancels the charge there and then (`laboratory.views.remove_test`).
When money *has* been taken it cannot: the laboratory never moves money, so the
bill stands and waits for the cash desk to cancel it and hand the money back.

"Withdrawn" is read off the service, never guessed from the bill: a `lab_test`
charge whose `LabOrderTest` is cancelled, whose whole order is cancelled, or
which is no longer there at all (a line with no results is deleted rather than
cancelled). A charge the counter typed in has no service record to read, so it
is never flagged — nothing here infers a withdrawal from a description, a date
or an amount. The pharmacy has no equivalent: only a *pending* prescription can
be cancelled, and a pending one has not been charged.
"""
from django.apps import apps
from django.db.models import BooleanField, Exists, ExpressionWrapper, OuterRef, Q

#: A charge that is still somebody's bill. Cancelled is the end state, and a
#: charge written off in full has nothing left to cancel.
LIVE_STATUSES = ("unpaid", "partial", "paid")


def withdrawn_q():
    """The rule, as a filter a queryset of charges can apply or annotate."""
    LabOrderTest = apps.get_model("laboratory", "LabOrderTest")
    still_ordered = (LabOrderTest.objects.filter(pk=OuterRef("source_id"))
                     .exclude(status="cancelled")
                     .exclude(order__status="cancelled"))
    return (Q(status__in=LIVE_STATUSES, source_type="lab_test", source_id__isnull=False)
            & ~Q(Exists(still_ordered)))


def annotate_withdrawn(queryset):
    """Adds `service_withdrawn` to every charge, in the same query."""
    return queryset.annotate(
        service_withdrawn=ExpressionWrapper(withdrawn_q(), output_field=BooleanField()))


def is_withdrawn(charge):
    """The same rule for one charge that did not come through the annotation."""
    return type(charge).objects.filter(pk=charge.pk).filter(withdrawn_q()).exists()
