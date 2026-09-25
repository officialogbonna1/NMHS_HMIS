// What the maternity desk is looking at, and therefore what it may do.
//
// The whole returning-patient problem in one module: is this woman known, is
// she pregnant *right now*, and which of the two actions should exist. The
// answer comes from `GET /api/maternity/lookup/` — the server decides it, so
// the screen cannot offer "start a new pregnancy" to somebody who is already
// in one, and `start_pregnancy` refuses it again if a stale screen tries.
//
// `refundPolicy.js` is the pattern: a pure module the page renders and a
// plain unit test beside it.

/** The three states a maternity desk can be in, and nothing else. */
export const NEW_PATIENT = "new_patient";
export const ACTIVE_PREGNANCY = "active_pregnancy";
export const NO_ACTIVE_PREGNANCY = "no_active_pregnancy";

/**
 * Which of the three it is.
 *
 * Read off the server's answer rather than worked out from the absence of a
 * key, so a payload that has not arrived yet is "nothing chosen" rather than
 * "new patient" — offering to register a woman because her record is still
 * loading is exactly the mistake this screen exists to prevent.
 */
export function deskState(lookup) {
  if (!lookup) return null;
  if (!lookup.known) return NEW_PATIENT;
  return lookup.active_pregnancy ? ACTIVE_PREGNANCY : NO_ACTIVE_PREGNANCY;
}

/** The headline the desk reads, per state. */
export const STATE_TITLE = {
  [NEW_PATIENT]: "New patient",
  [ACTIVE_PREGNANCY]: "Existing maternity patient",
  [NO_ACTIVE_PREGNANCY]: "Existing patient — no active pregnancy",
};

/**
 * What the desk may do.
 *
 * `can_continue` and `can_start` are the server's own booleans, mirrored
 * rather than recomputed: exactly one of them is true for a known patient,
 * which is what makes the screen unambiguous.
 */
export function actionsFor(lookup) {
  return {
    register: !lookup?.known,
    continuePregnancy: Boolean(lookup?.can_continue),
    startPregnancy: Boolean(lookup?.can_start),
  };
}

/** The pregnancy she is in, or null. */
export function activePregnancy(lookup) {
  return lookup?.active_pregnancy ?? null;
}

/** Her earlier pregnancies — the active one is not repeated among them. */
export function previousPregnancies(lookup) {
  const active = activePregnancy(lookup);
  return (lookup?.history ?? []).filter((pregnancy) => pregnancy.id !== active?.id);
}

/**
 * The summary line the desk says out loud: "Pregnancy #2 · 32w 4d · EDD 12 Mar
 * 2026". Each part is dropped when the record does not hold it, because a
 * gestation invented from nothing would be a clinical statement nobody made.
 */
export function pregnancySummary(pregnancy) {
  if (!pregnancy) return "";
  return [
    `Pregnancy #${pregnancy.number}`,
    pregnancy.gestation || null,
    pregnancy.edd ? `EDD ${formatDate(pregnancy.edd)}` : null,
  ].filter(Boolean).join(" · ");
}

/** "G2 P1", where she has told the hospital. */
export function gravidaPara(pregnancy) {
  const parts = [];
  if (pregnancy?.gravida != null) parts.push(`G${pregnancy.gravida}`);
  if (pregnancy?.para != null) parts.push(`P${pregnancy.para}`);
  return parts.join(" ");
}

/** When she was last seen in this pregnancy, in words. */
export function lastSeen(pregnancy) {
  const last = pregnancy?.last_encounter;
  if (!last) return "No visits recorded yet";
  return `${last.type} · ${formatDate(last.seen_on)}`;
}

/** The visit types the hospital currently runs, as the server sent them. */
export function visitTypes(lookup) {
  return lookup?.visit_types ?? [];
}

/**
 * What a visit submits: the pregnancy she is in and the clinic she is
 * attending. **Never a patient and never a pregnancy** — continuing is the
 * absence of creating, which is the point.
 */
export function encounterBody({ pregnancy, visitTypeId }) {
  return { pregnancy: pregnancy?.id, visit_type: Number(visitTypeId) };
}

export function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString();
}
