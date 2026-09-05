"""
Laboratory API.

Who may do what follows the boundaries the rest of the HMIS already draws:

* **The catalogue is configuration.** Anyone clinical may read it — a doctor
  ordering a test needs to know what the lab offers. Only the lab and admin
  may change it, because a reference range is a clinical statement.
* **Results are the bench's.** Only the laboratory role (and admin) enters
  or verifies one. A doctor reads their own patients' results and cannot
  edit them, the same way vitals are the nurse's.
* **The lab never touches money.** `billing` on an order is read from the
  charges the cash desk raised and is never written here — same rule as the
  referral flow, where the counter bills and the clinician refers.
"""
from django.db import models, transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status as drf_status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import RoleRequired
from apps.billing.services import cancel_charge
from apps.core.models import Notification
from apps.core.services import audit_event, notify
from apps.patients.access import patient_queryset_for
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit

from . import services
from .models import (
    LabOrder, LabOrderTest, LabPanel, LabParameter, LabTest, TEST_CATEGORIES,
)
from .serializers import (
    LabOrderSerializer, LabOrderSummarySerializer, LabOrderTestSerializer,
    LabPanelSerializer, LabParameterSerializer, LabTestSerializer,
)

# The bench. Admin is added by RoleRequired itself.
LAB_ROLES = ["laboratory"]
# Who may read the catalogue: a price list and a list of what is offered.
# Reception quotes it and the cash desk bills from it, so it is not clinical.
CATALOGUE_ROLES = ["laboratory", "doctor", "nurse", "reception", "cashier", "accountant"]
# Who may read a *result*. Narrower on purpose, and the same boundary the
# patient overview draws (patients/overview.py): a value is a clinical
# record, not something the front desk or the cash desk has business in.
RESULT_ROLES = ["laboratory", "doctor"]
# Who may see that an order exists, what it is for and whether it is paid —
# without the values. This is what the counter needs to bill it.
WORKLIST_ROLES = ["laboratory", "doctor", "reception", "cashier", "accountant"]
# Who may raise an order. Reception is here because a walk-in lab request is
# taken at the front desk in this hospital; the bench can raise one too.
ORDERING_ROLES = ["doctor", "laboratory", "reception"]


def _display(user):
    return (user.get_full_name() or user.username) if user else None


def _patient_block(patient):
    """The patient identity every printed laboratory document opens with."""
    return {
        "id": patient.pk,
        "name": f"{patient.last_name}, {patient.first_name}",
        "file_number": patient.file_number,
        "age": patient.age_display,
        "sex": patient.get_sex_display(),
        "birthdate": patient.birthdate,
        "phone_number": patient.phone_number,
    }


