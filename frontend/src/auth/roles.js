// The role groups the guards are built from — the mirror of
// `apps/accounts/permissions.py`'s groups on the backend. A page and the
// endpoint behind it must agree about who may open it, so both name the
// same set rather than each retyping a list.
//
// The frontend guard is a courtesy, not a control: it keeps a role off a page
// that would only 403 at them. The API is what actually refuses, and
// `apps/core/tests/test_api_permissions.py` is what proves it.

export const ADMIN_ROLES = ["admin", "hospital_admin"];

// The Super Admin alone. Mirrors SUPER_ADMIN_ROLES — `Role.ADMIN` is spelled
// "Super Admin" in the role list, with `hospital_admin` the ordinary
// administrator beside it. It guards the one irreversible action there is:
// permanently deleting a patient. `hasRole` is deliberately not used for this
// check — it lets every ADMIN_ROLE through by design, which is the opposite of
// what this group means.
export const SUPER_ADMIN_ROLES = ["admin"];

export function isSuperAdmin(user) {
  return SUPER_ADMIN_ROLES.includes(user?.role ?? "");
}

// Who may look a patient up at all — a name and a file number, not a chart.
// Mirrors PATIENT_LOOKUP_ROLES.
export const PATIENT_LOOKUP_ROLES = [
  "reception", "doctor", "nurse", "maternity_nurse", "pharmacist", "laboratory",
  "radiology", "optometrist", "ophthalmologist", "cashier", "accountant",
  "ward_manager",
];

// Who reads a chart — the health record, notes, the overview — and who
// prescribes and refers. Mirrors CLINICIAN_ROLES: the general doctor and the
// eye doctor (`ophthalmologist`), each reaching only their own patients.
export const CLINICAL_ROLES = ["doctor", "ophthalmologist"];

// Who works a ward. Mirrors WARD_ROLES.
export const WARD_ROLES = ["ward_manager", "doctor", "nurse"];

// Who works maternity, and who works the maternity desk. Mirrors
// MATERNITY_ROLES / MATERNITY_DESK_ROLES — a midwife is a `nurse` here and a
// maternity doctor is a `doctor`, so these are groups rather than new roles.
export const MATERNITY_ROLES = ["doctor", "nurse", "maternity_nurse"];
export const MATERNITY_DESK_ROLES = [...MATERNITY_ROLES, "reception"];
// Who may put a mother in Maternity's care and name the midwife responsible
// for her. Mirrors MATERNITY_ASSIGN_ROLES; a maternity nurse is deliberately
// not on it (she claims unassigned work, she does not hand patients over).
export const MATERNITY_ASSIGN_ROLES = [...ADMIN_ROLES, "reception"];
// Who does nursing work — records vitals and nursing notes, and hands a
// patient to a doctor. Mirrors `accounts.permissions.NURSING_ROLES`: the
// triage nurse, and the midwife doing the same job on the labour ward.
export const NURSING_ROLES = ["nurse", "maternity_nurse"];

// **Where a member of staff is authorised to work**, read off `/auth/me/`'s
// `authorized_departments` — the `Department.staff` relation an administrator
// sets (`accounts/departments.py`).
//
// It is a **courtesy**, exactly like every role guard here: it keeps somebody
// off a page that would only refuse them, and it keeps the nav honest. The
// backend asks `works_in(request.user, …)` against the database on every
// department-sensitive request, so editing this list in the browser changes
// what this screen offers and nothing at all about what the server allows.
export function authorizedDepartments(user) {
  return user?.authorized_departments ?? [];
}

/** Is this user authorised in the department with `code`? */
export function worksIn(user, code) {
  if (!code) return false;
  const wanted = String(code).toLowerCase();
  return authorizedDepartments(user).some(
    (d) => String(d.code).toLowerCase() === wanted
           || String(d.name).toLowerCase() === wanted,
  );
}

