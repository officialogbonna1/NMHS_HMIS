from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit
class Ward(TimeStampedModel):
    name=models.CharField(max_length=120,unique=True); department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL); is_active=models.BooleanField(default=True)
    class Meta: ordering=["name"]

    def __str__(self):
        """"Male Medical Ward" — the name is what the ward is called on the
        board, so it is what a bed's dropdown and the bed board show."""
        return self.name
class Bed(TimeStampedModel):
    ward=models.ForeignKey(Ward,on_delete=models.CASCADE,related_name="beds"); number=models.CharField(max_length=30); is_active=models.BooleanField(default=True)
    class Meta:
        ordering=["ward__name","number"]
        constraints=[models.UniqueConstraint(fields=["ward","number"],name="unique_ward_bed")]

    def __str__(self):
        """
        "Bed 01 — Male Medical Ward". The number alone is ambiguous: every
        ward has an 01, and an admission's bed dropdown lists all of them.
        """
        return f"Bed {self.number} — {self.ward.name}"
class Admission(TimeStampedModel):
    STATUS=[("admitted","Admitted"),("discharged","Discharged"),("cancelled","Cancelled")]
    patient=models.ForeignKey(Patient,on_delete=models.PROTECT,related_name="admissions"); visit=models.OneToOneField(Visit,null=True,blank=True,on_delete=models.SET_NULL,related_name="admission")
    bed=models.ForeignKey(Bed,on_delete=models.PROTECT,related_name="admissions"); admitted_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name="admissions_created"); attending_doctor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="admissions_attending")
    diagnosis=models.TextField(blank=True); status=models.CharField(max_length=20,choices=STATUS,default="admitted"); admitted_at=models.DateTimeField(auto_now_add=True); discharged_at=models.DateTimeField(null=True,blank=True)
    class Meta: ordering=["-admitted_at"]

    def __str__(self):
        """
        Who is in which bed, and whether they are still there — the three
        things somebody picking an admission off a list is choosing between.
        A patient can hold several over time (rule 9), so the status is part
        of the label rather than a detail behind it.
        """
        return f"{self.patient} · {self.bed} · {self.get_status_display()}"
class BedTransfer(TimeStampedModel):
    admission=models.ForeignKey(Admission,on_delete=models.CASCADE,related_name="transfers"); from_bed=models.ForeignKey(Bed,on_delete=models.PROTECT,related_name="transfers_out"); to_bed=models.ForeignKey(Bed,on_delete=models.PROTECT,related_name="transfers_in"); transferred_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT); reason=models.TextField(blank=True)

    def __str__(self):
        """The move itself: who was moved, out of which bed and into which."""
        return f"{self.admission.patient} · {self.from_bed} → {self.to_bed}"
class DischargeSummary(TimeStampedModel):
    """
    The completed discharge for one admission, and the letter that is printed
    from it.

    `OneToOneField` is the rule about duplicates: an admission is discharged
    once. A patient readmitted next month gets a new `Admission` and a new
    discharge, which is a different record and not a second discharge of this
    one (rule 9's "multiple admissions over time").

    `reference` is the number quoted on the letter and in the notification —
    `DCH-000123`, issued off the primary key exactly as `StockTransfer` and the
    hospital number are, so it is unique and ordered with no counter to race on.
    """
    admission=models.OneToOneField(Admission,on_delete=models.CASCADE,related_name="discharge_summary"); diagnosis=models.TextField(); summary=models.TextField(); instructions=models.TextField(blank=True); follow_up=models.DateField(null=True,blank=True); completed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    #: The reference on the letter. Blank only for the moment between insert
    #: and the second save below, and on rows written before this field existed.
    reference = models.CharField(max_length=24, unique=True, blank=True, null=True, editable=False)
    #: How the patient left — "Recovered", "Referred", "Against medical advice".
    #: Free text because the hospital has no coded list for it and inventing
    #: one here would be inventing clinical vocabulary.
    condition = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference or 'DCH'}: {self.admission.patient}"

    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating and not self.reference:
            self.reference = f"DCH-{self.pk:06d}"
            super().save(update_fields=["reference"])
