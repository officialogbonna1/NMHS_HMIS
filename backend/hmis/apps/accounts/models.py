from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.core import identifiers


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

    # `NMHS-S000001` — the staff half of the hospital's register, the mirror of
    # `Patient.patient_number`. Nullable rather than blank because not every
    # row in this table is a member of staff: an account with no HMIS role and
    # no superuser flag is a technical login that reaches nothing, and issuing
    # it a staff number would put a stranger on the payroll list. Several such
    # rows can coexist because a unique column permits many NULLs.
    staff_number = models.CharField(max_length=20, unique=True, null=True, blank=True,
                                    editable=False)

    @property
    def is_admin(self):
        return self.is_superuser or self.role in {Role.ADMIN, Role.HOSPITAL_ADMIN}

    @property
    def is_hospital_staff(self):
        """Whether this account belongs to a person the hospital employs, as
        opposed to a login with no role behind it."""
        return bool(self.role) or self.is_superuser

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Issued once, on the first save that makes this account staff — so an
        # account that is given a role later is numbered then, and a number
        # already held never moves. Deliberately a separate write: it must not
        # widen whatever `update_fields` the caller asked for.
        if self.is_hospital_staff and not self.staff_number:
            self.staff_number = identifiers.format_number(identifiers.STAFF, self.pk)
            super().save(update_fields=["staff_number"])

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"
