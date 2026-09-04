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
    "/api/users/": EVERYONE,                        # staff directory, minimal fields
    "/api/departments/": EVERYONE,
    "/api/services/": EVERYONE,
    "/api/billing-items/": EVERYONE,                # the price list, quoted from everywhere

    # --- patient identity ------------------------------------------------
    # A name and a file number, not a chart. Reception gets demographics only.
    "/api/patients/": [R, D, N, PH, LAB, RAD, OPT, OPH, CASH, ACC, WARD],

    # --- the chart: doctors only (plus admin) ----------------------------
    "/api/allergies/": [D],
    "/api/medications/": [D],
    "/api/conditions/": [D],
    "/api/devices/": [D],
    "/api/surgical-history/": [D],
    "/api/family-history/": [D],
    "/api/social-history/": [D],
    "/api/vaccinations/": [D],
    "/api/medical-tests/": [D],
    "/api/notes/": [D],
    "/api/note-amendments/": [D],

    # --- nursing ---------------------------------------------------------
    "/api/vitals/": [D, N],
    "/api/vitals/recorded-today/": [D, N],
    "/api/nursing-notes/": [D, N],

    # --- workflow --------------------------------------------------------
    "/api/visits/": [R, D, N],
    "/api/appointments/": [R, D],
    "/api/patient-routes/": [R, D, N, LAB, RAD, OPT, OPH],
    "/api/patient-routes/refer/": [R, D, N, LAB, RAD, OPT, OPH],
    "/api/patient-routes/send-to-doctor/": [R, D, N, LAB, RAD, OPT, OPH],

    # --- the ward --------------------------------------------------------
    # Was IsAuthenticated on all five: a cashier could list every inpatient,
    # admit somebody, move them between beds and discharge them.
    "/api/wards/": [WARD, D, N],
    "/api/beds/": [WARD, D, N],
    "/api/admissions/": [WARD, D, N],
    "/api/bed-transfers/": [WARD, D, N],
    "/api/discharges/": [WARD, D, N],

    # --- money -----------------------------------------------------------
    "/api/ledgers/": [CASH, ACC, R, PH],
    "/api/charges/": [CASH, ACC, R, PH],
    "/api/charges/discount-balance/": [CASH, ACC, R],
    "/api/payments/": [CASH, ACC, R, PH],
    "/api/adjustments/": [CASH, ACC, R],

    # --- pharmacy and stock ----------------------------------------------
    "/api/prescriptions/": [D, PH],
    "/api/prescriptions/bulk/": [D, PH],
    "/api/items/": [D, PH, INV],        # doctors get availability, not counts
    "/api/batches/": [PH, INV],
    "/api/stock-movements/": [PH, INV],

    # --- laboratory ------------------------------------------------------
    # The catalogue is a price list, so the desk reads it; a *result* is
    # clinical, so the orders are narrower.
    "/api/lab-tests/": [LAB, D, N, R, CASH, ACC],
    "/api/lab-tests/categories/": [LAB, D, N, R, CASH, ACC],
    "/api/lab-parameters/": [LAB, D, N, R, CASH, ACC],
    "/api/lab-parameters/reorder/": [LAB],
    "/api/lab-panels/": [LAB, D, N, R, CASH, ACC],
    "/api/lab-orders/": [LAB, D, R, CASH, ACC],
    "/api/lab-orders/worklist/": [LAB, D, R, CASH, ACC],
    "/api/lab-orders/for-route/": [LAB],

    # --- diagnostics: no page in front of it, kept to clinical roles ------
    "/api/investigations/": [D, LAB, RAD, OPT, OPH],
    "/api/investigation-orders/": [D, LAB, RAD, OPT, OPH],
    "/api/investigation-results/": [D, LAB, RAD, OPT, OPH],

    # --- admin only ------------------------------------------------------
    "/api/audit-logs/": [],
    "/api/notifications/overview/": [],
    # Not finished: SaleItem sells stock the shelf still thinks it has.
    "/api/sales/": [],
    "/api/sale-items/": [],
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
