from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import Visit
class Ward(TimeStampedModel):
    name=models.CharField(max_length=120,unique=True); department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL); is_active=models.BooleanField(default=True)
class Bed(TimeStampedModel):
    ward=models.ForeignKey(Ward,on_delete=models.CASCADE,related_name="beds"); number=models.CharField(max_length=30); is_active=models.BooleanField(default=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["ward","number"],name="unique_ward_bed")]
class Admission(TimeStampedModel):
    STATUS=[("admitted","Admitted"),("discharged","Discharged"),("cancelled","Cancelled")]
    patient=models.ForeignKey(Patient,on_delete=models.PROTECT,related_name="admissions"); visit=models.OneToOneField(Visit,null=True,blank=True,on_delete=models.SET_NULL,related_name="admission")
    bed=models.ForeignKey(Bed,on_delete=models.PROTECT,related_name="admissions"); admitted_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name="admissions_created"); attending_doctor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="admissions_attending")
    diagnosis=models.TextField(blank=True); status=models.CharField(max_length=20,choices=STATUS,default="admitted"); admitted_at=models.DateTimeField(auto_now_add=True); discharged_at=models.DateTimeField(null=True,blank=True)
class BedTransfer(TimeStampedModel):
    admission=models.ForeignKey(Admission,on_delete=models.CASCADE,related_name="transfers"); from_bed=models.ForeignKey(Bed,on_delete=models.PROTECT,related_name="transfers_out"); to_bed=models.ForeignKey(Bed,on_delete=models.PROTECT,related_name="transfers_in"); transferred_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT); reason=models.TextField(blank=True)
class DischargeSummary(TimeStampedModel):
    admission=models.OneToOneField(Admission,on_delete=models.CASCADE,related_name="discharge_summary"); diagnosis=models.TextField(); summary=models.TextField(); instructions=models.TextField(blank=True); follow_up=models.DateField(null=True,blank=True); completed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
