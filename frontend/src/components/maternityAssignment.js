// The maternity desk's two questions about a patient, decided once.
//
//   Which department is she in?   — visibility. The ward's, never one nurse's.
//   Who is responsible for her?   — assignment. Optional, and narrows nothing.
//
// Keeping them apart here is what stops a screen implying the second answers
// the first: a mother with no midwife named is not unassigned *from the ward*,
// she is simply nobody's in particular, and every midwife still works her.
//
// A pure module with a plain unit test, the pattern `refundPolicy.js` set.
// It refuses early only to spare a round trip — the server is what actually
// decides, and `MATERNITY_ASSIGN_ROLES` is what it reads.

// Mirrors `accounts.permissions.MATERNITY_ASSIGN_ROLES` through `roles.js`,
// which is the one place a set of roles is named (rule 28). A maternity nurse
// is deliberately absent: claiming unassigned work is hers through the queue,
// handing a patient to a named colleague is the desk's.
import { MATERNITY_ASSIGN_ROLES } from "../auth/roles.js";

export const ASSIGN_ROLES = MATERNITY_ASSIGN_ROLES;

export const UNASSIGNED = "Not assigned";

export function canAssign(user) {
  return Boolean(user?.role) && ASSIGN_ROLES.includes(user.role);
}

/** Who is responsible, in the words the desk uses when nobody is. */
export function responsibleNurse(row) {
  return row?.assigned_nurse || UNASSIGNED;
}

export function responsibleDoctor(row) {
  return row?.assigned_doctor || UNASSIGNED;
}

/**
 * What the assignment panel says, given where she stands.
 *
 * **The department is the headline**, because the department is what decides
 * who can see and work her. The two names underneath are responsibility, and
 * the copy says so — "no midwife named" is a normal state, not a gap, because
 * the whole team already has her.
 */
export function assignmentState(state) {
  if (!state?.in_maternity) {
    return {
      key: "outside",
      title: "Not in Maternity",
      detail: "Assign her to the Maternity department to put her on the ward's list.",
      tone: "warning",
    };
  }
  return {
    key: "assigned",
    title: "Maternity",
    detail: "The whole maternity team can see and work with her. "
      + "The names below record who is responsible — they do not limit who has access.",
    tone: "success",
  };
}

/**
 * One responsibility row: what it is called, who holds it, and the words for
 * nobody holding it.
 *
 * `field` is what the PATCH carries, and **one row sends one field** — the
 * server refuses a body naming both, because changing the midwife must never
 * be able to move the doctor with it.
 */
export const RESPONSIBILITIES = [
  { field: "nurse", label: "Assigned Nurse", roster: "nurse",
    empty: "Not assigned",
    hint: "Optional. Every maternity nurse sees her either way." },
  { field: "doctor", label: "Assigned Doctor", roster: "doctor",
    empty: "Not assigned",
    hint: "Optional. Every maternity doctor sees her either way." },
];

/** Who currently holds `field`, as `{id, name}` or null. */
export function holderOf(state, field) {
  return (field === "doctor" ? state?.assigned_doctor : state?.assigned_nurse) ?? null;
}

/** The label on the button that changes `field`. */
export function changeLabel(state, field) {
  const holder = holderOf(state, field);
  const who = field === "doctor" ? "doctor" : "midwife";
  return holder ? `Change ${who}` : `Assign ${who}`;
}

/**
 * The body for a nurse change. `nurse` of null clears it, which is a real
 * choice and not a missing value — so it is sent rather than omitted.
 */
export function assignNurseBody(patient, nurseId) {
  return assignPersonBody(patient, "nurse", nurseId);
}

/**
 * One field per call. Never both: they are separate decisions, and a body
 * carrying the pair is refused (`one_assignment_at_a_time`) rather than
 * quietly reassigning whichever the caller did not mean to touch.
 */
export function assignPersonBody(patient, field, personId) {
  return { patient: patient?.id ?? patient, [field]: personId ? Number(personId) : null };
}

export function assignDepartmentBody(patient, nurseId) {
  const body = { patient: patient?.id ?? patient };
  if (nurseId) body.nurse = Number(nurseId);
  return body;
}

/** How a midwife is labelled in the selector — name, and what she is. */
export function staffLabel(person) {
  if (!person) return "";
  return person.role_label ? `${person.name} — ${person.role_label}` : person.name;
}
