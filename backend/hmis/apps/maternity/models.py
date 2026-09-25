"""
The obstetric spine: a woman, the pregnancies she has had, and the visits
that make up each one.

**Patient ≠ Pregnancy ≠ Encounter.** That separation is the whole point of
this app. A woman is registered once, for life (`patients.Patient`); a
pregnancy is an episode she has one of at a time and several of over the
years; an encounter is one attendance inside one of those episodes. Returning
to the clinic creates an *encounter*, never a patient and never a pregnancy —
which is the failure this app exists to make impossible.

**Almost nothing here is new.** Maternity reuses the hospital it lives in:
`Vitals` for the observations, `Visit` and `PatientRoute` for the attendance
and the queue, `LabOrder` and the imaging referral for investigations,
`Charge` for the money, `Admission`/`Ward`/`Bed` for labour, `Appointment`
for the booking, and the existing notifications, audit and RBAC. What the
HMIS genuinely could not say before is "which pregnancy is this, and which
visit of it" — so that, and only that, is what these three models add.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core import labels
from apps.core.mixins import TimeStampedModel
from apps.patients.models import Patient


class MaternityVisitType(TimeStampedModel):
    """
    What a maternity attendance *is* — booking, follow-up, labour assessment,
    an emergency, postnatal care.

    A configured row rather than a choices list, because the hospital is what
    decides which clinics it runs, and rule 31's answer to "should an
    administrator be able to change this?" is a model with an admin behind it.
    Seeded with the set a maternity hospital starts with
    (`maternity/0002`), which never overwrites a row somebody has edited.

    **Retired, never deleted**: encounters point at it (`PROTECT`), so
    withdrawing a clinic must not take its history with it.
    """
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    # Whether this attendance starts a pregnancy's record. The booking visit
    # is where the obstetric history is taken; a follow-up is not, and forcing
    # a returning woman back through it is the thing section 7 forbids.
    is_booking = models.BooleanField(
        "first ANC / booking visit", default=False,
        help_text="The visit that opens a pregnancy's record and takes the obstetric history.")
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


#: The statuses that mean a pregnancy is still running. Module level because
#: a nested `Meta` cannot see its own class's attributes — and because the
#: constraint below and `services.active_pregnancy_for` must read one list or
#: they will disagree about what "active" means.
OPEN_STATUSES = ["active"]


class Pregnancy(TimeStampedModel):
    """
    One pregnancy of one woman — an episode with a beginning, a course and an
    end, kept whole.

    **Numbered per patient, never reused.** `number` is what staff say out
    loud ("pregnancy #2"), and `reference` is `PRG-000123`, derived off the
    primary key the way `DCH-`, `ADM-` and `TRF-` already are rather than
    stored twice.

    **One active pregnancy at a time**, held by the database (below) and not
    only by a check somebody could race past — the same reasoning rule 55
    applies to an open appointment. Starting pregnancy #2 writes a new row and
    touches pregnancy #1 in no way at all: that is what makes the history
    readable years later.

    It carries no demographics. Her name, number, age and contact live on
    `Patient` and are read from there, so a correction is made once.
    """
    STATUS = [
        ("active", "Active"),
        ("completed", "Completed"),
        ("ended", "Ended"),
    ]
    #: How a pregnancy finished. Deliberately blank on an active one — an
    #: outcome nobody has recorded is not "unknown", it is not yet.
    OUTCOME = [
        ("delivered", "Delivered"),
        ("miscarriage", "Miscarriage"),
        ("stillbirth", "Stillbirth"),
        ("termination", "Termination"),
        ("transferred", "Transferred out"),
        ("lost_to_follow_up", "Lost to follow-up"),
    ]
    #: Re-exposed on the model so callers read `Pregnancy.OPEN_STATUSES`
    #: rather than importing the module constant separately.
    OPEN_STATUSES = OPEN_STATUSES

    # CASCADE, like vitals and notes: a pregnancy only ever described this
    # person, so it is part of what rule 37 lets a deletion take. It holds no
    # money and nothing else points at it from outside maternity.
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="pregnancies")
    # Her second pregnancy *at this hospital*, which is what the record can
    # honestly count. `gravida` below is what she reports, and the two are
    # different numbers on purpose.
    number = models.PositiveIntegerField(default=1)

    lmp = models.DateField("last menstrual period", null=True, blank=True)
    edd = models.DateField("estimated delivery date", null=True, blank=True)
    # What she reports of her obstetric history. Nullable because a woman
    # booking late may not know, and a required field would be answered with a
    # guess (rule 20's reasoning: a blank is not a result).
    gravida = models.PositiveSmallIntegerField(null=True, blank=True,
                                               help_text="Total pregnancies including this one.")
    para = models.PositiveSmallIntegerField(null=True, blank=True,
                                            help_text="Births after 28 weeks.")

    status = models.CharField(max_length=20, choices=STATUS, default="active")
    outcome = models.CharField(max_length=20, choices=OUTCOME, blank=True)
    ended_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="pregnancies_opened")

    class Meta:
        ordering = ["-number", "-created_at"]
        verbose_name_plural = "pregnancies"
        constraints = [
            # One open episode per woman. Two concurrent "start pregnancy"
            # presses would otherwise both pass the service's check and leave
            # her with two active pregnancies and no way to say which the ANC
            # visit belongs to — rule 55's constraint, applied to the episode.
            models.UniqueConstraint(
                fields=["patient"],
                condition=models.Q(status__in=OPEN_STATUSES),
                name="one_active_pregnancy_per_patient",
            ),
        ]

    def __str__(self):
        """Whose pregnancy, which one, and where it has got to."""
        return f"{self.patient} · pregnancy #{self.number} ({self.get_status_display()})"

    @property
    def reference(self):
        return f"PRG-{self.pk:06d}" if self.pk else ""

    @property
    def is_active(self):
        return self.status in self.OPEN_STATUSES

    def gestation_on(self, day=None):
        """
        How far along she was on a given day, as `(weeks, days)` — or None
        where the dates cannot say.

        Derived from the LMP every time rather than stored on each visit: if a
        dating scan corrects the LMP, every gestation this hospital has ever
        printed for this pregnancy corrects with it, which is what a clinician
        expects. Falls back to counting back from the EDD when only that is
        known (the usual 280 days), and answers None rather than guessing when
        neither is recorded.
        """
        import datetime

        day = day or datetime.date.today()
        start = self.lmp
        if start is None and self.edd is not None:
            start = self.edd - datetime.timedelta(days=280)
        if start is None:
            return None
        elapsed = (day - start).days
        if elapsed < 0:
            return None
        return divmod(elapsed, 7)


class MaternityEncounter(TimeStampedModel):
    """
    One attendance inside one pregnancy — today's ANC follow-up, the labour
    assessment she walked in with, the postnatal check six weeks later.

    **A visit is a row, not an edit.** The pregnancy is not one record that
    gets overwritten every time she comes: each attendance is its own row with
    its own date, type, provider and findings, so ANC 1 still says what was
    found at ANC 1 after ANC 4 has happened. Nothing here updates an earlier
    encounter, and the API offers no way to (section 4 and 13).

    **It carries almost nothing clinical**, deliberately. The observations are
    `clinical.Vitals` taken at the existing triage station, the assessment is
    the existing consultation note, the tests are the existing `LabOrder` and
    imaging referral, and the money is the existing `Charge` — all of them
    reached through `visit`, the ordinary `workflow.Visit` this encounter
    belongs to. What this row adds is the two facts none of them could hold:
    *which pregnancy* and *which kind of attendance*.
    """
    STATUS = [
        ("in_progress", "In progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    pregnancy = models.ForeignKey(Pregnancy, on_delete=models.CASCADE, related_name="encounters")
    # PROTECT: retiring a clinic must not delete the attendances filed under
    # it. `ProtectedConfigMixin` turns that into a 409 that says so.
    visit_type = models.ForeignKey(MaternityVisitType, on_delete=models.PROTECT,
                                   related_name="encounters")
    # The hospital's own attendance record, where one was opened — this is
    # what carries the triage, the notes, the orders and the bill. SET_NULL
    # rather than required, because a maternity encounter recorded before a
    # visit is raised is still a real encounter.
    visit = models.ForeignKey("workflow.Visit", null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="maternity_encounters")
    provider = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="maternity_encounters")
    status = models.CharField(max_length=20, choices=STATUS, default="in_progress")
    # The clinician's own summary of the attendance. The full assessment is
    # the consultation note on the visit; this is the line the timeline shows.
    summary = models.TextField(blank=True)
    seen_on = models.DateField(auto_now_add=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="maternity_encounters_recorded")

    class Meta:
        ordering = ["-seen_on", "-created_at"]

    def __str__(self):
        """Which attendance, of whose pregnancy, on what day."""
        return (f"{self.pregnancy.patient} · {self.visit_type.name} "
                f"{labels.on(self.seen_on)}")

    @property
    def gestation(self):
        """How far along she was on the day of this attendance."""
        return self.pregnancy.gestation_on(self.seen_on)


class MaternityOption(TimeStampedModel):
    """
    The hospital's own vocabulary for maternity: delivery types, outcomes,
    complications, the states a newborn can be in, what a mother is advised.

    **One lookup table rather than five near-identical ones.** Every list the
    maternity screens offer has the same four columns — a code, a label, an
    order and whether it is still offered — and five models with those columns
    would be five admins, five config entries and five migrations saying one
    thing. `kind` is what separates them, and `KINDS` is the only place a new
    list is declared.

    This is deliberately **not** the catch-all the encounter must never become:
    it holds no clinical record and no relationship to a patient. It is a
    vocabulary, and a delivery points at one of its rows the way a product
    points at a category (rule 41) — which is what makes "Assisted vaginal
    delivery" renameable once, everywhere, without a deployment.

    **Retired, never deleted**: deliveries and newborns `PROTECT` the rows they
    name, so withdrawing an option from the list keeps every record that used
    it readable.
    """
    DELIVERY_TYPE = "delivery_type"
    DELIVERY_OUTCOME = "delivery_outcome"
    COMPLICATION = "complication"
    NEWBORN_STATUS = "newborn_status"
    MOTHER_CONDITION = "mother_condition"
    FAMILY_PLANNING = "family_planning"

    KINDS = [
        (DELIVERY_TYPE, "Delivery type"),
        (DELIVERY_OUTCOME, "Delivery outcome"),
        (COMPLICATION, "Complication"),
        (NEWBORN_STATUS, "Newborn status"),
        (MOTHER_CONDITION, "Mother's condition"),
        (FAMILY_PLANNING, "Family planning method"),
    ]

    kind = models.CharField(max_length=30, choices=KINDS)
    code = models.SlugField()
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=200, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "display_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "code"], name="unique_maternity_option"),
        ]

    def __str__(self):
        """The label and which list it belongs to — a dropdown of every kind
        at once would otherwise read as one undifferentiated pile."""
        return f"{self.name} ({self.get_kind_display()})"


class LabourEpisode(TimeStampedModel):
    """
    One labour, belonging to one pregnancy.

    **It is an episode, not an observation.** What it holds is the shape of
    the labour — when it started, how it is being managed, where she is, how
    it ended — and the readings taken along the way are `LabourObservation`
    rows beneath it. A single `cervical_dilation` column on this model would
    be overwritten at every check and the partogram would be gone by morning,
    which is exactly the "one constantly overwritten record" the maternity
    brief forbids.

    **The ward and the bed are not here.** She is admitted through the
    hospital's existing `Admission`, which already names the bed, and the bed
    already names the ward — so `admission` is the one link and `ward` / `bed`
    are read through it. Two copies of where she is lying are two answers free
    to disagree.
    """
    STATUS = [
        ("in_progress", "In progress"),
        ("delivered", "Delivered"),
        ("transferred", "Transferred out"),
        ("cancelled", "Cancelled"),
    ]
    #: The stage she is in. A fixed list rather than a configured one: the
    #: stages of labour are obstetric fact, not this hospital's preference.
    STAGE = [
        ("latent", "Latent first stage"),
        ("active", "Active first stage"),
        ("second", "Second stage"),
        ("third", "Third stage"),
        ("fourth", "Fourth stage (immediate recovery)"),
    ]
    ONSET = [
        ("spontaneous", "Spontaneous"),
        ("induced", "Induced"),
        ("elective_caesarean", "Elective caesarean"),
        ("emergency", "Emergency"),
    ]

    pregnancy = models.ForeignKey(Pregnancy, on_delete=models.CASCADE,
                                  related_name="labour_episodes")
    # The hospital's own admission, where she was admitted. SET_NULL and
    # optional: a precipitate delivery in the corridor is still a labour, and
    # a labour assessment that goes home again never had one.
    admission = models.ForeignKey("inpatient.Admission", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="labour_episodes")
    onset = models.CharField(max_length=30, choices=ONSET, default="spontaneous")
    status = models.CharField(max_length=20, choices=STATUS, default="in_progress")
    stage = models.CharField(max_length=20, choices=STAGE, default="latent")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="labour_episodes_opened")

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            # One labour running at a time in one pregnancy. She cannot be in
            # two labours at once, and two rows would leave the delivery with
            # no single episode to belong to.
            models.UniqueConstraint(
                fields=["pregnancy"],
                condition=models.Q(status="in_progress"),
                name="one_labour_in_progress_per_pregnancy",
            ),
        ]

    def __str__(self):
        return (f"{self.pregnancy.patient} · labour {labels.on(self.started_at)} "
                f"({self.get_status_display()})")

    @property
    def is_open(self):
        return self.status == "in_progress"

    @property
    def bed(self):
        """Where she is lying — read through the admission, never stored twice."""
        return self.admission.bed if self.admission_id else None

    @property
    def ward(self):
        return self.admission.bed.ward if self.admission_id else None


class LabourObservation(TimeStampedModel):
    """
    One check during labour — the partogram, a row at a time.

    Every reading is its own row with its own time and its own author, so the
    4 a.m. check still says what it said after the 6 a.m. one. Nothing updates
    an earlier observation and the API offers no way to: this is rule 2's
    idiom (a correction is a new record) applied to labour monitoring.

    Nothing mandatory (rule 20): a midwife records what she measured, and a
    blank column means "not taken", never zero.
    """
    MEMBRANES = [
        ("intact", "Intact"),
        ("ruptured_clear", "Ruptured — clear"),
        ("ruptured_meconium", "Ruptured — meconium"),
        ("ruptured_blood", "Ruptured — blood stained"),
    ]

    labour = models.ForeignKey(LabourEpisode, on_delete=models.CASCADE,
                               related_name="observations")
    observed_at = models.DateTimeField()
    cervical_dilation_cm = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="0–10 cm.")
    contractions_per_10min = models.PositiveSmallIntegerField(null=True, blank=True)
    contraction_duration_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    fetal_heart_rate = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Beats per minute.")
    membranes = models.CharField(max_length=30, choices=MEMBRANES, blank=True)
    # Her own condition in the midwife's words. The figures — blood pressure,
    # pulse, temperature — are `clinical.Vitals` taken at the existing triage
    # station, which is why there is no second set of them here.
    maternal_condition = models.CharField(max_length=200, blank=True)
    vitals = models.ForeignKey("clinical.Vitals", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="labour_observations")
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="labour_observations")

    class Meta:
        ordering = ["observed_at", "pk"]

    def __str__(self):
        return f"{self.labour.pregnancy.patient} · labour check {labels.on(self.observed_at)}"


class Delivery(TimeStampedModel):
    """
    The delivery this labour ended in — one per labour episode.

    `OneToOneField`, because a labour produces one delivery: **twins are two
    `Newborn` rows against this one delivery**, never two deliveries and never
    a second pregnancy. That is the single most important thing this model
    says, and the database says it rather than a convention.

    The pregnancy is reached through the labour, so it is not stored again.
    """
    labour = models.OneToOneField(LabourEpisode, on_delete=models.CASCADE,
                                  related_name="delivery")
    delivered_at = models.DateTimeField()
    # PROTECT: the vocabulary a record used must stay readable (rule 41).
    delivery_type = models.ForeignKey(MaternityOption, on_delete=models.PROTECT,
                                      related_name="deliveries_of_type",
                                      limit_choices_to={"kind": MaternityOption.DELIVERY_TYPE})
    outcome = models.ForeignKey(MaternityOption, null=True, blank=True,
                                on_delete=models.PROTECT, related_name="deliveries_with_outcome",
                                limit_choices_to={"kind": MaternityOption.DELIVERY_OUTCOME})
    complications = models.ManyToManyField(
        MaternityOption, blank=True, related_name="deliveries_with_complication",
        limit_choices_to={"kind": MaternityOption.COMPLICATION})
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="deliveries_attended")
    midwife = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="deliveries_as_midwife")
    estimated_blood_loss_ml = models.PositiveIntegerField(null=True, blank=True)
    placenta_complete = models.BooleanField(null=True, blank=True)
    placenta_notes = models.CharField(max_length=200, blank=True)
    maternal_condition = models.ForeignKey(
        MaternityOption, null=True, blank=True, on_delete=models.PROTECT,
        related_name="deliveries_with_maternal_condition",
        limit_choices_to={"kind": MaternityOption.MOTHER_CONDITION})
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="deliveries_recorded")

    class Meta:
        ordering = ["-delivered_at"]
        verbose_name_plural = "deliveries"

    def __str__(self):
        babies = self.newborns.count()
        return (f"{self.pregnancy.patient} · {self.delivery_type.name} "
                f"{labels.on(self.delivered_at)} ({babies} baby/babies)")

    @property
    def reference(self):
        return f"DEL-{self.pk:06d}" if self.pk else ""

    @property
    def pregnancy(self):
        return self.labour.pregnancy

    @property
    def patient(self):
        return self.labour.pregnancy.patient


class Newborn(TimeStampedModel):
    """
    A baby. Several of these against one `Delivery` is how twins are recorded.

    `birth_order` is what tells Baby A from Baby B — required, and unique
    within the delivery, so a set of twins cannot silently become one row
    saved twice.

    The baby is **not** a `Patient` here. Registering the newborn as a patient
    in their own right is the front desk's job through the existing
    registration, and `patient` is the nullable link for once that has
    happened — so a birth record never invents a second patient row, and a
    baby who is registered is joined to the birth rather than copied from it.
    """
    SEX = [("M", "Male"), ("F", "Female"), ("A", "Ambiguous")]

    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="newborns")
    birth_order = models.PositiveSmallIntegerField(default=1)
    name = models.CharField(max_length=120, blank=True,
                            help_text="Often not chosen for days. Left blank rather than guessed.")
    sex = models.CharField(max_length=1, choices=SEX)
    birth_weight_grams = models.PositiveIntegerField(null=True, blank=True)
    apgar_1_min = models.PositiveSmallIntegerField(null=True, blank=True)
    apgar_5_min = models.PositiveSmallIntegerField(null=True, blank=True)
    apgar_10_min = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.ForeignKey(MaternityOption, null=True, blank=True, on_delete=models.PROTECT,
                               related_name="newborns_with_status",
                               limit_choices_to={"kind": MaternityOption.NEWBORN_STATUS})
    congenital_abnormalities = models.TextField(blank=True)
    # Whether the baby went to the nursery or stayed with the mother. Where a
    # baby is admitted in their own right that is the hospital's existing
    # `Admission` against the baby's own patient record.
    admitted_to_nursery = models.BooleanField(default=False)
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="birth_records",
                                help_text="Set once the baby is registered as a patient.")
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="newborns_recorded")

    class Meta:
        ordering = ["delivery", "birth_order"]
        constraints = [
            models.UniqueConstraint(fields=["delivery", "birth_order"],
                                    name="unique_birth_order_per_delivery"),
        ]

    def __str__(self):
        called = self.name or f"Baby {self.birth_order}"
        return f"{self.delivery.patient} · {called} ({self.get_sex_display()})"

    @property
    def reference(self):
        return f"NB-{self.pk:06d}" if self.pk else ""


class PostpartumVisit(TimeStampedModel):
    """
    A check on mother and baby after the delivery — immediate, day three, six
    weeks. **Several rows per delivery**, because postpartum care is a course
    and not a moment, and the same rule holds as everywhere else here: a later
    check never overwrites an earlier one.

    Her blood pressure is not a column. It is `clinical.Vitals`, taken at the
    existing triage station and linked, so the hospital has one set of
    observations for this woman rather than a maternity copy of them.
    """
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE,
                                 related_name="postpartum_visits")
    seen_at = models.DateTimeField()
    mother_condition = models.ForeignKey(
        MaternityOption, null=True, blank=True, on_delete=models.PROTECT,
        related_name="postpartum_with_condition",
        limit_choices_to={"kind": MaternityOption.MOTHER_CONDITION})
    bleeding = models.CharField(max_length=200, blank=True)
    wound_condition = models.CharField(max_length=200, blank=True)
    breastfeeding_established = models.BooleanField(null=True, blank=True)
    baby_feeding = models.CharField(max_length=200, blank=True)
    vitals = models.ForeignKey("clinical.Vitals", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="postpartum_visits")
    family_planning = models.ForeignKey(
        MaternityOption, null=True, blank=True, on_delete=models.PROTECT,
        related_name="postpartum_with_family_planning",
        limit_choices_to={"kind": MaternityOption.FAMILY_PLANNING})
    discharge_advice = models.TextField(blank=True)
    follow_up_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="postpartum_visits_recorded")

    class Meta:
        ordering = ["-seen_at"]

    def __str__(self):
        return f"{self.delivery.patient} · postpartum {labels.on(self.seen_at)}"


class MaternityAmendment(TimeStampedModel):
    """
    A correction to a maternity record, and what it corrected.

    **The consultation note's amendment trail, over maternity's records**
    (rule 45): the snapshot is written *before* the record changes, in the same
    transaction, so a refused save can never leave a trail describing a
    correction that did not happen. What a screen shows — original, amended,
    who, when, why — is this row read against the record.

    **One trail, not five.** Pregnancy, encounter, labour and delivery all
    point at it through the content types Django already has, the way
    `core.AuditLog` does. Five per-record amendment models would be five
    migrations, five admins and five spellings of one rule.

    `previous` is JSON because nothing filters, groups or joins on what a
    record used to say — the same reason `PatientRoute.result_data` is
    (rule 53). It is read back through `amendments.changes_for`.

    Nothing here is `clinical.Vitals`. A reading is locked on save and a
    correction is a *new reading* (rules 2 and 11); there is deliberately no
    amendment path for one, and this model does not give it one.
    """
    #: Why a maternity record may be corrected. The consultation note's list,
    #: because a mistyped date is a mistyped date on either record.
    REASONS = [
        ("typo", "Typing or data-entry error"),
        ("wrong_value", "Wrong value recorded"),
        ("incomplete", "Record was incomplete"),
        ("clarification", "Clarifying what was recorded"),
        ("other", "Other (explained below)"),
    ]

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    record = GenericForeignKey("content_type", "object_id")

    amended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="maternity_amendments")
    reason = models.CharField(max_length=30, choices=REASONS, blank=True)
    detail = models.TextField(blank=True)
    #: What the record said before this correction — the amendable fields only.
    previous = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        """Which record was corrected, and when — `__str__` is what Django
        admin labels an inline and a log entry with (rule: every model names
        itself)."""
        return f"{self.content_type.model} #{self.object_id} · amended {labels.on(self.created_at)}"

    @property
    def amended_by_name(self):
        if self.amended_by is None:
            return None
        return self.amended_by.get_full_name() or self.amended_by.username
