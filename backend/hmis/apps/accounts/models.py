from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "Super Admin"
    HOSPITAL_ADMIN = "hospital_admin", "Hospital Admin"
    DOCTOR = "doctor", "Doctor"
    NURSE = "nurse", "Nurse"
    RECEPTION = "reception", "Reception"
    PHARMACIST = "pharmacist", "Pharmacist"
    LABORATORY = "laboratory", "Laboratory Scientist"
    RADIOLOGY = "radiology", "Radiology Staff"
    OPTOMETRIST = "optometrist", "Optometrist"
    OPHTHALMOLOGIST = "ophthalmologist", "Ophthalmologist / Eye Doctor"
    SURGEON = "surgeon", "Surgeon"
    ANESTHETIST = "anesthetist", "Anesthetist"
    CASHIER = "cashier", "Billing Officer / Cashier"
    WARD_MANAGER = "ward_manager", "Ward Manager"
    RECORDS_OFFICER = "records_officer", "Records Officer"
    INVENTORY_MANAGER = "inventory_manager", "Inventory Manager"
    ACCOUNTANT = "accountant", "Accountant"
    EXECUTIVE = "executive", "Management / Executive"
    CUSTOM = "custom", "Custom role"


class User(AbstractUser):
    role = models.CharField(max_length=20, choices=Role.choices)
    department = models.CharField(max_length=100, blank=True)
    must_change_password = models.BooleanField(default=False)
    sensitive_record_access = models.BooleanField(default=False)

    @property
    def is_admin(self):
        return self.is_superuser or self.role in {Role.ADMIN, Role.HOSPITAL_ADMIN}

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"
