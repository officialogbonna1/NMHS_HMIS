"""
The maternity API: look a mother up, continue what she is already in, and
record today.
"""
import uuid as uuid_module

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status as drf_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import (IsAdmin, MATERNITY_ASSIGN_ROLES, MATERNITY_DESK_ROLES,
                                       MATERNITY_ROLES, RoleRequired)
from apps.core.config import ProtectedConfigMixin
from apps.patients.models import Patient

from . import access, services
from apps.inpatient.models import Admission

from .models import (Delivery, LabourEpisode, MaternityEncounter, MaternityOption,
                     MaternityVisitType, Newborn, Pregnancy)
from .serializers import (DeliverySerializer, LabourDetailSerializer,
                          LabourEpisodeSerializer, LabourObservationSerializer,
                          MaternityEncounterSerializer, MaternityOptionSerializer,
                          MaternityPatientSerializer, MaternityStaffSerializer,
                          MaternityVisitTypeSerializer, NewbornSerializer,
                          PostpartumVisitSerializer, PregnancySerializer,
                          PregnancyTimelineSerializer, gestation_text)


def _patient_or_404(value):
    """
    A patient by hospital number, UUID or primary key — whatever the desk has
    in its hand. The number is what she reads off her card, the UUID is what a
    link carries, and the pk is what a filter passes (rule 32's three
    identifiers, all answered here rather than in three call sites).
    """
    if not value:
        raise NotFound("Name a patient.")
    value = str(value).strip()
    found = Patient.objects.filter(patient_number__iexact=value).first()
    if found is None and value.isdigit():
        found = Patient.objects.filter(pk=int(value)).first()
    if found is None:
        try:
            found = Patient.objects.filter(uuid=uuid_module.UUID(value)).first()
        except (ValueError, AttributeError, TypeError):
            found = None
    if found is None:
        raise NotFound("No such patient.")
    return found