class LabTestViewSet(viewsets.ModelViewSet):
    """The test catalogue. Configuration, editable without a deployment."""
    queryset = LabTest.objects.prefetch_related("parameters").select_related("billing_item")
    serializer_class = LabTestSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["category", "is_active"]
    search_fields = ["name", "code", "description", "specimen_type"]

    def get_permissions(self):
        if self.action in {"list", "retrieve", "categories"}:
            return [RoleRequired(CATALOGUE_ROLES)]
        return [RoleRequired(LAB_ROLES)]

    def get_queryset(self):
        queryset = super().get_queryset()
        # Active-only is a default for *browsing*, never for addressing a row
        # by id. A detail route already names the row the caller means, and
        # filtering it here made `get_object()` unable to find a retired test
        # — so retiring one was a one-way door and restoring it answered
        # "No LabTest matches the given query."
        if self.action != "list":
            return queryset
        # The picker asks for active only; the catalogue page wants both, so
        # a retired test can be brought back rather than re-created.
        if self.request.query_params.get("all") not in ("1", "true", "True"):
            if "is_active" not in self.request.query_params:
                queryset = queryset.filter(is_active=True)
        return queryset

    def perform_create(self, serializer):
        test = serializer.save()
        audit_event(actor=self.request.user, action="lab.test_created", instance=test,
                    details={"code": test.code}, request=self.request)

    def perform_update(self, serializer):
        test = serializer.save()
        audit_event(actor=self.request.user, action="lab.test_updated", instance=test,
                    details={"code": test.code}, request=self.request)

    def perform_destroy(self, instance):
        """
        Retire, never delete. Results already filed point at this row, and a
        report from last year has to keep saying what it said.
        """
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        audit_event(actor=self.request.user, action="lab.test_deactivated", instance=instance,
                    request=self.request)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        """
        Bring a retired test back onto the catalogue.

        The mirror of `perform_destroy`, and an action of its own rather than
        a PATCH of `is_active`: restoring is one decision, so it is one
        request that cannot carry a half-filled form's other fields with it,
        and it leaves an audit row saying who brought the test back.

        Nothing is re-created — the same row, its parameters and every order
        that already points at it are untouched.
        """
        test = self.get_object()
        if not test.is_active:
            test.is_active = True
            test.save(update_fields=["is_active", "updated_at"])
            audit_event(actor=request.user, action="lab.test_restored", instance=test,
                        details={"code": test.code}, request=request)
        return Response(self.get_serializer(test).data)

    @action(detail=False, methods=["get"])
    def categories(self, request):
        """The catalogue grouped, for the picker and the admin page's tabs."""
        from .models import RESULT_TYPES, TEST_CATEGORIES
        counts = dict(
            LabTest.objects.filter(is_active=True).values_list("category").annotate(
                n=models.Count("id")).values_list("category", "n")
        )
        return Response({
            "categories": [
                {"value": value, "label": label, "count": counts.get(value, 0)}
                for value, label in TEST_CATEGORIES
            ],
            "result_types": [{"value": v, "label": l} for v, l in RESULT_TYPES],
        })


class LabParameterViewSet(viewsets.ModelViewSet):
    """
    A test's result lines. `is_required` is writable but defaults to False —
    the hospital may insist on a value one day; nothing here does.
    """
    queryset = LabParameter.objects.select_related("test")
    serializer_class = LabParameterSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["test", "is_active"]

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [RoleRequired(CATALOGUE_ROLES)]
        return [RoleRequired(LAB_ROLES)]

    def perform_destroy(self, instance):
        # Same reason as a test: results reference it.
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        audit_event(actor=self.request.user, action="lab.parameter_deactivated",
                    instance=instance, request=self.request)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        """Bring a retired result line back — the mirror of retiring one."""
        parameter = self.get_object()
        if not parameter.is_active:
            parameter.is_active = True
            parameter.save(update_fields=["is_active", "updated_at"])
            audit_event(actor=request.user, action="lab.parameter_restored",
                        instance=parameter, request=request)
        return Response(self.get_serializer(parameter).data)

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        """
        Put the lines in the order the bench reads them off the analyser.
        Takes `{"order": [parameter_id, …]}` — position in the list is the
        display order.
        """
        ids = request.data.get("order") or []
        rows = {p.id: p for p in LabParameter.objects.filter(id__in=ids)}
        with transaction.atomic():
            for position, raw in enumerate(ids):
                row = rows.get(services._as_int(raw))
                if row:
                    row.display_order = position
                    row.save(update_fields=["display_order", "updated_at"])
        return Response({"reordered": len(rows)})


class LabPanelViewSet(viewsets.ModelViewSet):
    """
    Named groups of tests — the antenatal profile and the like. A panel
    points at catalogue tests; it never holds a copy of one.
    """
    queryset = LabPanel.objects.prefetch_related("tests")
    serializer_class = LabPanelSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["is_active"]

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [RoleRequired(CATALOGUE_ROLES)]
        return [RoleRequired(LAB_ROLES)]


