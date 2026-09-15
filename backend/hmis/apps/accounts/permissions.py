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

# The Super Admin, and only them. `Role.ADMIN` is spelled "Super Admin" in the
# role list a person picks from — `hospital_admin` is the ordinary
# administrator beside it — so the distinction already exists in the RBAC and
# is not invented here. It is what guards the one irreversible action in the
# application: permanently deleting a patient.
SUPER_ADMIN_ROLES = ["admin"]

# Who may look a patient up at all. Reading a name is not reading a chart —
# the health-record tiles and the overview are gated far more tightly (see
# ClinicalRecordAccess), and reception gets the demographics-only serializer.
PATIENT_LOOKUP_ROLES = [
    "reception", "doctor", "nurse", "pharmacist", "laboratory", "radiology",
    "optometrist", "ophthalmologist", "cashier", "accountant", "ward_manager",
]

# Who works a ward: the bed board, admissions, transfers and discharges.
WARD_ROLES = ["ward_manager", "doctor", "nurse"]

# Who treats a patient from a desk of their own: reads the chart, writes the
# consultation note, prescribes and refers. The general doctor, and the eye
# doctor (`ophthalmologist`, "Ophthalmologist / Eye Doctor") working the same
# chart with an eye examination on the note.
#
# Deliberately *not* what `IsDoctor` means. That still names the general
# doctor alone, and the things built on it keep that meaning: appointments,
# nursing's Send to Doctor, a consultation route. Widen a capability to this
# group one at a time, where the eye doctor genuinely needs it — never by
# rewriting every `"doctor"`. Either role reaches only its own patients
# (`patients.access.patient_queryset_for`).
CLINICIAN_ROLES = ["doctor", "ophthalmologist"]

# Who works the ward **for their own patients only**. They see which beds are
# free and occupied, the name on a bed only when it is one of their patients,
# and admit, move and discharge only those patients. WARD_ROLES above is
# unchanged and still works the whole ward.
OWN_PATIENT_WARD_ROLES = ["ophthalmologist"]

# Who records the structured eye examination on a consultation note
# (`clinical/eye_exam.py`). A general doctor's note never carries one, so it
# stays exactly the note it was.
EYE_EXAMINATION_ROLES = ["ophthalmologist"]

# Who handles money at a counter.
BILLING_ROLES = ["cashier", "accountant", "reception"]

# Who moves stock.
STOCK_ROLES = ["pharmacist", "inventory_manager"]

# The pharmacy's walk-in till (POS). Pharmacists work the counter and cashiers
# take money; both open a register, ring up sales and take payment. Admins pass
# every group, as everywhere.
POS_ROLES = ["pharmacist", "cashier"]

# Who reads POS sales, registers and their reconciliation. The accountant
# reconciles the tills but never operates one.
POS_HISTORY_ROLES = [*POS_ROLES, "accountant"]

# Who may discount a POS sale — rule 13's boundary, unchanged. A pharmacist
# completes sales at the till but cannot give money away (rule 18).
POS_DISCOUNT_ROLES = ["cashier", "accountant"]

# Who may take a POS return. It pays money out of a till, so it needs refund
# authority (REFUND_ROLES) held by somebody who operates one (POS_ROLES).
POS_RETURN_ROLES = ["cashier"]

# Who may approve a POS discount **larger than a cashier's configured limit**
# (`HospitalSettings.pos_discount_limit_percent` / `_amount`).
#
# Deliberately narrower than POS_DISCOUNT_ROLES, and that narrowing is the
# whole point: a cashier gives the everyday discount, and the person who
# authorises an unusual one is somebody else — the same separation rule 13
# draws between recording money and forgiving it. A cashier is *not* here, so
# a cashier can never authorise their own over-limit discount; an accountant
# or an administrator may, and when one of them is working the till they
# approve their own by being who they are, which is recorded as such.
POS_DISCOUNT_APPROVAL_ROLES = ["accountant", *ADMIN_ROLES]


def has_any_role(user, roles):
    """
    `RoleRequired`'s rule as a plain check — for a service that must refuse on
    its own, whatever view called it. Admins pass, as they do everywhere.
    """
    if not (user and getattr(user, "is_authenticated", False)):
        return False
    return getattr(user, "role", None) in {*ADMIN_ROLES, *roles}

# Who may hand money back. Refunding is the one billing action that takes cash
# *out* of the drawer, so it sits with the roles that already approve a
# discount or a waiver (rule 13) — never with reception, and never with the
# pharmacy counter, which may collect but never adjust (rule 18).
REFUND_ROLES = ["cashier", "accountant"]

# Who may cancel a service — withdraw a bill so the patient no longer owes it.
# Named apart from REFUND_ROLES even while the two hold the same people:
# cancelling ends an obligation and moves no money, refunding takes cash out of
# the drawer, and letting somebody cancel must never hand them the drawer by
# accident. Cancel & refund is both decisions at once, so it needs both groups
# (`billing.views.ChargeViewSet.get_permissions`).
CANCEL_ROLES = ["cashier", "accountant"]

# Who is told when the patient's financial position changes — a bill raised, a
# payment taken, a discount, a waiver, a refund, a cancellation.
#
# The desks that work the money: the cash desk, the accounts office and the
# front desk, plus admin. Deliberately *not* every role — a nurse has nothing
# to do about a consultation fee, and a bell that rings for work you cannot do
# is a bell that stops being read (rule 14).
FINANCIAL_NOTICE_ROLES = [*ADMIN_ROLES, "cashier", "accountant", "reception"]


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


def is_super_admin(user):
    """
    The Super Admin alone — `Role.ADMIN`, or a Django superuser.

    Named once so the API (`IsSuperAdmin`) and Django admin's patient purge
    (`patients/services.py`) cannot disagree about who that is.
    """
    if not (user and user.is_authenticated):
        return False
    return bool(user.is_superuser) or getattr(user, "role", None) in set(SUPER_ADMIN_ROLES)


class IsSuperAdmin(permissions.BasePermission):
    """
    The Super Admin alone — `Role.ADMIN`, or a Django superuser.

    `IsAdmin` is not this: it lets `hospital_admin` through as well, which is
    right for configuration and wrong for anything irreversible. Reach for
    this only where the answer to "should an ordinary administrator be able to
    do it?" is no.
    """

    def has_permission(self, request, view):
        return is_super_admin(request.user)


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
    """The chart: overview, health-record tiles, consultation notes."""
    allowed_roles = CLINICIAN_ROLES


class ClinicianOrNurse(RoleRequired):
    """Reading vitals and nursing notes. Recording them stays `IsNurse`."""
    allowed_roles = [*CLINICIAN_ROLES, "nurse"]


class WardStaff(RoleRequired):
    """The ward round: who is in which bed, admitting, moving, discharging."""
    allowed_roles = WARD_ROLES
