from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel
from apps.patients.models import Patient
from apps.workflow.models import Visit
from apps.departments.models import Department

class InvestigationCatalog(TimeStampedModel):
    KIND=[("laboratory","Laboratory"),("imaging","Imaging"),("eye","Eye")]
    code=models.SlugField(unique=True); name=models.CharField(max_length=160); kind=models.CharField(max_length=20,choices=KIND)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL); price=models.DecimalField(max_digits=12,decimal_places=2,default=0); is_active=models.BooleanField(default=True)

class InvestigationOrder(TimeStampedModel):
    STATUS=[("requested","Requested"),("collected","Collected"),("in_progress","In progress"),("completed","Completed"),("cancelled","Cancelled")]
    patient=models.ForeignKey(Patient,on_delete=models.PROTECT,related_name="investigation_orders"); visit=models.ForeignKey(Visit,null=True,blank=True,on_delete=models.SET_NULL,related_name="investigations")
    investigation=models.ForeignKey(InvestigationCatalog,on_delete=models.PROTECT); requested_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name="investigations_requested")
    status=models.CharField(max_length=20,choices=STATUS,default="requested"); clinical_notes=models.TextField(blank=True); performed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="investigations_performed")
    class Meta: ordering=["-created_at"]

class InvestigationResult(TimeStampedModel):
    order=models.OneToOneField(InvestigationOrder,on_delete=models.CASCADE,related_name="result")
    findings=models.TextField(); conclusion=models.TextField(blank=True); values=models.JSONField(default=dict,blank=True); attachment=models.FileField(upload_to="investigations/%Y/%m/",blank=True)
    released_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT); released_at=models.DateTimeField(auto_now_add=True)