/**
 * Role **and** department, which is what authorisation is in this
 * application — never one of the two. A doctor authorised for Maternity is a
 * doctor in Maternity; a cashier posted there is still a cashier.
 *
 * `always` names the roles that *are* the department by definition and need
 * no posting — the midwife is the labour ward, the way the backend's
 * `in_maternity_team` reads it.
 */
export function worksInAs(user, code, roles, { always = [] } = {}) {
  if (!hasRole(user, roles)) return false;
  if (always.includes(user?.role)) return true;
  return worksIn(user, code);
}

// Who works the ward for their own patients only. Mirrors
// OWN_PATIENT_WARD_ROLES — the server hides other patients' names on the bed
// board and refuses to admit, move or discharge them.
export const OWN_PATIENT_WARD_ROLES = ["ophthalmologist"];

// Who opens the bed board at all.
export const BED_BOARD_ROLES = [...WARD_ROLES, ...OWN_PATIENT_WARD_ROLES];

// Who records the eye examination on a consultation note. Mirrors
// EYE_EXAMINATION_ROLES; a general doctor's note form never shows it.
export const EYE_EXAMINATION_ROLES = ["ophthalmologist"];

// Who handles money at a counter. Mirrors BILLING_ROLES.
export const BILLING_ROLES = ["cashier", "accountant", "reception"];

// Who reads the hospital's financial report. Mirrors FINANCE_REPORT_ROLES in
// `apps/billing/views.py` — deliberately narrower than BILLING_ROLES, because
// reception bills at a window and hospital-wide revenue by department is a
// management figure.
export const FINANCE_REPORT_ROLES = ["cashier", "accountant"];

// Who may hand money back. Mirrors REFUND_ROLES — narrower than
// BILLING_ROLES, because a refund takes cash out of the drawer and reception
// bills at a window.
export const REFUND_ROLES = ["cashier", "accountant"];

// Who may cancel a service — withdraw a bill the patient no longer owes.
// Mirrors CANCEL_ROLES. Kept apart from REFUND_ROLES even while the two hold
// the same people: reaching the Service Cancellations page is never permission
// to take money out of the drawer, and Cancel & refund needs both groups.
export const CANCEL_ROLES = ["cashier", "accountant"];

// Who moves stock. Mirrors STOCK_ROLES.
export const STOCK_ROLES = ["pharmacist", "inventory_manager"];

// The pharmacy POS till. Mirrors POS_ROLES: pharmacists and cashiers open a
// register, ring up sales and take payment.
export const POS_ROLES = ["pharmacist", "cashier"];

// Who reads POS sales and registers. Mirrors POS_HISTORY_ROLES — the accountant
// reconciles the tills but never operates one.
export const POS_HISTORY_ROLES = [...POS_ROLES, "accountant"];

// Who may discount a POS sale. Mirrors POS_DISCOUNT_ROLES — rule 13's boundary;
// a pharmacist sells but never discounts.
export const POS_DISCOUNT_ROLES = ["cashier", "accountant"];

// Who may take a POS return (money out of a till). Mirrors POS_RETURN_ROLES.
export const POS_RETURN_ROLES = ["cashier"];

// Who a patient can be routed to, and so who has a queue to read. Mirrors
// `workflow.views.WORKING_ROLES` — the roles in `PURPOSE_ROLE` — which is what
// the queue's list, start and complete permissions are built from (rule 16).
// The midwife joined it when `maternity` became a route purpose; her own
// station is `/maternity`, and `work_routes_for` is what scopes either page to
// the work actually sent to her.
export const QUEUE_ROLES = [
  "reception", "doctor", "nurse", "maternity_nurse", "laboratory", "radiology",
  "optometrist", "ophthalmologist",
];

// Roles that can open a patient's chart at all — any of them gets at least
// one tab. PatientDetail decides which.
export const CHART_ROLES = [
  ...CLINICAL_ROLES, "nurse", "pharmacist", ...BILLING_ROLES,
];

export function hasRole(user, roles) {
  const role = user?.role;
  if (!role) return false;
  if (ADMIN_ROLES.includes(role)) return true;
  return !roles || roles.includes(role);
}
