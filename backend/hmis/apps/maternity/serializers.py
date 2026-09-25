from rest_framework import serializers

from apps.patients.models import Patient

from .models import (Delivery, LabourEpisode, LabourObservation, MaternityAmendment,
                     MaternityEncounter, MaternityOption, MaternityVisitType, Newborn,
                     PostpartumVisit, Pregnancy)


def gestation_text(weeks_days):
    """"32w 4d", or an empty string where the dates cannot say."""
    if not weeks_days:
        return ""
    weeks, days = weeks_days
    return f"{weeks}w {days}d"


class MaternityAmendmentSerializer(serializers.ModelSerializer):
    """
    One correction, as the record's history shows it: what it said before,
    what changed, who changed it, when, and why.

    `changes` is **derived** against the record as it stands now (rule 45), so
    there is no stored diff to disagree with the snapshot.
    """
    amended_by_name = serializers.CharField(read_only=True)
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)
    changes = serializers.SerializerMethodField()

    class Meta:
        model = MaternityAmendment
        fields = ["id", "created_at", "amended_by", "amended_by_name",
                  "reason", "reason_label", "detail", "previous", "changes"]
        read_only_fields = fields

    def get_changes(self, obj):
        from . import amendments

        record = obj.record
        return amendments.changes_for(obj, record) if record is not None else []


class AmendableRecordSerializer(serializers.ModelSerializer):
    """
    A maternity clinical record that can be corrected, but never quietly.

    Two rules, both server-side, because a field hidden in React is not a
    field that cannot be written (rule 28: the frontend guard is a courtesy,
    the API is the control).

    1. **An identity fact is refused outright** — `patient`, `pregnancy`,
       `labour`, `visit`, who recorded it, when it was filed. Changing one
       does not correct a record, it turns it into a different record. Refused
       with `code: "immutable_field"` naming the fields, and **nothing else is
       written**: a PATCH carrying one good field and one protected one does
       not half-succeed.
    2. **Everything else needs a reason.** A correction without one is refused
       with `code: "amendment_reason_required"`, carrying the choices, exactly
       as a released laboratory result and a consultation note already do
       (rules 22 and 45).

    The snapshot itself is written by the viewset, before `save()`, inside the
    same transaction.

    It also carries **whether this record has been corrected and whether this
    reader may correct it** — `is_amended` and `can_amend`, read from the same
    `amendments.may_amend` the viewset gates on. The consultation note does
    exactly this (rule 45) and for the same reason: a screen that worked the
    answer out for itself would be a second copy of the rule, and would offer
    a button the API then refuses.
    """
    is_amended = serializers.SerializerMethodField()
    amendment_count = serializers.SerializerMethodField()
    last_amended_at = serializers.SerializerMethodField()
    last_amended_by_name = serializers.SerializerMethodField()
    can_amend = serializers.SerializerMethodField()
    amendable_fields = serializers.SerializerMethodField()

    def _trail(self, obj):
        """This record's corrections, newest first — cached per instance so a
        list of twenty records is not twenty queries times four fields."""
        cached = getattr(obj, "_amendment_trail", None)
        if cached is None:
            from django.contrib.contenttypes.models import ContentType

            cached = list(MaternityAmendment.objects.filter(
                content_type=ContentType.objects.get_for_model(type(obj)),
                object_id=obj.pk).select_related("amended_by"))
            obj._amendment_trail = cached
        return cached

    def get_is_amended(self, obj):
        return bool(self._trail(obj))

    def get_amendment_count(self, obj):
        return len(self._trail(obj))

    def get_last_amended_at(self, obj):
        trail = self._trail(obj)
        return trail[0].created_at if trail else None

    def get_last_amended_by_name(self, obj):
        trail = self._trail(obj)
        return trail[0].amended_by_name if trail else None

    def get_can_amend(self, obj):
        from . import amendments

        user = getattr(self.context.get("request"), "user", None)
        return amendments.may_amend(user, obj)

    def get_amendable_fields(self, obj):
        """
        What a correction may touch on *this* record, with its label — so the
        form offers exactly what the server accepts and the identity facts are
        never rendered as editable boxes.
        """
        from . import amendments

        return [{"name": name, "label": amendments.label_for(name)}
                for name in sorted(amendments.amendable_fields(obj))]

    def validate(self, attrs):
        from . import amendments

        attrs = super().validate(attrs)
        if self.instance is None:
            return attrs

        changing = amendments.blocked_changes(self.instance, attrs)
        if changing:
            raise serializers.ValidationError({
                "detail": ("These are part of what identifies this record and cannot be "
                           "changed: " + ", ".join(changing) + "."),
                "code": amendments.IMMUTABLE_FIELD,
                "fields": changing,
            })
        return attrs


class MaternityVisitTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaternityVisitType
        fields = "__all__"


class MaternityEncounterSerializer(AmendableRecordSerializer):
    visit_type_name = serializers.CharField(source="visit_type.name", read_only=True)
    visit_type_code = serializers.CharField(source="visit_type.code", read_only=True)
    provider_name = serializers.SerializerMethodField()
    gestation = serializers.SerializerMethodField()
    pregnancy_number = serializers.IntegerField(source="pregnancy.number", read_only=True)

    class Meta:
        model = MaternityEncounter
        fields = "__all__"
        # An attendance is a record of a day. `pregnancy`, `visit_type` and
        # `visit` are decided when it is opened and never re-pointed
        # afterwards — moving a visit to another pregnancy would rewrite two
        # histories at once — and `recorded_by` / `seen_on` are stamped.
        read_only_fields = ["pregnancy", "visit_type", "visit", "recorded_by", "seen_on"]

    def get_provider_name(self, obj):
        person = obj.provider
        return (person.get_full_name() or person.username) if person else None

    def get_gestation(self, obj):
        return gestation_text(obj.gestation)


class PregnancySerializer(AmendableRecordSerializer):
    reference = serializers.CharField(read_only=True)
    patient_name = serializers.CharField(source="patient.display_name", read_only=True)
    patient_number = serializers.CharField(source="patient.patient_number", read_only=True)
    patient_uuid = serializers.UUIDField(source="patient.uuid", read_only=True)
    gestation = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    encounter_count = serializers.SerializerMethodField()
    last_encounter = serializers.SerializerMethodField()

    class Meta:
        model = Pregnancy
        fields = "__all__"
        # `number` is issued by the service (the next one she has not had),
        # and the status moves through `close/` so an outcome is always
        # recorded with it. A client that could write either could fork the
        # history or end an episode without saying how.
        read_only_fields = ["number", "status", "outcome", "ended_on", "opened_by"]

    #: What the front desk does not read. It is on this list because it has to
    #: know she is pregnant, which of Continue / Start applies and roughly how
    #: far along she is — not because it reads her record. `notes` is a
    #: clinician writing to a clinician and `gravida`/`para` are her obstetric
    #: history, so they are dropped for anyone outside MATERNITY_ROLES. The
    #: clinical endpoints refuse reception outright; this is the same boundary
    #: on the one payload the desk legitimately reads.
    CLINICAL_FIELDS = ["notes", "gravida", "para"]

    def to_representation(self, instance):
        from apps.accounts.permissions import MATERNITY_ROLES

        data = super().to_representation(instance)
        user = getattr(self.context.get("request"), "user", None)
        role = getattr(user, "role", None)
        if role in MATERNITY_ROLES or getattr(user, "is_admin", False):
            return data
        for field in self.CLINICAL_FIELDS:
            data.pop(field, None)
        return data

    def get_gestation(self, obj):
        return gestation_text(obj.gestation_on())

    def get_encounter_count(self, obj):
        return obj.encounters.count()

    def get_last_encounter(self, obj):
        last = obj.encounters.first()   # newest first, by Meta.ordering
        if last is None:
            return None
        return {
            "id": last.pk,
            "type": last.visit_type.name,
            "seen_on": last.seen_on,
            "status": last.status,
            "gestation": gestation_text(last.gestation),
        }


class PregnancyTimelineSerializer(PregnancySerializer):
    """One pregnancy with every visit in it — the workspace's timeline."""
    encounters = MaternityEncounterSerializer(many=True, read_only=True)


class MaternityOptionSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = MaternityOption
        fields = "__all__"


class LabourObservationSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()
    membranes_label = serializers.CharField(source="get_membranes_display", read_only=True)

    class Meta:
        model = LabourObservation
        fields = "__all__"
        # An observation is a reading taken at a time. It is never re-pointed
        # at another labour and never re-stamped with another author.
        read_only_fields = ["labour", "recorded_by"]

    def get_recorded_by_name(self, obj):
        person = obj.recorded_by
        return (person.get_full_name() or person.username) if person else None


class NewbornSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    sex_label = serializers.CharField(source="get_sex_display", read_only=True)
    status_name = serializers.CharField(source="status.name", read_only=True)
    mother_name = serializers.SerializerMethodField()

    class Meta:
        model = Newborn
        fields = "__all__"
        read_only_fields = ["delivery", "recorded_by"]

    def get_mother_name(self, obj):
        return obj.delivery.patient.display_name


class PostpartumVisitSerializer(serializers.ModelSerializer):
    mother_condition_name = serializers.CharField(source="mother_condition.name", read_only=True)
    family_planning_name = serializers.CharField(source="family_planning.name", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PostpartumVisit
        fields = "__all__"
        read_only_fields = ["delivery", "recorded_by"]

    def get_recorded_by_name(self, obj):
        person = obj.recorded_by
        return (person.get_full_name() or person.username) if person else None


class DeliverySerializer(AmendableRecordSerializer):
    reference = serializers.CharField(read_only=True)
    delivery_type_name = serializers.CharField(source="delivery_type.name", read_only=True)
    outcome_name = serializers.CharField(source="outcome.name", read_only=True)
    complication_names = serializers.SerializerMethodField()
    doctor_name = serializers.SerializerMethodField()
    midwife_name = serializers.SerializerMethodField()
    newborns = NewbornSerializer(many=True, read_only=True)
    postpartum_visits = PostpartumVisitSerializer(many=True, read_only=True)
    patient_name = serializers.SerializerMethodField()
    patient_number = serializers.SerializerMethodField()
    pregnancy_id = serializers.SerializerMethodField()

    class Meta:
        model = Delivery
        fields = "__all__"
        # The delivery belongs to the labour it ended. Re-pointing it would
        # move a birth between two pregnancies, so it is decided once.
        read_only_fields = ["labour", "recorded_by"]

    def get_complication_names(self, obj):
        return [option.name for option in obj.complications.all()]

    def _name(self, person):
        return (person.get_full_name() or person.username) if person else None

    def get_doctor_name(self, obj):
        return self._name(obj.doctor)

    def get_midwife_name(self, obj):
        return self._name(obj.midwife)

    def get_patient_name(self, obj):
        return obj.patient.display_name

    def get_patient_number(self, obj):
        return obj.patient.patient_number

    def get_pregnancy_id(self, obj):
        return obj.labour.pregnancy_id


class LabourEpisodeSerializer(AmendableRecordSerializer):
    patient_name = serializers.SerializerMethodField()
    patient_number = serializers.SerializerMethodField()
    pregnancy_number = serializers.IntegerField(source="pregnancy.number", read_only=True)
    onset_label = serializers.CharField(source="get_onset_display", read_only=True)
    stage_label = serializers.CharField(source="get_stage_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # Where she is lying, read through the admission — never stored twice.
    ward_name = serializers.SerializerMethodField()
    bed_number = serializers.SerializerMethodField()
    gestation = serializers.SerializerMethodField()
    observation_count = serializers.SerializerMethodField()
    has_delivery = serializers.SerializerMethodField()

    class Meta:
        model = LabourEpisode
        fields = "__all__"
        # `status`, `stage` and `ended_at` move through the services, which is
        # what keeps a delivery and its labour's closure in one transaction.
        read_only_fields = ["pregnancy", "status", "stage", "ended_at", "opened_by"]

    def get_patient_name(self, obj):
        return obj.pregnancy.patient.display_name

    def get_patient_number(self, obj):
        return obj.pregnancy.patient.patient_number

    def get_ward_name(self, obj):
        return obj.ward.name if obj.ward else None

    def get_bed_number(self, obj):
        return obj.bed.number if obj.bed else None

    def get_gestation(self, obj):
        return gestation_text(obj.pregnancy.gestation_on(obj.started_at.date()))

    def get_observation_count(self, obj):
        return obj.observations.count()

    def get_has_delivery(self, obj):
        return hasattr(obj, "delivery")


class LabourDetailSerializer(LabourEpisodeSerializer):
    """One labour with its whole partogram and the delivery it ended in."""
    observations = LabourObservationSerializer(many=True, read_only=True)
    delivery = DeliverySerializer(read_only=True)


class MaternityStaffSerializer(serializers.Serializer):
    """
    A midwife, as the assignee dropdown needs her: enough to tell two people
    apart and nothing else. Deliberately not `UserSerializer` — the desk is
    picking a name, not reading a personnel record.
    """
    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    role = serializers.CharField(read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    staff_number = serializers.CharField(read_only=True)

    def get_name(self, obj):
        return obj.get_full_name() or obj.username


class MaternityPatientSerializer(serializers.ModelSerializer):
    """
    A mother as the maternity picker shows her: who she is, where she is in
    this pregnancy, and who is responsible for her.

    **Identity and one line of obstetric context, never a chart.** The
    pregnancy number and gestation are what tell two mothers apart on a ward
    list; findings, labour, delivery and the babies are on the endpoints that
    `MATERNITY_ROLES` gates, and reception — who is on this list because she
    has to find a returning mother — reaches none of them.
    """
    name = serializers.CharField(source="display_name", read_only=True)
    # Both spellings of the identity, so the picker can label a row and route
    # to a chart without a second lookup (rule 32).
    patient_number = serializers.CharField(read_only=True)
    file_number = serializers.CharField(read_only=True)
    uuid = serializers.UUIDField(read_only=True)
    age = serializers.CharField(source="age_display", read_only=True)
    pregnancy_number = serializers.SerializerMethodField()
    pregnancy_status = serializers.SerializerMethodField()
    gestation = serializers.SerializerMethodField()
    # Who is answerable for her — **shown, never used to decide who sees
    # her**. The team is the department (`access.in_maternity_team`), so these
    # two are labels on a row that every authorised colleague already has.
    assigned_nurse = serializers.SerializerMethodField()
    assigned_nurse_id = serializers.SerializerMethodField()
    assigned_doctor = serializers.SerializerMethodField()
    assigned_doctor_id = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = ["id", "uuid", "name", "first_name", "last_name", "patient_number",
                  "file_number", "phone_number", "age", "pregnancy_number",
                  "pregnancy_status", "gestation", "assigned_nurse", "assigned_nurse_id",
                  "assigned_doctor", "assigned_doctor_id"]

    def _pregnancy(self, obj):
        """The active one, else the most recent — one query, cached per row."""
        cached = getattr(obj, "_picker_pregnancy", "missing")
        if cached == "missing":
            episodes = list(obj.pregnancies.all()[:1]) or []
            active = [p for p in obj.pregnancies.all() if p.is_active]
            cached = active[0] if active else (episodes[0] if episodes else None)
            obj._picker_pregnancy = cached
        return cached

    def get_pregnancy_number(self, obj):
        pregnancy = self._pregnancy(obj)
        return pregnancy.number if pregnancy else None

    def get_pregnancy_status(self, obj):
        pregnancy = self._pregnancy(obj)
        return pregnancy.status if pregnancy else None

    def get_gestation(self, obj):
        pregnancy = self._pregnancy(obj)
        return gestation_text(pregnancy.gestation_on()) if pregnancy else None

    def _nurse(self, obj):
        """
        The responsible midwife, off the map the view built in one query —
        falling back to the single lookup for a caller rendering one row, so
        there is one answer and not two.
        """
        from .access import assigned_nurse_for

        nurses = self.context.get("assigned_nurses")
        if nurses is not None:
            return nurses.get(obj.pk)
        return assigned_nurse_for(obj)

    def get_assigned_nurse(self, obj):
        nurse = self._nurse(obj)
        return (nurse.get_full_name() or nurse.username) if nurse else None

    def get_assigned_nurse_id(self, obj):
        nurse = self._nurse(obj)
        return nurse.pk if nurse else None

    def _doctor(self, obj):
        from .access import assigned_doctors_for

        doctors = self.context.get("assigned_doctors")
        if doctors is not None:
            return doctors.get(obj.pk)
        return assigned_doctors_for([obj]).get(obj.pk)

    def get_assigned_doctor(self, obj):
        doctor = self._doctor(obj)
        return (doctor.get_full_name() or doctor.username) if doctor else None

    def get_assigned_doctor_id(self, obj):
        doctor = self._doctor(obj)
        return doctor.pk if doctor else None
