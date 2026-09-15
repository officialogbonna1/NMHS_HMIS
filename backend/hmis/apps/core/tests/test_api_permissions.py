"""
The whole API surface, checked against a written-down expectation.

Two questions this file exists to answer, and to keep answering as endpoints
are added:

1. **Is anything reachable without logging in?** Every `/api/` path is called
   with no credentials and must answer 401/403 — never 2xx, and never 500.
   (`/api/items/` used to raise AttributeError on `AnonymousUser.role` and
   return a 500 traceback with DEBUG on.)

2. **Can a role reach something it has no business reaching?** REACHABLE below
   names, per endpoint, exactly which roles get past the permission class.
   The test walks every role against every endpoint and fails on any
   difference — in *either* direction, so a widened permission is as loud as a
   narrowed one.

A new endpoint with no entry in REACHABLE fails the test by name. That is
deliberate: the failure is the prompt to decide who the endpoint is for,
rather than shipping it open because DRF's default is `IsAuthenticated` and
`IsAuthenticated` means "the cashier too".

To see the current truth after an intended change, run:

    ./venv/bin/python manage.py test apps.core.tests.test_api_permissions -v 2

— the failure message prints the table in paste-ready form.
"""
from django.test import TestCase
from django.urls import get_resolver
from rest_framework.authtoken.models import Token

from apps.accounts.models import Role, User
from apps.accounts.permissions import ADMIN_ROLES

# Roles that pass every RoleRequired check by definition. Listing them per
# endpoint below would say nothing, so they are excluded from the table and
# asserted separately.
ALWAYS = set(ADMIN_ROLES)

D = "doctor"; N = "nurse"; R = "reception"; PH = "pharmacist"
LAB = "laboratory"; RAD = "radiology"; OPT = "optometrist"; OPH = "ophthalmologist"
CASH = "cashier"; ACC = "accountant"; WARD = "ward_manager"; INV = "inventory_manager"

EVERYONE = "*"  # any authenticated user, whatever their role