class LabOrderViewSet(viewsets.ModelViewSet):
    queryset = LabOrder.objects.select_related(
        "patient", "visit", "route", "requested_by", "entered_by", "verified_by",
    ).prefetch_related("items__test", "items__charge__deferrals",
                       "items__values__parameter", "items__amendments")
    serializer_class = LabOrderSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["patient", "visit", "status", "priority", "route"]
    search_fields = ["order_number", "specimen_id", "patient__last_name",
                     "patient__first_name", "patient__file_number"]

    def get_permissions(self):
        if self.action in {"create", "for_route", "add_tests", "remove_test"}:
            # Putting tests on an order is the ordering act itself — a doctor
            # must be able to do it, and the bench may add one the clinician
            # did not think of. Both are billed identically.
            return [RoleRequired(ORDERING_ROLES)]
        if self.action in {"list", "worklist", "request_form"}:
            # The summary serializer answers these — order number, patient,
            # status, what was asked for. No values. The request form is in
            # this group rather than with the report for exactly that reason:
            # it is the sheet that travels with the sample, and it carries
            # nothing the desk may not read.
            return [RoleRequired(WORKLIST_ROLES)]
        if self.action in {"retrieve", "report"}:
            return [RoleRequired(RESULT_ROLES)]
        if self.action == "cancel":
            return [RoleRequired(["laboratory", "doctor"])]
        # Everything else is bench work: collecting the sample, entering
        # values, releasing the report.
        return [RoleRequired(LAB_ROLES)]

    def _may_read_values(self):
        return self.request.user.role in {"admin", "hospital_admin", *RESULT_ROLES}

    def get_serializer_class(self):
        if self.action == "worklist":
            return LabOrderSummarySerializer
        if self.action == "list":
            # The list is the worklist by default — no values, so the counter
            # can bill from it. A chart asking for `?detail=1` gets the whole
            # order, but only for the roles that may read a result at all.
            wants_detail = self.request.query_params.get("detail") in ("1", "true", "True")
            if wants_detail and self._may_read_values():
                return LabOrderSerializer
            return LabOrderSummarySerializer
        return super().get_serializer_class()

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()
        if user.role in {"admin", "hospital_admin", "laboratory"}:
            return queryset
        if user.role == "doctor":
            # The same definition of "my patient" the rest of the chart uses,
            # plus anything this doctor asked for.
            return queryset.filter(
                models.Q(patient__in=patient_queryset_for(user)) |
                models.Q(requested_by=user)
            ).distinct()
        # Reception and the cash desk reach only the worklist, which carries
        # no values — they need to know an order exists in order to bill it.
        return queryset

    def perform_create(self, serializer):
        order = serializer.save(
            requested_by=serializer.validated_data.get("requested_by") or self.request.user,
            created_by=self.request.user,
        )
        tests = self._requested_tests(self.request.data)
        if tests:
            services.add_tests(order=order, tests=tests, author=self.request.user)
        audit_event(actor=self.request.user, action="lab.order_created", instance=order,
                    details={"tests": [t.code for t in tests]}, request=self.request)
        self._tell_the_lab(order)

    def _requested_tests(self, data):
        """
        Tests by id or by code, and panels expanded to the tests they name —
        which is how the antenatal profile arrives without duplicating a
        single catalogue entry.
        """
        ids = data.get("tests") or []
        codes = data.get("test_codes") or []
        panel_codes = data.get("panels") or []
        found = list(LabTest.objects.filter(
            models.Q(id__in=[services._as_int(i) for i in ids if services._as_int(i)]) |
            models.Q(code__in=codes),
            is_active=True,
        ))
        for panel in LabPanel.objects.filter(code__in=panel_codes).prefetch_related("tests"):
            found.extend(t for t in panel.tests.all() if t.is_active)
        # De-duplicated, order preserved: a panel and a loose test can name
        # the same FBC and it must go on the order once.
        seen, unique = set(), []
        for test in found:
            if test.pk not in seen:
                seen.add(test.pk)
                unique.append(test)
        return unique

    def _tell_the_lab(self, order):
        from apps.accounts.models import User
        for user in User.objects.filter(role="laboratory", is_active=True):
            notify(recipient=user,
                   title=f"Laboratory request: {order.patient}",
                   message=", ".join(i.name for i in order.items.all())
                           or (order.clinical_notes or "No tests named yet."),
                   category="routing", action_url="/laboratory")

    def _fresh(self, order):
        """
        Re-read after changing the order's tests. The instance the action was
        given carries a prefetched `items` list from before the change, and
        serializing that hands the caller the state they just replaced.
        """
        return self.get_queryset().get(pk=order.pk)

    @action(detail=False, methods=["get"], url_path="worklist")
    def worklist(self, request):
        """The bench's list: everything not yet released, urgent first."""
        rows = self.get_queryset().exclude(status__in=["completed", "cancelled"])
        page = self.paginate_queryset(rows)
        serializer = self.get_serializer(page if page is not None else rows, many=True)
        return self.get_paginated_response(serializer.data) if page is not None \
            else Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="for-route")
    def for_route(self, request):
        """
        The order behind a laboratory referral, made on first sight.

        The station opens a route and calls this; the doctor's referral is
        already the request, so nobody is asked to raise a second one.
        """
        route = PatientRoute.objects.filter(
            pk=services._as_int(request.data.get("route"))
        ).select_related("visit__patient", "routed_by").first()
        if route is None:
            return Response({"route": "No such route."}, status=drf_status.HTTP_404_NOT_FOUND)
        if route.purpose not in ("laboratory", "investigation"):
            return Response({"route": "That referral is not for the laboratory."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        order = services.order_for_route(route, author=request.user)
        return Response(LabOrderSerializer(order).data)

    @action(detail=True, methods=["post"], url_path="add-tests")
    def add_tests(self, request, pk=None):
        """Put more tests on an order — the doctor asked for two more."""
        order = self.get_object()
        tests = self._requested_tests(request.data)
        if not tests:
            return Response({"tests": "Choose at least one test."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        # Who is adding is read from their role, not from the request body:
        # a line marked "requested by the clinician" has to actually have
        # been. Both sources are billed the same way.
        source = "laboratory" if request.user.role == "laboratory" else "requested"
        added = services.add_tests(order=order, tests=tests, author=request.user, source=source)
        audit_event(actor=request.user, action="lab.tests_added", instance=order,
                    details={"tests": [i.test.code for i in added], "source": source,
                             "charged": [str(i.unit_price) for i in added]}, request=request)
        return Response(self.get_serializer(self._fresh(order)).data)

    @action(detail=True, methods=["post"], url_path="remove-test")
    def remove_test(self, request, pk=None):
        """Take a test off — ordered by mistake, or the sample never came."""
        order = self.get_object()
        item = order.items.filter(pk=services._as_int(request.data.get("order_test"))).first()
        if item is None:
            return Response({"order_test": "That test is not on this order."},
                            status=drf_status.HTTP_404_NOT_FOUND)
        # The charge goes with it — a test nobody is going to run must not
        # sit on the patient's bill. Cancelled rather than deleted, so the
        # ledger keeps the history; a charge already part-paid is left for
        # the cash desk to refund, because the lab cannot move money.
        charge = item.charge
        if charge and charge.amount_paid <= 0 and charge.status != "cancelled":
            cancel_charge(charge=charge)

        if item.values.exists():
            # A result has been filed against it: cancel rather than delete,
            # so the values stay auditable.
            item.status = "cancelled"
            item.save(update_fields=["status", "updated_at"])
        else:
            item.delete()
        audit_event(actor=request.user, action="lab.test_removed", instance=order,
                    details={"charge_cancelled": bool(charge and charge.amount_paid <= 0)},
                    request=request)
        return Response(self.get_serializer(self._fresh(order)).data)

    @action(detail=True, methods=["post"], url_path="collect-sample")
    def collect_sample(self, request, pk=None):
        """Log the specimen. A result belongs to a sample, and a sample to a time."""
        order = self.get_object()
        order.specimen_id = (request.data.get("specimen_id") or order.specimen_id or "").strip()[:40]
        order.specimen_collected_at = timezone.now()
        order.collected_by = request.user
        if order.status == "requested":
            order.status = "collected"
        order.save(update_fields=["specimen_id", "specimen_collected_at", "collected_by",
                                  "status", "updated_at"])
        audit_event(actor=request.user, action="lab.sample_collected", instance=order,
                    request=request)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="save-results")
    def save_results(self, request, pk=None):
        """
        Enter what was measured — and only that.

        `{"order_test": id, "values": [{"parameter": id, "value": "13.5"}, …],
        "comments": "...", "submit": false}`.

        A blank value is not an error and is not stored; an entry that was
        previously filed and is now blank is cleared. Nothing here checks
        that every parameter has a value, because that is the one thing this
        module exists to avoid. `submit: true` submits it for verification.
        """
        order = self.get_object()
        item = order.items.filter(
            pk=services._as_int(request.data.get("order_test"))
        ).select_related("test").first()
        if item is None:
            return Response({"order_test": "That test is not on this order."},
                            status=drf_status.HTTP_404_NOT_FOUND)
        if order.status == "cancelled":
            return Response({"detail": "This order was cancelled."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if item.status == "cancelled":
            return Response({"detail": "That test was cancelled."},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        # A released result is a document that has gone to a doctor. It can
        # still be corrected — a transcription error has to be fixable — but
        # only through the amendment path, which insists on a reason and
        # records the change against a name.
        reason = str(request.data.get("reason") or "").strip()
        if item.status == "verified" and not reason:
            return Response(
                {"code": "amendment_reason_required",
                 "detail": "This result has been released. Say what is being corrected and why."},
                status=drf_status.HTTP_400_BAD_REQUEST)

        written, cleared = services.save_values(
            order_test=item, entries=request.data.get("values") or [],
            author=request.user, reason=reason,
        )
        comments = request.data.get("comments")
        if comments is not None:
            item.comments = str(comments)[:2000]
            item.save(update_fields=["comments", "updated_at"])

        submitting = request.data.get("submit") in (True, "true", "True", 1, "1")
        if submitting:
            if not item.values.exists() and not (item.comments or "").strip():
                # Submitting is a claim that the test was done. Nothing at all
                # is not a result — but *one* value is, and that is the point.
                return Response(
                    {"detail": "Enter at least one result, or a comment, before submitting.",
                     "code": "empty_result"},
                    status=drf_status.HTTP_400_BAD_REQUEST)
            problem = self._set_recipient(order, request)
            if problem:
                return Response({"notify_doctor": problem},
                                status=drf_status.HTTP_400_BAD_REQUEST)
            services.mark_resulted(order_test=item, author=request.user)
            self._tell_the_doctor(order, item)
        else:
            if item.status == "pending":
                item.status = "draft"
                item.save(update_fields=["status", "updated_at"])
            services.touch_order(order, request.user)

        audit_event(
            actor=request.user,
            action="lab.result_submitted" if submitting else "lab.result_saved",
            instance=order,
            details={"test": item.test.code, "values": written, "cleared": cleared},
            request=request,
        )
        order.refresh_from_db()
        return Response(LabOrderSerializer(order).data)

    def _result_recipient(self, order):
        """
        The one doctor a result is addressed to: whoever the bench redirected
        it to, else the doctor who asked for it. Never a broadcast — a result
        sent to every doctor is a result nobody owns.
        """
        doctor = order.report_to or order.requested_by
        return doctor if doctor and doctor.is_active else None

    def _set_recipient(self, order, request):
        """
        Let the bench redirect the report. The doctor who referred is the
        default and needs no thought; naming someone else is for when that
        doctor is off and a colleague is covering the patient.
        """
        chosen = services._as_int(request.data.get("notify_doctor"))
        if not chosen:
            return None
        from apps.accounts.models import User
        doctor = User.objects.filter(pk=chosen, role="doctor", is_active=True).first()
        if doctor is None:
            return "That doctor is not available to receive this result."
        if order.report_to_id != doctor.pk:
            order.report_to = doctor
            order.save(update_fields=["report_to", "updated_at"])
        return None

    def _tell_the_doctor(self, order, item):
        doctor = self._result_recipient(order)
        if not doctor:
            return
        # One notification per test per person. Submitting tells the doctor
        # the figure is there; verifying it afterwards is the same news, and
        # sending it twice is how a chart's bell stops being read.
        title = f"Laboratory result: {item.name} — {order.patient}"
        if Notification.objects.filter(recipient=doctor, title=title,
                                       category="clinical").exists():
            return
        notify(recipient=doctor,
               title=title,
               message=f"{order.order_number} is ready on the chart.",
               category="clinical",
               # Straight to the Lab tab on the chart, not the chart's front
               # page — the doctor came to read the result, not to look for it.
               action_url=f"/patients/{order.patient_id}/lab")

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """
        Release the report. A second signature, recorded against a name and a
        time — it is what "verified by" on the printed report means.
        """
        order = self.get_object()
        if not order.items.filter(status="submitted").exists():
            return Response({"detail": "Nothing has been submitted on this order yet."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        problem = self._set_recipient(order, request)
        if problem:
            return Response({"notify_doctor": problem}, status=drf_status.HTTP_400_BAD_REQUEST)
        comments = request.data.get("lab_comments")
        if comments is not None:
            order.lab_comments = str(comments)[:2000]
            order.save(update_fields=["lab_comments", "updated_at"])
        services.verify_order(order=order, author=request.user)
        self._close_the_route(order, request)
        audit_event(actor=request.user, action="lab.order_verified", instance=order,
                    request=request)
        for item in order.items.filter(status="verified"):
            self._tell_the_doctor(order, item)
        order.refresh_from_db()
        return Response(LabOrderSerializer(order).data)

    def _close_the_route(self, order, request):
        """
        A verified report is the referral answered, so the patient comes off
        the lab's queue and the finding lands on the chart the way any other
        unit's does — through the workflow the hospital already has.
        """
        route = order.route
        if route is None or route.status in ("completed", "cancelled"):
            return
        summary = self._summary_text(order)
        route.result = summary[:5000]
        route.result_by = request.user
        route.result_at = timezone.now()
        route.status = "completed"
        route.save(update_fields=["result", "result_by", "result_at", "status", "updated_at"])

        # Same permanent copy every other unit files, so the result is on the
        # patient's Tests & Diagnostics record after the visit closes.
        from apps.workflow.views import _file_result_on_the_record
        _file_result_on_the_record(
            route, request.user,
            title=", ".join(i.name for i in order.items.exclude(status="cancelled")),
        )

    def _summary_text(self, order):
        lines = []
        for section in services.report_lines(order):
            lines.append(section["order_test"].name)
            for value in section["values"]:
                unit = value.unit_at_entry or value.parameter.unit
                flag = f" [{value.get_flag_display()}]" if value.flag in ("low", "high", "critical", "abnormal") else ""
                lines.append(f"  {value.parameter.name}: {value.value}"
                             f"{' ' + unit if unit else ''}{flag}")
            if section["comments"]:
                lines.append(f"  Comment: {section['comments']}")
        if order.lab_comments:
            lines.append(f"Laboratory: {order.lab_comments}")
        return "\n".join(lines) or "See the laboratory report."

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        order.status = "cancelled"
        order.save(update_fields=["status", "updated_at"])
        audit_event(actor=request.user, action="lab.order_cancelled", instance=order,
                    details={"reason": request.data.get("reason", "")}, request=request)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["get"], url_path="request-form")
    def request_form(self, request, pk=None):
        """
        The laboratory request form — what was asked for, for whom, and on
        what specimen. **Never a result value.**

        This is the sheet that goes with the sample and gets pinned to the
        bench's worksheet, so it is deliberately in the worklist's permission
        group rather than the report's: the front desk and the cash desk can
        print and file it without ever seeing a figure. Everything on it is
        read from the order-time snapshot, so a re-priced catalogue does not
        rewrite the form the patient was handed.

        **The clinical detail is not for the desk.** "Query malaria, please
        run FBC" is the doctor writing to the bench; it is why the test was
        ordered, which is the chart. The desk needs to know a sample is due
        and what it costs, so it gets the form without that line — the same
        boundary the front desk's queue draws.
        """
        order = self.get_object()
        doctor = order.requested_by
        items = [item for item in order.items.all() if item.status != "cancelled"]
        may_read_clinical = self._may_read_values()
        return Response({
            "order": {
                "id": order.pk,
                "order_number": order.order_number,
                "status": order.get_status_display(),
                "priority": order.get_priority_display(),
                "clinical_notes": order.clinical_notes if may_read_clinical else "",
                "specimen_id": order.specimen_id,
                "specimen_collected_at": order.specimen_collected_at,
                "collected_by": _display(order.collected_by),
                "requested_by": _display(doctor),
                "report_to": _display(order.report_to or doctor),
                "created_at": order.created_at,
                "visit": order.visit_id,
            },
            "patient": _patient_block(order.patient),
            "tests": [
                {
                    "id": item.pk,
                    "name": item.name,
                    "category": dict(TEST_CATEGORIES).get(item.test_category, ""),
                    "specimen_type": item.specimen_type,
                    "container": item.container,
                    "status": item.get_status_display(),
                    "source": item.get_source_display(),
                    # What it cost when it was ordered, and where that stands.
                    # The bench reads it; the bench never acts on it.
                    "price": f"{item.unit_price:.2f}",
                    "billing": LabOrderTestSerializer().get_billing(item),
                }
                for item in items
            ],
            "billing": LabOrderSerializer().get_billing(order),
        })

    @action(detail=True, methods=["get"])
    def report(self, request, pk=None):
        """
        Everything the printed report needs, in one call — and **only the
        parameters that have a result**. A blank line on a laboratory report
        is a question nobody can answer later.
        """
        order = self.get_object()
        patient = order.patient
        sections = []
        for section in services.report_lines(order):
            item = section["order_test"]
            sections.append({
                # All from the order-time snapshot: a test renamed or
                # recategorised since must not rewrite this report.
                "test_name": item.name,
                "test_code": item.test.code,
                "category": dict(TEST_CATEGORIES).get(item.test_category, ""),
                "specimen_type": item.specimen_type,
                "status": item.get_status_display(),
                "released": item.is_released,
                "comments": section["comments"],
                "verified_by": (item.verified_by.get_full_name() or item.verified_by.username)
                if item.verified_by else None,
                "verified_at": item.verified_at,
                "performed_by": (item.performed_by.get_full_name()
                                 or item.performed_by.username)
                if item.performed_by else None,
                "performed_at": item.performed_at,
                "values": [
                    {
                        "parameter": value.parameter.name,
                        "group": value.parameter.group,
                        "value": value.value,
                        "unit": value.unit_at_entry or value.parameter.unit,
                        "reference_range": value.reference_at_entry or value.parameter.reference_range,
                        "flag": value.flag,
                        "flag_label": value.get_flag_display() if value.flag else "",
                        "comment": value.comment,
                    }
                    for value in section["values"]
                ],
            })

        doctor = order.requested_by
        return Response({
            "order": {
                "id": order.pk,
                "order_number": order.order_number,
                "status": order.get_status_display(),
                "priority": order.get_priority_display(),
                "specimen_id": order.specimen_id,
                "specimen_collected_at": order.specimen_collected_at,
                "clinical_notes": order.clinical_notes,
                "lab_comments": order.lab_comments,
                "created_at": order.created_at,
                "entered_at": order.entered_at,
                "entered_by": (order.entered_by.get_full_name() or order.entered_by.username)
                if order.entered_by else None,
                "verified_at": order.verified_at,
                "verified_by": (order.verified_by.get_full_name() or order.verified_by.username)
                if order.verified_by else None,
                "requested_by": (doctor.get_full_name() or doctor.username) if doctor else None,
                "visit": order.visit_id,
            },
            "patient": _patient_block(patient),
            "sections": sections,
        })
