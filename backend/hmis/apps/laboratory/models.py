"""
The laboratory: a catalogue the hospital configures, and the results entered
against it.

Two ideas hold this module together.

**The catalogue is data, not code.** A `LabTest` and its `LabParameter` rows
say what a test is called, what it is measured in and what counts as normal.
Adding "Serum Magnesium" is a row the lab adds from the Laboratory Catalogue
page, not a deployment. Nothing in the React components may hard-code a
parameter list.

**A result is sparse.** `LabResultValue` rows exist only for parameters the
scientist actually filled in. A bench that ran three of the fourteen FBC
indices stores three rows, submits without complaint, and the report prints
three lines. There is no "blank result" state to interpret later, and no
parameter is required unless the hospital deliberately marks it so —
`LabParameter.is_required` defaults to False and the seeded catalogue never
sets it True.
"""
from django.conf import settings
from django.db import models

from apps.core.mixins import TimeStampedModel
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


# How a result is written down. The type decides what the entry form shows
# and how the value is checked — never what the value means clinically.
RESULT_TYPES = [
    ("numeric", "Numeric"),
    ("text", "Text"),
    ("positive_negative", "Positive / Negative"),
    ("reactive_nonreactive", "Reactive / Non-reactive"),
    ("detected_notdetected", "Detected / Not detected"),
    ("normal_abnormal", "Normal / Abnormal"),
    ("select", "Select option"),
]

# The fixed choices behind the non-numeric types. Held here so the entry form
# and the report agree on the spelling of "Non-reactive".
TYPE_OPTIONS = {
    "positive_negative": ["Positive", "Negative"],
    "reactive_nonreactive": ["Reactive", "Non-reactive"],
    "detected_notdetected": ["Detected", "Not detected"],
    "normal_abnormal": ["Normal", "Abnormal"],
}

TEST_CATEGORIES = [
    ("haematology", "Haematology"),
    ("chemistry", "Chemical Pathology / Clinical Chemistry"),
    ("microbiology", "Microbiology"),
    ("parasitology", "Parasitology"),
    ("urinalysis", "Urinalysis"),
    ("serology", "Serology / Immunology"),
    ("endocrinology", "Endocrinology / Hormones"),
    ("other", "Other"),
]


class LabTest(TimeStampedModel):
    """
    One orderable test. Everything about it is editable by the lab — a test
    is deactivated rather than deleted, so results filed against it keep
    reading correctly.
    """
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=20, choices=TEST_CATEGORIES, default="other")
    description = models.TextField(blank=True)

    # What the bench needs before it can start.
    specimen_type = models.CharField(max_length=120, blank=True)
    container = models.CharField(max_length=120, blank=True)
    turnaround_hours = models.PositiveIntegerField(
        null=True, blank=True, help_text="Roughly how long a result takes, in hours.")

    # The lab's own price list. `billing_item` links a test to the Billing
    # Catalogue where the counter prices it; when it is set, that price wins,
    # because the cash desk must never be quoting a second figure.
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    billing_item = models.ForeignKey(
        "billing.BillingItem", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="lab_tests")

    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_tests")
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["category", "display_order", "name"]
        indexes = [models.Index(fields=["category", "is_active"])]

    def __str__(self):
        return self.name

    @property
    def charge_amount(self):
        """What this test costs, the catalogue price taking precedence."""
        if self.billing_item_id and self.billing_item.price:
            return self.billing_item.price
        return self.price


class LabParameter(TimeStampedModel):
    """
    One line on a test's result form.

    `is_required` exists because a hospital may one day insist on a value —
    a blood group with no ABO is not a blood group — but it defaults to
    False and nothing in the seeded catalogue turns it on. The bench fills in
    what it measured.
    """
    test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="parameters")
    code = models.SlugField(max_length=40)
    name = models.CharField(max_length=160)
    # Urinalysis is read in three passes (physical, chemical, microscopy) and
    # a semen analysis in two. The group is only a heading on the form and on
    # the report; a parameter with no group sits under the test itself.
    group = models.CharField(max_length=80, blank=True)

    result_type = models.CharField(max_length=25, choices=RESULT_TYPES, default="numeric")
    unit = models.CharField(max_length=40, blank=True)

    # What is printed beside the result. Free text, because half of the real
    # ranges are "12 – 16 (M), 11 – 15 (F)" or "< 200".
    reference_range = models.CharField(max_length=120, blank=True)
    # What the flagging actually reads. Left null where a range cannot be
    # reduced to two numbers — a missing bound simply means nothing is
    # flagged on that side, which is the safe direction to fail in.
    ref_low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    # The expected answer where it is not a number: "Negative", "Not detected".
    normal_value = models.CharField(max_length=120, blank=True)

    options = models.JSONField(default=list, blank=True)
    display_order = models.PositiveIntegerField(default=100)
    is_required = models.BooleanField(
        default=False,
        help_text="Off by design. A parameter is only filled in when it was measured.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["test", "display_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["test", "code"], name="unique_parameter_code_per_test"),
        ]

    def __str__(self):
        return f"{self.test.code}.{self.code}"

    @property
    def choices(self):
        """The options the entry form offers, whether fixed by type or set."""
        return TYPE_OPTIONS.get(self.result_type) or list(self.options or [])


