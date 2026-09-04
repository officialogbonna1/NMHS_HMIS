from datetime import date

from django.db import models
from django.conf import settings
from apps.core.mixins import TimeStampedModel


class Patient(TimeStampedModel):
    SEX_CHOICES = [("M", "Male"), ("F", "Female")]

    file_number = models.CharField(max_length=20, unique=True, blank=True, editable=False)

    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    sex = models.CharField(max_length=1, choices=SEX_CHOICES)
    birthdate = models.DateField(null=True, blank=True)
    # The fallback when a birthdate is not known. It carries its own unit
    # because "0 years" is how a three-day-old baby disappears from the
    # record — and on a maternity ward that is most of the register.
    AGE_UNITS = [("days", "Days"), ("weeks", "Weeks"), ("months", "Months"), ("years", "Years")]
    age_value = models.PositiveIntegerField(null=True, blank=True)
    age_unit = models.CharField(max_length=10, choices=AGE_UNITS, default="years", blank=True)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=30, blank=True)
    short_note = models.TextField(blank=True)

    street_address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    # Defaulted rather than required: almost every patient is local, and a
    # field the desk has to retype for each one gets left blank.
    country = models.CharField(max_length=100, blank=True, default="Nigeria")

    # Who to call if something happens. Kept on the patient rather than in a
    # note, because at the moment it is needed nobody is going to read prose
    # looking for a phone number.
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_relationship = models.CharField(max_length=60, blank=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True)
    emergency_contact_alt_phone = models.CharField(max_length=30, blank=True)
    emergency_contact_address = models.CharField(max_length=255, blank=True)
    emergency_contact_notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="patients_registered"
    )

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["last_name", "first_name"])]

    def __str__(self):
        return f"{self.last_name}, {self.first_name}"

    @property
    def age_display(self):
        """
        How old, in the largest unit that still says something useful. A
        birthdate is exact, so it is preferred; otherwise the stated age is
        shown in the unit it was given in.
        """
        if self.birthdate:
            days = (date.today() - self.birthdate).days
            if days < 0:
                return None
            if days < 14:
                return f"{days} day{'s' if days != 1 else ''}"
            if days < 60:
                weeks = days // 7
                return f"{weeks} week{'s' if weeks != 1 else ''}"
            if days < 730:
                months = days // 30
                return f"{months} month{'s' if months != 1 else ''}"
            return f"{days // 365} yrs"
        if self.age_value is None:
            return None
        unit = self.age_unit or "years"
        label = "yrs" if unit == "years" else unit.rstrip("s") + ("s" if self.age_value != 1 else "")
        return f"{self.age_value} {label}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new and not self.file_number:
            # Derived from the DB's own PK sequence — unique and ordered by
            # registration for free, no separate counter to race on.
            self.file_number = f"NMHS-{self.pk:06d}"
            super().save(update_fields=["file_number"])


# --- Health record tiles (mirrors the 9 sections in the reference manual) ---

class Allergy(TimeStampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="allergies")
    name = models.CharField(max_length=150)
    reactions = models.JSONField(default=list, blank=True)  # list of reaction strings
    is_dangerous = models.BooleanField(default=False)
    notes = models.TextField(blank=True)


class Medication(TimeStampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="medications")
    name = models.CharField(max_length=150)
    strength = models.CharField(max_length=50, blank=True)
    consumption_type = models.CharField(max_length=50, blank=True)
    dose_frequency = models.CharField(max_length=100, blank=True)
    dose_schedule = models.CharField(max_length=100, blank=True)
    as_needed = models.BooleanField(default=True)
    time_frame_days = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)


class MedicalCondition(TimeStampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="conditions")
    name = models.CharField(max_length=150)
    date_diagnosed = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)


class MedicalDevice(TimeStampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="devices")
    name = models.CharField(max_length=150)
    make = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    device_id = models.CharField(max_length=100, blank=True)
    date_acquired = models.DateField(null=True, blank=True)
    next_update = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)


class SurgicalHistory(TimeStampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="surgeries")
    name = models.CharField(max_length=150)
    surgery_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)


class FamilyMedicalHistory(TimeStampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="family_history")
    relationship = models.CharField(max_length=50)  # Mother, Father, Sister, etc.
    is_deceased = models.BooleanField(default=False)
    conditions = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)


class SocialHistory(TimeStampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="social_history")
    category = models.CharField(max_length=50)  # Smoking, Drinking
    is_active = models.BooleanField(default=False)
    frequency = models.CharField(max_length=100, blank=True)
    amount = models.CharField(max_length=100, blank=True)
    started_year = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)


class Vaccination(TimeStampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="vaccinations")
    name = models.CharField(max_length=150)
    date_administered = models.DateField(null=True, blank=True)
    next_due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)


class MedicalTest(TimeStampedModel):
    TYPE_CHOICES = [
        ("labs", "Labs"), ("xray", "X-ray"), ("mri", "MRI"),
        ("ct", "CT Scan"), ("ultrasound", "Ultrasound"), ("other", "Other"),
    ]
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="tests")
    title = models.CharField(max_length=200)
    test_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    test_date = models.DateField()
    # Optional because a result is not always a document: a verbal lab result
    # read over the phone gets typed into `impressions` and is still the
    # record of the test. The serializer insists on one or the other.
    file = models.FileField(upload_to="medical_tests/%Y/%m/", blank=True, null=True)
    impressions = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    # Set when the lab / imaging / eye clinic filed this from a doctor's
    # referral, so re-saving that result updates this row instead of filing
    # a second copy of the same test.
    source_route = models.ForeignKey(
        "workflow.PatientRoute", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="filed_tests",
    )

    class Meta:
        # Newest result first, and an explicit order so paging is stable —
        # without one the database is free to return a different page 2 each
        # time, which is how a result goes missing from a list.
        ordering = ["-test_date", "-created_at"]