# Which non-admin roles get a non-403 answer from a GET on each endpoint.
REACHABLE = {
    # --- your own account and your own inbox: everyone -------------------
    "/api/": EVERYONE,                              # DRF's route index
    "/api/auth/me/": EVERYONE,
    "/api/dashboard/": EVERYONE,
    "/api/notifications/": EVERYONE,                # scoped to the caller
    "/api/notifications/unread-count/": EVERYONE,
    "/api/notifications/mark_all_read/": EVERYONE,
    "/api/notifications/archive_selected/": EVERYONE,  # own notifications only
    "/api/notifications/archive_all/": EVERYONE,       # the caller's inbox
    "/api/users/": EVERYONE,                        # staff directory, minimal fields
    "/api/departments/": EVERYONE,
    "/api/services/": EVERYONE,
    "/api/billing-items/": EVERYONE,                # the price list, quoted from everywhere
    # Configuration everyone reads and an admin writes: the letterhead on
    # every printed document, and whether a category of notification is sent.
    "/api/hospital-settings/": EVERYONE,
    "/api/hospital-settings/current/": EVERYONE,
    "/api/notification-settings/": EVERYONE,

    # --- patient identity ------------------------------------------------
    # A name and a file number, not a chart. Reception gets demographics only.
    "/api/patients/": [R, D, N, PH, LAB, RAD, OPT, OPH, CASH, ACC, WARD],

    # --- the chart: the clinicians only (plus admin) ---------------------
    # CLINICIAN_ROLES — the general doctor and the eye doctor, each reaching
    # only their own patients (patients.access).
    "/api/allergies/": [D, OPH],
    "/api/medications/": [D, OPH],
    "/api/conditions/": [D, OPH],
    "/api/devices/": [D, OPH],
    "/api/surgical-history/": [D, OPH],
    "/api/family-history/": [D, OPH],
    "/api/social-history/": [D, OPH],
    "/api/vaccinations/": [D, OPH],
    "/api/medical-tests/": [D, OPH],
    "/api/notes/": [D, OPH],
    "/api/notes/eye-examination-fields/": [D, OPH],   # the note form's field definition
    "/api/note-amendments/": [D, OPH],

    # --- nursing ---------------------------------------------------------
    # Read by the clinicians; recorded by nurses alone (a POST is IsNurse).
    "/api/vitals/": [D, N, OPH],
    "/api/vitals/recorded-today/": [D, N, OPH],
    "/api/nursing-notes/": [D, N, OPH],

    # --- workflow --------------------------------------------------------
    "/api/visits/": [R, D, N],
    "/api/appointments/": [R, D],
    "/api/patient-routes/": [R, D, N, LAB, RAD, OPT, OPH],
    "/api/patient-routes/refer/": [R, D, N, LAB, RAD, OPT, OPH],
    "/api/patient-routes/send-to-doctor/": [R, D, N, LAB, RAD, OPT, OPH],

    # --- the ward --------------------------------------------------------
    # Was IsAuthenticated on all five: a cashier could list every inpatient,
    # admit somebody, move them between beds and discharge them.
    # The eye doctor reaches the ward for their own patients only
    # (OWN_PATIENT_WARD_ROLES) — see apps/inpatient/tests/test_eye_ward_scope.py.
    "/api/wards/": [WARD, D, N, OPH],
    "/api/beds/": [WARD, D, N, OPH],
    "/api/admissions/": [WARD, D, N, OPH],
    "/api/bed-transfers/": [WARD, D, N, OPH],
    "/api/discharges/": [WARD, D, N, OPH],

    # --- money -----------------------------------------------------------
    "/api/ledgers/": [CASH, ACC, R, PH],
    "/api/charges/": [CASH, ACC, R, PH],
    "/api/charges/discount-balance/": [CASH, ACC, R],
    "/api/payments/": [CASH, ACC, R, PH],
    "/api/adjustments/": [CASH, ACC, R],
    # The refund register — read like the write-off register, by the desks
    # that have to make a statement add up. Granting a refund is
    # `POST /api/payments/<id>/refund/` and is narrower still (REFUND_ROLES).
    "/api/refunds/": [CASH, ACC, R],
    # The figures heading the two desks. Narrower than the lists they count:
    # the Service Cancellations desk is CANCEL_ROLES and the Refunds desk is
    # REFUND_ROLES — the people who act on them, not everyone who reads a bill.
    "/api/charges/cancellation-summary/": [CASH, ACC],
    "/api/refunds/summary/": [CASH, ACC],
    # Hospital-wide revenue by department is a management figure, so it sits
    # with the cash desk and accounts rather than with every counter that can
    # take a payment — reception bills at a window and is not on it.
    "/api/finance/report/": [CASH, ACC],

    # --- pharmacy and stock ----------------------------------------------
    "/api/prescriptions/": [D, PH, OPH],
    "/api/prescriptions/bulk/": [D, PH, OPH],
    "/api/items/": [D, PH, INV, OPH],   # prescribers get availability, not counts
    "/api/batches/": [PH, INV],
    "/api/stock-movements/": [PH, INV],
    # Stock is product + batch + location. The locations are configuration —
    # readable by whoever works stock, writable by admin only, because the
    # receiving/dispensing flags decide where every delivery lands and where
    # every prescription draws from.
    # The catalogue's own configuration — product categories and units of
    # measure. STOCK_ROLES, the same as the products they describe: an
    # inventory manager who cannot add a category types it into the name.
    "/api/item-categories/": [PH, INV],
    "/api/units/": [PH, INV],
    "/api/stock-locations/": [PH, INV],
    "/api/stock-records/": [PH, INV],   # read-only: quantities move via services
    "/api/stock-transfers/": [PH, INV],
    "/api/stock-counts/": [PH, INV],
    "/api/stock-counts/sheet/": [PH, INV],

    # --- laboratory ------------------------------------------------------
    # The catalogue is a price list, so the desk reads it; a *result* is
    # clinical, so the orders are narrower.
    "/api/lab-tests/": [LAB, D, N, R, CASH, ACC, OPH],
    "/api/lab-tests/categories/": [LAB, D, N, R, CASH, ACC, OPH],
    "/api/lab-parameters/": [LAB, D, N, R, CASH, ACC, OPH],
    "/api/lab-parameters/reorder/": [LAB],
    "/api/lab-panels/": [LAB, D, N, R, CASH, ACC, OPH],
    "/api/lab-orders/": [LAB, D, R, CASH, ACC, OPH],
    "/api/lab-orders/worklist/": [LAB, D, R, CASH, ACC, OPH],
    "/api/lab-orders/for-route/": [LAB],

    # --- diagnostics: no page in front of it, kept to clinical roles ------
    "/api/investigations/": [D, LAB, RAD, OPT, OPH],
    "/api/investigation-orders/": [D, LAB, RAD, OPT, OPH],
    "/api/investigation-results/": [D, LAB, RAD, OPT, OPH],

    # --- admin only ------------------------------------------------------
    "/api/audit-logs/": [],
    "/api/notifications/overview/": [],
    # Not finished: SaleItem sells stock the shelf still thinks it has.
    # The pharmacy POS till. Pharmacists and cashiers operate it; the
    # accountant reads sales and registers to reconcile them, and never sells.
    "/api/sales/": [PH, CASH, ACC],
    "/api/sales/summary/": [PH, CASH, ACC],
    "/api/sales/returns/": [PH, CASH, ACC],     # the POS returns register
    "/api/sales/products/": [PH, CASH],
    "/api/sales/hold/": [PH, CASH],
    "/api/sales/complete/": [PH, CASH],
    "/api/pos-registers/": [PH, CASH, ACC],
    "/api/pos-registers/current/": [PH, CASH],
    "/api/pos-registers/open/": [PH, CASH],
    # The CSV stock count has the count sheet's authority.
    "/api/stock-counts/export/": [PH, INV],
    "/api/stock-count-imports/": [PH, INV],
}