class LabPanel(TimeStampedModel):
    """
    A named group of tests ordered together — the antenatal booking profile
    being the one every maternity unit uses.

    It *references* catalogue tests rather than restating them, so a change
    to the FBC's parameters reaches the antenatal profile with it and there
    is never a second Full Blood Count drifting out of step.
    """
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    tests = models.ManyToManyField(LabTest, related_name="panels", blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LabOrder(TimeStampedModel):
    """
    A request for laboratory work on one patient, during one visit.

    It hangs off the existing referral flow rather than replacing it: when a
    doctor refers a patient to the lab, `route` is that `PatientRoute`, so
    the queue, the notifications and the station all keep working exactly as
    they did. The order is what the bench types results into.
    """
    STATUS = [
        ("requested", "Requested"),
        ("collected", "Sample collected"),
        ("in_progress", "In progress"),
        ("awaiting_verification", "Awaiting verification"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]
    PRIORITY = [("routine", "Routine"), ("urgent", "Urgent"), ("emergency", "Emergency")]

    order_number = models.CharField(max_length=24, unique=True, blank=True, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="lab_orders")
    visit = models.ForeignKey(Visit, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="lab_orders")
    # The referral this order answers. One route, one order — the station
    # opens the route and finds the order already waiting for it.
    route = models.OneToOneField(PatientRoute, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="lab_order")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="lab_orders_requested")
    priority = models.CharField(max_length=10, choices=PRIORITY, default="routine")
    status = models.CharField(max_length=25, choices=STATUS, default="requested")
    clinical_notes = models.TextField(blank=True)

    # The sample itself. A result belongs to a specimen, and a specimen has a
    # time — a glucose is read differently at 8am fasting than at noon.
    specimen_id = models.CharField(max_length=40, blank=True)
    specimen_collected_at = models.DateTimeField(null=True, blank=True)
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="lab_specimens_collected")

    lab_comments = models.TextField(blank=True)

    # Who the result goes back to. Normally the doctor who asked, which is
    # the whole point of a referral; the bench can redirect it when that
    # doctor is off and somebody else is covering the patient. One named
    # person, never a broadcast.
    report_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="lab_results_addressed")

    # Who did what, and when. A result with no name on it cannot be queried
    # a month later, and an amendment with no name cannot be trusted at all.
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="lab_results_entered")
    entered_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="lab_results_verified")
    verified_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="lab_orders_created")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"{self.order_number or 'LAB'} — {self.patient}"

    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating and not self.order_number:
            # Off the PK sequence, like the patient's file number: unique and
            # ordered by when it was raised, with no counter to race on.
            self.order_number = f"LAB-{self.pk:06d}"
            super().save(update_fields=["order_number"])

    @property
    def is_verified(self):
        return self.verified_at is not None