class AmendableRecordMixin:
    """
    The update half of a maternity clinical record: **a correction, never an
    in-place edit.**

    `clinical.ConsultationNoteViewSet.update` is the pattern (rule 45) and
    this is it, over maternity's records:

    - `may_amend` decides — the person who recorded it, or an administrator.
      Working in Maternity is what opens the ward's records; correcting
      somebody else's entry is a different act, and the consultation note has
      always drawn that line at the author.
    - The reason is checked **before** the serializer, so a 403 for the wrong
      person and a missing-reason 400 answer ahead of a field-level complaint
      about the payload.
    - The snapshot is written **first**, inside the same transaction as the
      save, so a refused save leaves no trail describing a correction that did
      not happen.
    - The record is re-read from the database for that snapshot
      (`_before`), because the instance the serializer holds has already been
      written over by the time `perform_update` runs — the mistake
      `clinical.services.note_before()` exists to avoid.

    DELETE is not here. A maternity clinical record is never removed through
    the API; rule 38's distinction applies — a record filed by mistake is
    corrected or cancelled, not erased.
    """
    #: One statement of which verbs a maternity clinical record answers. No
    #: PUT (a whole-record replace is not a correction) and no DELETE.
    http_method_names = ["get", "post", "patch", "head", "options"]

    def update(self, request, *args, **kwargs):
        from . import amendments

        instance = self.get_object()
        if not amendments.may_amend(request.user, instance):
            raise PermissionDenied(
                "You can only correct a record you entered. Ask an administrator.")
        # Asked before the serializer so the refusal carries a flat `code`
        # the frontend can read — DRF wraps a serializer's codes in lists
        # (rule 55). `blocked_changes` is the one rule; the serializer asks it
        # again for any other caller.
        blocked = amendments.blocked_changes(instance, self._offered(request, instance))
        if blocked:
            return Response(
                {"detail": ("These are part of what identifies this record and cannot "
                            "be changed: " + ", ".join(amendments.label_for(f) for f in blocked)
                            + "."),
                 "code": amendments.IMMUTABLE_FIELD, "fields": blocked},
                status=drf_status.HTTP_400_BAD_REQUEST)
        reason = request.data.get("amendment_reason")
        if not amendments.is_valid_reason(reason):
            return Response(
                {"detail": "Say why this record is being corrected.",
                 "code": amendments.REASON_REQUIRED,
                 "reasons": amendments.reason_choices()},
                status=drf_status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def _offered(self, request, instance):
        """
        The protected fields this request actually names, resolved to what
        they would become — so `{"patient": 4}` is compared with the patient
        the record holds rather than with the raw id.
        """
        from . import amendments

        offered = {}
        for field in amendments.immutable_fields(instance):
            if field not in request.data:
                continue
            sent = request.data.get(field)
            current = getattr(instance, field, None)
            current_id = getattr(instance, f"{field}_id", None)
            if current_id is not None:
                offered[field] = current if str(sent) == str(current_id) else sent
            else:
                offered[field] = sent if str(sent) != str(current) else current
        return offered

    def perform_update(self, serializer):
        from django.db import transaction

        from . import amendments

        request = self.request
        with transaction.atomic():
            # Re-read: `serializer.instance` is about to be written over, and
            # a snapshot of the new values would be worse than none.
            before = self.get_queryset().model.objects.get(pk=serializer.instance.pk)
            amendments.archive(instance=before, actor=request.user,
                               reason=request.data.get("amendment_reason", ""),
                               detail=request.data.get("amendment_detail", ""))
            record = serializer.save()
            amendments.audit_amended(instance=record, actor=request.user,
                                     reason=request.data.get("amendment_reason", ""),
                                     request=request)

    @action(detail=True, methods=["get"], url_path="amendments")
    def amendments_list(self, request, pk=None):
        """
        This record's corrections, newest first — what it said before each
        one, what changed, who changed it, when and why.

        A read on the record rather than a list endpoint of its own: an
        amendment only means anything beside the thing it corrected, and the
        chart asks for it where it is shown (the consultation note's trail is
        an inline on the note for the same reason).
        """
        from django.contrib.contenttypes.models import ContentType

        from .models import MaternityAmendment
        from .serializers import MaternityAmendmentSerializer

        record = self.get_object()
        rows = MaternityAmendment.objects.filter(
            content_type=ContentType.objects.get_for_model(type(record)),
            object_id=record.pk).select_related("amended_by")
        return Response(MaternityAmendmentSerializer(rows, many=True).data)

    @action(detail=False, methods=["get"], url_path="amendment-reasons")
    def amendment_reasons(self, request):
        """The reasons a correction may be given for, so the form never
        hard-codes them (rule 21's principle: the list is data to the screen)."""
        from . import amendments

        return Response(amendments.reason_choices())


class MaternityLookupView(APIView):
    """
    **Is she known, and is she pregnant right now?**

    The one read the maternity desk makes before doing anything else, and the
    answer to the whole returning-patient problem: her identity as the
    hospital already holds it, the pregnancy she is currently in, everything
    she has been through before, and therefore which of the two actions
    exists. Reception is never left to decide that from memory, and because
    the *server* decides it, the screen cannot offer "start a new pregnancy"
    to a woman who is already in one.
    """

    def get_permissions(self):
        return [RoleRequired(MATERNITY_DESK_ROLES)]

    def get_serializer_context(self):
        return {"request": self.request, "view": self}

    def get(self, request):
        patient = _patient_or_404(request.query_params.get("patient"))
        view = services.desk_view_for(patient)
        active = view["active_pregnancy"]
        return Response({
            "patient": {
                "id": patient.pk,
                "uuid": str(patient.uuid),
                "name": patient.display_name,
                "patient_number": patient.patient_number,
                "phone_number": patient.phone_number,
                "sex": patient.sex,
            },
            # `known` is always true here — the patient was found — and is
            # carried so the screen's three states read from one shape rather
            # than from the absence of a key.
            "known": True,
            # Context carried so the serializer knows who is reading: the
            # front desk gets the episode without the clinical fields on it.
            "active_pregnancy": (PregnancySerializer(active, context=self.get_serializer_context()).data
                                 if active else None),
            "history": PregnancySerializer(view["history"], many=True,
                                           context=self.get_serializer_context()).data,
            "can_continue": view["can_continue"],
            "can_start": view["can_start"],
            "admission": access.admission_state(patient),
            "has_history": view["has_history"],
            "visit_types": MaternityVisitTypeSerializer(
                services.bookable_visit_types(), many=True).data,
        })


class PregnancyViewSet(AmendableRecordMixin, viewsets.ModelViewSet):
    """
    The episodes. Created through the service, so a second active one is
    refused rather than written.
    """
    queryset = Pregnancy.objects.select_related("patient", "opened_by").all()
    serializer_class = PregnancySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["patient", "status"]

    def get_permissions(self):
        # The desk reads *that she has a pregnancy* — which is the whole of
        # the returning-patient answer and what the lookup already tells it.
        # `timeline` is the visit list, so it is the chart and is not the
        # desk's, the same boundary rule 17 draws on a clinician's referral
        # notes appearing on the front-desk queue.
        if self.action in ("list", "retrieve"):
            return [RoleRequired(MATERNITY_DESK_ROLES)]
        return [RoleRequired(MATERNITY_ROLES)]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            pregnancy = services.start_pregnancy(
                patient=serializer.validated_data["patient"],
                actor=request.user,
                lmp=serializer.validated_data.get("lmp"),
                edd=serializer.validated_data.get("edd"),
                gravida=serializer.validated_data.get("gravida"),
                para=serializer.validated_data.get("para"),
                notes=serializer.validated_data.get("notes", ""),
            )
        except services.MaternityError as refusal:
            return Response({"detail": str(refusal), "code": refusal.code},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(pregnancy).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        """This pregnancy with every visit in it, oldest history intact."""
        return Response(PregnancyTimelineSerializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """End the episode with an outcome. The visits in it are untouched."""
        try:
            pregnancy = services.close_pregnancy(
                pregnancy=self.get_object(), actor=request.user,
                outcome=request.data.get("outcome", ""),
                notes=request.data.get("notes", ""),
            )
        except services.MaternityError as refusal:
            return Response({"detail": str(refusal), "code": refusal.code},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(pregnancy).data)


class MaternityEncounterViewSet(AmendableRecordMixin, viewsets.ModelViewSet):
    """
    Today's attendances. **Create and read only** — `http_method_names` has no
    PUT and no DELETE, because an earlier visit is a record of a day and
    correcting it by overwriting is how ANC 2 stops being true (section 4).
    """
    queryset = (MaternityEncounter.objects
                .select_related("pregnancy__patient", "visit_type", "provider").all())
    serializer_class = MaternityEncounterSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["pregnancy", "visit_type", "status", "provider"]

    def get_permissions(self):
        # Clinical throughout, reads included: an encounter carries the
        # clinician's own summary of the attendance, which is the chart.
        return [RoleRequired(MATERNITY_ROLES)]

    def create(self, request, *args, **kwargs):
        pregnancy = self._named(Pregnancy, request.data.get("pregnancy"), "pregnancy")
        visit_type = self._named(MaternityVisitType, request.data.get("visit_type"),
                                 "visit type")
        try:
            encounter = services.open_encounter(
                pregnancy=pregnancy, visit_type=visit_type, actor=request.user,
                provider=self._provider(request),
                summary=request.data.get("summary", ""),
            )
        except services.MaternityError as refusal:
            return Response({"detail": str(refusal), "code": refusal.code},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(encounter).data, status=drf_status.HTTP_201_CREATED)

    def _provider(self, request):
        """
        Who is seeing her. Defaults to the person recording it, which is the
        usual case — a midwife opening her own clinic's encounter.
        """
        from apps.accounts.models import User

        named = request.data.get("provider")
        if not named:
            return request.user
        return User.objects.filter(pk=named, is_active=True).first() or request.user

    def _named(self, model, value, what):
        found = model.objects.filter(pk=value).first() if value else None
        if found is None:
            raise NotFound(f"Name the {what}.")
        return found


class MaternityVisitTypeViewSet(ProtectedConfigMixin, viewsets.ModelViewSet):
    """
    The clinics the hospital runs. Everyone who works maternity reads them;
    only an admin changes them, and one that has attendances behind it is
    retired rather than deleted (rule 31).
    """
    queryset = MaternityVisitType.objects.all()
    serializer_class = MaternityVisitTypeSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["is_active"]
    protected_relations = ("encounters",)

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [RoleRequired(MATERNITY_DESK_ROLES)]
        return [IsAdmin()]


class MaternityOptionViewSet(ProtectedConfigMixin, viewsets.ModelViewSet):
    """
    The hospital's maternity vocabulary — delivery types, outcomes,
    complications, newborn states, maternal conditions, family planning.

    Everyone who works maternity reads it; only an admin changes it. A word a
    record has used is retired rather than deleted (rule 31), which is what
    keeps an old delivery note readable after the list has moved on.
    """
    queryset = MaternityOption.objects.all()
    serializer_class = MaternityOptionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["kind", "is_active"]
    protected_relations = ("deliveries_of_type", "deliveries_with_outcome",
                           "deliveries_with_complication", "newborns_with_status",
                           "deliveries_with_maternal_condition",
                           "postpartum_with_condition", "postpartum_with_family_planning")

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [RoleRequired(MATERNITY_DESK_ROLES)]
        return [IsAdmin()]


class LabourEpisodeViewSet(AmendableRecordMixin, viewsets.ModelViewSet):
    """
    Labour: open it, watch it, close it with the delivery.

    Everything that moves the episode on goes through `maternity/services.py`,
    so a delivery and its labour's closure are one transaction and a
    partogram entry is always a new row.
    """
    queryset = (LabourEpisode.objects
                .select_related("pregnancy__patient", "admission__bed__ward", "opened_by")
                .prefetch_related("observations").all())
    serializer_class = LabourEpisodeSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["pregnancy", "status", "stage"]

    def get_permissions(self):
        # The labour, the partogram and everything under them are clinical
        # records. The front desk reaches none of it, in either direction:
        # reading a partogram is reading the chart.
        return [RoleRequired(MATERNITY_ROLES)]

    def get_serializer_class(self):
        return LabourDetailSerializer if self.action == "retrieve" else self.serializer_class

    def create(self, request, *args, **kwargs):
        pregnancy = self._row(Pregnancy, request.data.get("pregnancy"), "pregnancy")
        admission = (self._row(Admission, request.data.get("admission"), "admission")
                     if request.data.get("admission") else None)
        try:
            labour = services.open_labour(
                pregnancy=pregnancy, actor=request.user,
                onset=request.data.get("onset", "spontaneous"),
                started_at=request.data.get("started_at") or None,
                admission=admission, notes=request.data.get("notes", ""))
        except services.MaternityError as refusal:
            return Response({"detail": str(refusal), "code": refusal.code},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(labour).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="admit")
    def admit(self, request, pk=None):
        """Link the labour to the admission the ward made — no second bed board."""
        admission = self._row(Admission, request.data.get("admission"), "admission")
        try:
            labour = services.attach_admission(labour=self.get_object(),
                                               admission=admission, actor=request.user)
        except services.MaternityError as refusal:
            return Response({"detail": str(refusal), "code": refusal.code},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(labour).data)

    @action(detail=True, methods=["post"], url_path="observations")
    def observations(self, request, pk=None):
        """One partogram entry. Always a new row."""
        readings = {key: request.data.get(key) for key in (
            "cervical_dilation_cm", "contractions_per_10min",
            "contraction_duration_seconds", "fetal_heart_rate", "membranes",
            "maternal_condition", "notes")}
        try:
            observation = services.record_observation(
                labour=self.get_object(), actor=request.user,
                observed_at=request.data.get("observed_at") or None, **readings)
        except services.MaternityError as refusal:
            return Response({"detail": str(refusal), "code": refusal.code},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(LabourObservationSerializer(observation).data,
                        status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="delivery")
    def delivery(self, request, pk=None):
        """
        Close the labour with the delivery and the baby or babies.

        `newborns` is a list, which is how twins are recorded: one delivery,
        several `Newborn` rows, one pregnancy throughout.
        """
        delivery_type = MaternityOption.objects.filter(
            pk=request.data.get("delivery_type"),
            kind=MaternityOption.DELIVERY_TYPE).first()
        details = {
            "outcome": self._option(request.data.get("outcome"),
                                    MaternityOption.DELIVERY_OUTCOME),
            "maternal_condition": self._option(request.data.get("maternal_condition"),
                                               MaternityOption.MOTHER_CONDITION),
            "doctor_id": request.data.get("doctor") or None,
            "midwife_id": request.data.get("midwife") or None,
            "estimated_blood_loss_ml": request.data.get("estimated_blood_loss_ml") or None,
            "placenta_complete": request.data.get("placenta_complete"),
            "placenta_notes": request.data.get("placenta_notes", ""),
            "notes": request.data.get("notes", ""),
            "complications": MaternityOption.objects.filter(
                pk__in=request.data.get("complications") or [],
                kind=MaternityOption.COMPLICATION),
        }
        try:
            delivery = services.record_delivery(
                labour=self.get_object(), actor=request.user,
                delivered_at=request.data.get("delivered_at") or None,
                delivery_type=delivery_type,
                newborns=[self._baby(baby) for baby in (request.data.get("newborns") or [])],
                **details)
        except services.MaternityError as refusal:
            return Response({"detail": str(refusal), "code": refusal.code},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(DeliverySerializer(delivery).data, status=drf_status.HTTP_201_CREATED)

    def _baby(self, baby):
        """
        One baby off the request, with its `status` resolved to the option it
        names.

        The view is where a request's ids become objects — the same thing it
        does for the delivery's own outcome and complications — and a nested
        list is no exception. Passing the raw id down reached the model as an
        integer and answered 500, which a walk-through of a real delivery is
        what caught.
        """
        resolved = {key: value for key, value in (baby or {}).items() if key != "status"}
        if baby.get("status"):
            resolved["status"] = self._option(baby["status"], MaternityOption.NEWBORN_STATUS)
        return resolved

    def _option(self, value, kind):
        return MaternityOption.objects.filter(pk=value, kind=kind).first() if value else None

    def _row(self, model, value, what):
        found = model.objects.filter(pk=value).first() if value else None
        if found is None:
            raise NotFound(f"Name the {what}.")
        return found


class DeliveryViewSet(AmendableRecordMixin, viewsets.ModelViewSet):
    """
    The deliveries. Created by closing a labour (never on its own, so a birth
    always has the labour it came from), read by the desk, and extended with
    further babies and postpartum checks.
    """
    queryset = (Delivery.objects
                .select_related("labour__pregnancy__patient", "delivery_type", "outcome",
                                "doctor", "midwife")
                .prefetch_related("newborns", "postpartum_visits", "complications").all())
    serializer_class = DeliverySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["delivery_type", "outcome"]

    def get_permissions(self):
        # A delivery, its complications, the babies and the postpartum course
        # are the clinical record of the birth.
        return [RoleRequired(MATERNITY_ROLES)]

    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "Record a delivery by closing its labour: "
                       "POST /api/labour-episodes/<id>/delivery/.",
             "code": "use_labour_delivery"},
            status=drf_status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path="newborns")
    def newborns(self, request, pk=None):
        """A further baby on this delivery — the second twin, the third triplet."""
        baby = {key: request.data.get(key) for key in (
            "name", "sex", "birth_weight_grams", "apgar_1_min", "apgar_5_min",
            "apgar_10_min", "congenital_abnormalities", "notes") if request.data.get(key)}
        baby["admitted_to_nursery"] = bool(request.data.get("admitted_to_nursery"))
        status_option = MaternityOption.objects.filter(
            pk=request.data.get("status"), kind=MaternityOption.NEWBORN_STATUS).first()
        newborn = services.add_newborn(delivery=self.get_object(), actor=request.user,
                                       status=status_option, **baby)
        return Response(NewbornSerializer(newborn).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="postpartum")
    def postpartum(self, request, pk=None):
        """A postpartum check. A course of care, so always a new row."""
        findings = {key: request.data.get(key) for key in (
            "bleeding", "wound_condition", "baby_feeding", "discharge_advice",
            "notes", "follow_up_on")}
        findings["breastfeeding_established"] = request.data.get("breastfeeding_established")
        findings["mother_condition"] = MaternityOption.objects.filter(
            pk=request.data.get("mother_condition"),
            kind=MaternityOption.MOTHER_CONDITION).first()
        findings["family_planning"] = MaternityOption.objects.filter(
            pk=request.data.get("family_planning"),
            kind=MaternityOption.FAMILY_PLANNING).first()
        visit = services.record_postpartum(
            delivery=self.get_object(), actor=request.user,
            seen_at=request.data.get("seen_at") or None, **findings)
        return Response(PostpartumVisitSerializer(visit).data,
                        status=drf_status.HTTP_201_CREATED)


class NewbornViewSet(viewsets.ReadOnlyModelViewSet):
    """The birth register. Babies are recorded against their delivery."""
    queryset = Newborn.objects.select_related("delivery__labour__pregnancy__patient",
                                              "status").all()
    serializer_class = NewbornSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["delivery", "sex", "admitted_to_nursery"]

    def get_permissions(self):
        # The birth register is a clinical record of a baby.
        return [RoleRequired(MATERNITY_ROLES)]


class MaternityPatientsView(APIView):
    """
    **The ward's patients** — what the maternity picker opens onto.

    Click, and the mothers this user may work with are there. That is the
    whole of it, and it is why this exists rather than the picker calling
    `/patients/`: the generic list is every patient in the hospital for
    reception, and was empty for a midwife, so the desk was left typing a
    hospital number in full and pressing Enter to find anybody at all.

    **The scope is the server's.** `patient_queryset_for` is the security rule
    and is applied first, unchanged; the maternity filter narrows it further.
    So a midwife reaches Maternity's patients and no others however she asks —
    `?search=`, `?scope=all` or neither — and the front desk reaches exactly
    what it already could. Nothing here is filtered in the browser.
    """
    def get_permissions(self):
        return [RoleRequired(MATERNITY_DESK_ROLES)]

    def get(self, request):
        # `scope=all` drops the maternity filter for the desk that has to find
        # a woman who is not in Maternity *yet* — the first step of putting
        # her there. It widens nothing: it returns the caller's own authorised
        # patients, which for a midwife is already Maternity's list.
        patients = access.maternity_patients_for(
            request.user,
            search=request.query_params.get("search", ""),
            scope=request.query_params.get("scope", "maternity"),
        ).prefetch_related("pregnancies")
        limit = min(int(request.query_params.get("page_size") or 50), 200)
        page = list(patients[:limit])
        # Who is responsible for each of them, in one query rather than one
        # per row: a fifty-row picker must not cost fifty round trips to draw.
        rows = MaternityPatientSerializer(
            page, many=True,
            context={"assigned_nurses": access.assigned_nurses_for(page),
                     "assigned_doctors": access.assigned_doctors_for(page)}).data
        return Response({"count": len(rows), "results": rows})


class MaternityStaffView(APIView):
    """
    Who may be named as the responsible midwife — `access.maternity_staff`,
    which is the role plus the Maternity department's own staff list.

    A separate endpoint rather than `/users/?role=maternity_nurse` because the
    question is "who works this ward", and the answer has to survive an
    administrator who fills in `Department.staff` and one who never does
    (rule 16). The desk being offered somebody the server would then refuse is
    the drift this closes.
    """
    def get_permissions(self):
        return [RoleRequired(MATERNITY_DESK_ROLES)]

    def get(self, request):
        """`?for=nurse` or `?for=doctor` — the roster that column is filled
        from. Without it, both, for a screen showing the ward's staff."""
        wanted = request.query_params.get("for")
        roster = {"nurse": access.maternity_nurses,
                  "doctor": access.maternity_doctors}.get(wanted, access.maternity_staff)
        return Response(MaternityStaffSerializer(roster(), many=True).data)


class MaternityAssignmentView(APIView):
    """
    Put a mother in Maternity's care, and say which midwife is responsible.

    Two separate decisions, deliberately (section L): `POST` assigns her to
    the department and may name a nurse at the same time; `PATCH` changes only
    the nurse and **never** the department. Moving her out of Maternity is not
    here at all — that is the existing routing workflow, and a handover at
    shift change must not be a way to transfer a patient by accident.

    Neither affects who can *see* her: every authorised midwife sees every
    Maternity patient, assigned or not.
    """
    def get_permissions(self):
        if self.request.method == "GET":
            return [RoleRequired(MATERNITY_DESK_ROLES)]
        return [RoleRequired(MATERNITY_ASSIGN_ROLES)]

    def get(self, request):
        """Where she stands: her department, and who is responsible for her."""
        patient = _patient_or_404(request.query_params.get("patient"))
        return Response(self._state(patient))

    def post(self, request):
        patient = _patient_or_404(request.data.get("patient"))
        try:
            services.assign_to_maternity(
                patient=patient, actor=request.user,
                nurse=self._nurse(request), notes=request.data.get("notes", ""))
        except services.MaternityError as refusal:
            return Response({"detail": str(refusal), "code": refusal.code},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(self._state(patient), status=drf_status.HTTP_201_CREATED)

    def patch(self, request):
        """
        Change **one** responsibility. `{"nurse": …}` or `{"doctor": …}`, and
        `null` for either clears it.

        Exactly one per call, refused otherwise (`one_assignment_at_a_time`):
        they are separate decisions taken by different people at different
        moments, and a single call that moved both would make "change the
        midwife" capable of silently reassigning the doctor. Neither touches
        the department, and neither changes who can *see* her.
        """
        patient = _patient_or_404(request.data.get("patient"))
        named = [field for field in ("nurse", "doctor") if field in request.data]
        if len(named) != 1:
            return Response(
                {"detail": "Name either a nurse or a doctor — one at a time.",
                 "code": "one_assignment_at_a_time"},
                status=drf_status.HTTP_400_BAD_REQUEST)
        change = (services.assign_doctor if named[0] == "doctor"
                  else services.assign_nurse)
        try:
            change(patient=patient, actor=request.user,
                   **{named[0]: self._person(request, named[0])})
        except services.MaternityError as refusal:
            return Response({"detail": str(refusal), "code": refusal.code},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(self._state(patient))

    def _person(self, request, field="nurse"):
        named = request.data.get(field)
        if named in (None, "", "none"):
            return None
        person = User.objects.filter(pk=named, is_active=True).first()
        if person is None:
            raise NotFound("No such staff account.")
        return person

    def _nurse(self, request):
        return self._person(request, "nurse")

    def _state(self, patient):
        """
        Where she stands: the department that gives the team its access, and
        the two people answerable for her. Three fields, three decisions.
        """
        route = access.maternity_route_for(patient)
        nurse = route.assigned_to if route is not None else None
        doctor = route.visit.attending_doctor if route is not None else None
        return {
            "patient": {"id": patient.pk, "uuid": str(patient.uuid),
                        "name": patient.display_name,
                        "patient_number": patient.patient_number},
            "in_maternity": access.in_maternity(patient),
            # Where she is lying, from the ward's own record. Derived on every
            # read, so a bed transfer is reflected without maternity storing a
            # second copy of it (rule 56).
            "admission": access.admission_state(patient),
            "department": route.department.name if route is not None else None,
            "department_code": route.department.code if route is not None else None,
            "route_status": route.status if route is not None else None,
            "assigned_nurse": self._person_row(nurse),
            "assigned_doctor": self._person_row(doctor),
        }

    def _person_row(self, person):
        return ({"id": person.pk, "name": person.get_full_name() or person.username}
                if person else None)
