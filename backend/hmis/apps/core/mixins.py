"""
Shared model behavior: records that lock themselves after first save.
Vitals, reception intake, and consultation notes all use this so that
nobody but Admin can silently rewrite clinical history. Corrections
should go through an Amendment model instead of editing in place.
"""
from django.db import models
from django.core.exceptions import PermissionDenied


class LockedRecordMixin(models.Model):
    """
    Mix into any model that must become immutable after its first save.
    - New records save normally.
    - Once is_locked=True, only a caller who passes admin_override=True
      (checked in the view/serializer against request.user.is_admin)
      may update it; anyone else raises PermissionDenied.
    """
    is_locked = models.BooleanField(default=False)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def save(self, *args, admin_override=False, **kwargs):
        if self.pk and self.is_locked and not admin_override:
            raise PermissionDenied(
                "This record is locked. Only an admin can amend it."
            )
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating and not self.is_locked:
            # Lock immediately after the first successful save.
            type(self).objects.filter(pk=self.pk).update(
                is_locked=True, locked_at=models.functions.Now()
            )
            self.is_locked = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