class LabOrderTest(TimeStampedModel):
    """
    One test on an order — and a **snapshot of the catalogue as it stood the
    day it was ordered**.

    The catalogue is the master configuration, but a report is a historical
    document. If the reference range for Fasting Blood Glucose is edited next
    year, last year's report must still print the range the result was read
    against, and last year's bill must still say what was charged. So the
    name, category, specimen, price and the whole parameter definition are
    copied here when the test is put on the order, and everything downstream
    — the entry form, the report, the charge — reads the copy.

    The `test` FK stays for reporting and integrity ("how many FBCs did we
    run?"), but it is never the source of truth for how this result reads.
    """
    STATUS = [
        ("pending", "Pending"),
        ("draft", "Draft — being entered"),
        ("submitted", "Submitted for verification"),
        ("verified", "Verified / released"),
        ("cancelled", "Cancelled"),
    ]
    # Where the line came from. A doctor's request is the order; anything the
    # bench adds itself is marked, so "who asked for this?" has an answer —
    # and both are billed the same way.
    SOURCE = [("requested", "Requested by the clinician"),
              ("laboratory", "Added by the laboratory")]

    order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="items")
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="order_items")
    status = models.CharField(max_length=15, choices=STATUS, default="pending")
    source = models.CharField(max_length=15, choices=SOURCE, default="requested")

    # --- the snapshot ---
    test_name = models.CharField(max_length=160, blank=True)
    test_category = models.CharField(max_length=20, blank=True)
    specimen_type = models.CharField(max_length=120, blank=True)
    container = models.CharField(max_length=120, blank=True)
    # What this test cost when it was ordered. The catalogue price can change
    # tomorrow; this one cannot.
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Every parameter as configured at order time: id, code, name, group,
    # result type, unit, reference range, bounds, options, order.
    parameters_snapshot = models.JSONField(default=list, blank=True)

    # The charge raised for this test, so the bench can read whether it has
    # been paid without the laboratory ever touching money itself.
    charge = models.ForeignKey("billing.Charge", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="lab_order_tests")

    comments = models.TextField(blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="lab_tests_performed")
    performed_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="lab_tests_verified")
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "test"], name="unique_test_per_lab_order"),
        ]

    def __str__(self):
        return f"{self.order.order_number}: {self.name}"

    @property
    def name(self):
        """The name as ordered — not as the catalogue reads today."""
        return self.test_name or self.test.name

    @property
    def parameters(self):
        """
        The result form's definition.

        The order-time snapshot whenever there is one — which is every row
        `services.add_tests` created. The live catalogue is the fallback only
        for a row written before snapshots existed, or one built directly in
        a test; it is a safety net, not the normal path, because reading the
        catalogue is exactly what lets an edit rewrite history.
        """
        if self.parameters_snapshot:
            return self.parameters_snapshot
        from .services import snapshot_of
        return snapshot_of(self.test)

    @property
    def is_released(self):
        return self.status == "verified"


class LabResultValue(TimeStampedModel):
    """
    One measured parameter.

    A row exists only where the bench entered something. Clearing a value
    deletes the row rather than storing an empty string, so "no row" is the
    single meaning of "not done" — and the report can simply print the rows
    it finds.
    """
    FLAGS = [
        ("", "—"),
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
        ("abnormal", "Abnormal"),
        ("critical", "Critical"),
        ("positive", "Positive"),
        ("negative", "Negative"),
        ("reactive", "Reactive"),
        ("non_reactive", "Non-reactive"),
    ]
    order_test = models.ForeignKey(LabOrderTest, on_delete=models.CASCADE, related_name="values")
    parameter = models.ForeignKey(LabParameter, on_delete=models.PROTECT, related_name="results")
    value = models.CharField(max_length=255)
    # Worked out from the reference range for a numeric result, or set by
    # hand. Either way it is a flag on a number, never a diagnosis.
    flag = models.CharField(max_length=15, choices=FLAGS, blank=True, default="")
    # True when a person overrode the automatic flag, so recalculating on a
    # later save does not quietly undo their judgement.
    flag_is_manual = models.BooleanField(default=False)
    comment = models.CharField(max_length=255, blank=True)

    # The figures as they stood when the result was entered. A reference
    # range edited next year must not rewrite what last year's report said.
    unit_at_entry = models.CharField(max_length=40, blank=True)
    reference_at_entry = models.CharField(max_length=120, blank=True)

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="lab_values_recorded")

    class Meta:
        ordering = ["order_test", "parameter__display_order", "parameter_id"]
        constraints = [
            models.UniqueConstraint(fields=["order_test", "parameter"],
                                    name="unique_value_per_parameter"),
        ]

    def __str__(self):
        return f"{self.parameter.name}: {self.value}"


class LabResultAmendment(TimeStampedModel):
    """
    A result that has been corrected after it went out.

    The value itself is overwritten — a report has to show the true figure —
    but never silently: the old value, who changed it and why are kept here,
    the way `ConsultationNoteAmendment` keeps a note's history.
    """
    order_test = models.ForeignKey(LabOrderTest, on_delete=models.CASCADE, related_name="amendments")
    parameter = models.ForeignKey(LabParameter, on_delete=models.PROTECT, related_name="amendments")
    previous_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    amended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="lab_amendments")

    class Meta:
        ordering = ["-created_at"]
