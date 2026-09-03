"""
Role-based permission classes for DRF.
Combine with LockedRecordMixin (in core/mixins.py) on models that must
become read-only for non-admins once saved.
"""
from rest_framework import permissions


class RoleRequired(permissions.BasePermission):
    """Usage: permission_classes = [RoleRequired(['doctor', 'admin'])]"""
    allowed_roles = []

    def __init__(self, allowed_roles=None):
        if allowed_roles is not None:
            self.allowed_roles = allowed_roles

    def has_permission(self, request, view):
        user = request.user
        # HMIS role is the source of truth for API access. Django superuser is
        # intentionally not an implicit clinical-data bypass: staff accounts
        # are sometimes created from a superuser template and must still obey
        # their assigned hospital role.
        return bool(user and user.is_authenticated and user.role in {"admin", "hospital_admin", *self.allowed_roles})


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in {"admin", "hospital_admin"})


class IsDoctor(RoleRequired):
    allowed_roles = ["doctor"]


class IsNurse(RoleRequired):
    allowed_roles = ["nurse"]


class IsReception(RoleRequired):
    allowed_roles = ["reception"]


class IsPharmacist(RoleRequired):
    allowed_roles = ["pharmacist"]


class ClinicalRecordAccess(RoleRequired):
    allowed_roles = ["doctor"]


class DoctorOrNurse(RoleRequired):
    allowed_roles = ["doctor", "nurse"]
