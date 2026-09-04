// The role groups the guards are built from — the mirror of
// `apps/accounts/permissions.py`'s groups on the backend. A page and the
// endpoint behind it must agree about who may open it, so both name the
// same set rather than each retyping a list.
//
// The frontend guard is a courtesy, not a control: it keeps a role off a page
// that would only 403 at them. The API is what actually refuses, and
// `apps/core/tests/test_api_permissions.py` is what proves it.

export const ADMIN_ROLES = ["admin", "hospital_admin"];

// Who may look a patient up at all — a name and a file number, not a chart.
// Mirrors PATIENT_LOOKUP_ROLES.
export const PATIENT_LOOKUP_ROLES = [
  "reception", "doctor", "nurse", "pharmacist", "laboratory", "radiology",
  "optometrist", "ophthalmologist", "cashier", "accountant", "ward_manager",
];

// Who reads a chart: the health record, notes, the overview.
export const CLINICAL_ROLES = ["doctor"];

// Who works a ward. Mirrors WARD_ROLES.
export const WARD_ROLES = ["ward_manager", "doctor", "nurse"];

// Who handles money at a counter. Mirrors BILLING_ROLES.
export const BILLING_ROLES = ["cashier", "accountant", "reception"];

// Who moves stock. Mirrors STOCK_ROLES.
export const STOCK_ROLES = ["pharmacist", "inventory_manager"];

// Who a patient can be routed to, and so who has a queue to read.
export const QUEUE_ROLES = [
  "reception", "doctor", "nurse", "laboratory", "radiology",
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
