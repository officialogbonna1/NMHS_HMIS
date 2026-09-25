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
MN = "maternity_nurse"   # the midwife — maternity, and nothing else
LAB = "laboratory"; RAD = "radiology"; OPT = "optometrist"; OPH = "ophthalmologist"
CASH = "cashier"; ACC = "accountant"; WARD = "ward_manager"; INV = "inventory_manager"

EVERYONE = "*"  # any authenticated user, whatever their role

# Which non-admin roles get a non-403 answer from a GET on each endpoint.
#: Paths the **Super Admin alone** reaches — `IsSuperAdmin`, not `ADMIN_ROLES`.
#: `hospital_admin` is the ordinary administrator and is refused there, which is
#: the boundary rule 37 draws on permanently deleting a patient. Listed rather
#: than inferred so that adding one is a decision somebody wrote down: the
#: matrix below assumes both administrators reach everything, and this is the
#: only sanctioned way for that to be untrue.
#:
#: The Admin Discharge workspace used to be here and deliberately is not any
#: more: a discharge is a record that is written once and keeps its reference,
#: its audit row and its letter, so running the wards is ordinary hospital
#: administration rather than an irreversible act.
SUPER_ADMIN_ONLY = set()

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
    # Every configured billable service, composed over the price list and the
    # laboratory catalogue (`billing/catalogue.py`). It carries the lab
    # catalogue's names, so it is drawn no wider than the roles that already
    # read `/api/lab-tests/`, plus the desks that bill. The pharmacy works a
    # different catalogue and is deliberately not on it.
    "/api/billable-services/": [R, D, N, LAB, RAD, OPT, OPH, CASH, ACC],
    # Billing several of them at once. The same desks that may raise one
    # charge — BILLING_ROLES, through ChargeViewSet's own permissions.
    "/api/charges/bill-services/": [R, CASH, ACC],
    # Configuration everyone reads and an admin writes: the letterhead on
    # every printed document, and whether a category of notification is sent.
    "/api/hospital-settings/": EVERYONE,
    "/api/hospital-settings/current/": EVERYONE,
    "/api/notification-settings/": EVERYONE,

    # --- patient identity ------------------------------------------------
    # A name and a file number, not a chart. Reception gets demographics only.
    # The midwife looks a mother up like every other clinical role
    # (PATIENT_LOOKUP_ROLES). Reading a name is not reading a chart.
    "/api/patients/": [R, D, N, MN, PH, LAB, RAD, OPT, OPH, CASH, ACC, WARD],

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
    # Read by the clinicians; recorded by `NURSING_ROLES` — the triage nurse
    # and the midwife, who does the same job on the labour ward and could not
    # take a blood pressure at all while this said `IsNurse`. What each of them
    # reads *back* is still the viewset's queryset: the general nurse her own
    # readings (rule 11, unchanged), the midwife and the clinicians the
    # patients on their own list.
    "/api/vitals/": [D, N, MN, OPH],
    "/api/vitals/recorded-today/": [D, N, MN, OPH],
    "/api/nursing-notes/": [D, N, MN, OPH],

    # --- workflow --------------------------------------------------------
    "/api/visits/": [R, D, N],
    # Reception queues; the provider works their own queue. The provider
    # roles are `appointments.booking.PROVIDER_ROLES` — every role a bookable
    # service's category maps to — so an eye doctor can accept an eye
    # appointment. A nurse is absent: vitals is not a billing category, so a
    # nurse is routed a patient rather than booked one.
    # Maternity, and the line the front desk does not cross.
    #
    # Reception is on exactly four of these: the lookup (is she known, is she
    # pregnant right now), the episode behind that answer, the ward's patient
    # list and its staff list — plus the two configuration reads. That is the
    # front desk's job, and it is where the front desk stops.
    #
    # Everything **clinical** is MATERNITY_ROLES alone: the encounter (a
    # clinician's own summary of an attendance), the labour and its partogram,
    # the delivery, the babies and the postpartum course. Reception used to
    # read every one of them, which put a partogram on the front-desk screen.
    "/api/maternity/lookup/": [R, D, N, MN],
    "/api/pregnancies/": [R, D, N, MN],
    # The ward's patient list — what the maternity picker opens onto. The
    # scope is `patients.access.patient_queryset_for` and is the server's: a
    # midwife reaches Maternity's patients and no others.
    "/api/maternity/patients/": [R, D, N, MN],
    # Who may be named as the responsible midwife. A name, not a record.
    "/api/maternity/staff/": [R, D, N, MN],
    # Putting a mother in Maternity's care and naming her midwife —
    # MATERNITY_ASSIGN_ROLES for the writes (administration and the desk that
    # already assigns a route), the desk group for the read. A maternity nurse
    # is deliberately not a writer: claiming unassigned work is hers through
    # the queue, handing a patient to a named colleague is not.
    "/api/maternity/assignment/": [R, D, N, MN],
    "/api/maternity-visit-types/": [R, D, N, MN],
    "/api/maternity-options/": [R, D, N, MN],
    "/api/maternity-encounters/": [D, N, MN],
    # The reasons a maternity record may be corrected for, so the form never
    # hard-codes them. A list of labels, read by everyone who may open the
    # record; `may_amend` is what decides who may actually correct one.
    # Reception is absent: it reads the pregnancy list (is she pregnant?) and
    # corrects nothing, so it has no use for the reasons a correction is given
    # for. The action falls to the viewset's write group, which is right.
    "/api/pregnancies/amendment-reasons/": [D, N, MN],
    "/api/maternity-encounters/amendment-reasons/": [D, N, MN],
    "/api/labour-episodes/amendment-reasons/": [D, N, MN],
    "/api/deliveries/amendment-reasons/": [D, N, MN],
    "/api/labour-episodes/": [D, N, MN],
    "/api/deliveries/": [D, N, MN],
    "/api/newborns/": [D, N, MN],
    # The midwife joined `PROVIDER_ROLES` when the hospital configured
    # Maternity's price list: that list is "every role a bookable category
    # maps to", Maternity became a billing category, and `PURPOSE_ROLE`
    # already named her. She reads the queue *scoped to herself*
    # (`filter(doctor=user)`) and still cannot create or edit one.
    "/api/appointments/": [R, D, MN, LAB, RAD, OPT, OPH],
    # The booking form's own read: which units take appointments, what they
    # offer, at what fee, and who may be named. Reception books, so reception
    # reads it.
    "/api/appointments/booking-options/": [R],
    # The midwife joined these when `maternity` became a route purpose: rule
    # 16's "extend PURPOSE_ROLE and the queue's list/start/complete
    # permissions follow". `work_routes_for` is what scopes it — she lists
    # maternity routes and nothing else — and the write actions here are
    # refused by their own narrower groups (`refer` is CLINICIAN_ROLES,
    # `send-to-doctor` is the general nurse's).
    "/api/patient-routes/": [R, D, N, MN, LAB, RAD, OPT, OPH],
    "/api/patient-routes/refer/": [R, D, N, MN, LAB, RAD, OPT, OPH],
    # The shape of a unit's report, read by whoever works the queue — it is a
    # field list, not a record.
    "/api/patient-routes/report-fields/": [R, D, N, MN, LAB, RAD, OPT, OPH],
    "/api/patient-routes/send-to-doctor/": [R, D, N, MN, LAB, RAD, OPT, OPH],

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

    # The Admin Discharge workspace: the same DischargeSummary rows and the
    # same `inpatient/services.discharge_patient`, reached by **both**
    # administrators (`IsAdmin`). Empty here because `admin` and
    # `hospital_admin` are excluded from this table by definition and both now
    # pass — which `apps/inpatient/tests/test_admin_discharge.py` asserts
    # directly, for each of them. No clinical role is on it, and the ward's own
    # endpoint above is deliberately unchanged.
    "/api/admin-discharges/": [],
    "/api/admin-discharges/dischargeable/": [],
    "/api/admin-discharges/discharge/": [],

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

            # Both administrators reach everything — except the handful of
            # paths the Super Admin keeps to itself, which are asserted the
            # other way round below.
            expected_admins = {"admin"} if path in SUPER_ADMIN_ONLY else ALWAYS
            missing_admin = expected_admins - actual
            if missing_admin:
                wrong.append(f"{path}: admin refused ({sorted(missing_admin)})")
            if path in SUPER_ADMIN_ONLY and "hospital_admin" in actual:
                wrong.append(f"{path}: hospital_admin reaches a Super Admin path")

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
