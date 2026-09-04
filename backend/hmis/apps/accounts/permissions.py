"""
Role-based permission classes for DRF.
Combine with LockedRecordMixin (in core/mixins.py) on models that must
become read-only for non-admins once saved.

The role *groups* below are the one place a set of roles is named. A viewset
that wants "the roles that work a ward" imports WARD_ROLES rather than
retyping the list, so widening a group is one edit instead of a hunt — and
`frontend/src/auth/roles.js` mirrors these names so a page and the endpoint
behind it cannot drift apart.
"""
from rest_framework import permissions

# Admin is not a hospital job so much as a bypass: every RoleRequired check
# lets these two through, which is why they are never listed in a group.
ADMIN_ROLES = ["admin", "hospital_admin"]

# Who may look a patient up at all. Reading a name is not reading a chart —
# the health-record tiles and the overview are gated far more tightly (see
# ClinicalRecordAccess), and reception gets the demographics-only serializer.
PATIENT_LOOKUP_ROLES = [
    "reception", "doctor", "nurse", "pharmacist", "laboratory", "radiology",
    "optometrist", "ophthalmologist", "cashier", "accountant", "ward_manager",
]

# Who works a ward: the bed board, admissions, transfers and discharges.
WARD_ROLES = ["ward_manager", "doctor", "nurse"]

# Who handles money at a counter.
BILLING_ROLES = ["cashier", "accountant", "reception"]

# Who moves stock.
STOCK_ROLES = ["pharmacist", "inventory_manager"]


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
        if not (user and user.is_authenticated):
            return False
        # getattr, not user.role: an anonymous or non-HMIS user has no role,
        # and a missing attribute must read as "denied", never as a 500.
        return getattr(user, "role", None) in {*ADMIN_ROLES, *self.allowed_roles}


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return getattr(user, "role", None) in set(ADMIN_ROLES)


class ReadOnlyForRoles(RoleRequired):
    """Safe methods for `allowed_roles`; writes for admin only.

    For the reference data everybody quotes from and nobody but the back
    office edits — wards, beds, the department list.
    """

    def has_permission(self, request, view):
        if request.method not in permissions.SAFE_METHODS:
            return IsAdmin().has_permission(request, view)
        return super().has_permission(request, view)


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


class WardStaff(RoleRequired):
    """The ward round: who is in which bed, admitting, moving, discharging."""
    allowed_roles = WARD_ROLES