def api_paths():
    """Every argument-free `/api/` path in the URLconf."""
    flat = []

    def walk(patterns, prefix=""):
        for pattern in patterns:
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns, prefix + str(pattern.pattern))
            else:
                flat.append(prefix + str(pattern.pattern))

    walk(get_resolver().url_patterns)
    found = set()
    for raw in flat:
        if not raw.startswith("api/"):
            continue
        if "(?P<" in raw or "<" in raw:
            continue  # detail routes need an object; covered by their own tests
        found.add("/" + raw.replace("^", "").replace("$", ""))
    # Logging in is the one thing that must work without credentials.
    found -= {"/api/auth/login/", "/api/auth/logout/"}
    return sorted(found)


class AnonymousIsLockedOut(TestCase):
    def test_no_api_endpoint_answers_an_unauthenticated_caller(self):
        leaks, crashes = [], []
        for path in api_paths():
            for method in ("get", "post"):
                try:
                    status = getattr(self.client, method)(path, HTTP_HOST="localhost").status_code
                except Exception as exc:  # a permission check that raises is a 500
                    crashes.append(f"{method.upper()} {path}: {type(exc).__name__}: {exc}")
                    continue
                if status >= 500:
                    crashes.append(f"{method.upper()} {path}: {status}")
                elif status not in (401, 403, 405):
                    leaks.append(f"{method.upper()} {path}: {status}")
        self.assertEqual(crashes, [], "Unauthenticated request crashed instead of being refused")
        self.assertEqual(leaks, [], "Reachable without logging in")


class RoleMatrix(TestCase):
    """Every role against every endpoint, compared with REACHABLE."""

    @classmethod
    def setUpTestData(cls):
        cls.roles = [value for value, _ in Role.choices]
        cls.tokens = {}
        for role in cls.roles:
            user = User.objects.create_user(username=f"perm_{role}", password="x", role=role)
            cls.tokens[role] = Token.objects.create(user=user).key

    def _reachable_roles(self, path):
        reached = []
        for role in self.roles:
            response = self.client.get(
                path, HTTP_HOST="localhost",
                HTTP_AUTHORIZATION=f"Token {self.tokens[role]}",
            )
            # 405 means the endpoint exists and this caller got past the
            # permission class; only 403 is a refusal.
            if response.status_code != 403:
                reached.append(role)
        return reached

    def test_every_endpoint_is_declared(self):
        undeclared = [p for p in api_paths() if p not in REACHABLE]
        self.assertEqual(
            undeclared, [],
            "New endpoint with no entry in REACHABLE — decide who it is for, "
            "then add it to the table in this file.",
        )

    def test_roles_reach_only_what_the_table_allows(self):
        wrong = []
        for path in api_paths():
            expected = REACHABLE.get(path)
            if expected is None:
                continue  # reported by test_every_endpoint_is_declared
            actual = set(self._reachable_roles(path))

            missing_admin = ALWAYS - actual
            if missing_admin:
                wrong.append(f"{path}: admin refused ({sorted(missing_admin)})")

            actual -= ALWAYS
            if expected == EVERYONE:
                shut_out = set(self.roles) - ALWAYS - actual
                if shut_out:
                    wrong.append(f"{path}: expected every role, refused {sorted(shut_out)}")
                continue

            wanted = set(expected)
            extra = actual - wanted
            short = wanted - actual
            if extra:
                wrong.append(f"{path}: reachable by {sorted(extra)}, which the table does not allow")
            if short:
                wrong.append(f"{path}: table allows {sorted(short)} but they are refused")

        self.assertEqual(wrong, [], "\n".join(wrong))
