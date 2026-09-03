from django.conf import settings
from django.db import models
from apps.core.mixins import TimeStampedModel


class Department(TimeStampedModel):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_departments")
    staff = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="department_memberships")
    visible_domains = models.JSONField(default=list, blank=True, help_text="Approved record domains, e.g. demographics, clinical, billing")

    class Meta:
        ordering = ["name"]

    def __str__(self): return self.name


class Service(TimeStampedModel):
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="services")
    name = models.CharField(max_length=150)
    code = models.SlugField()
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["department", "code"], name="unique_department_service_code")]
        ordering = ["department__name", "name"]
