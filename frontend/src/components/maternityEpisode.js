// The labour → delivery → newborn → postpartum decisions, out of the DOM.
//
// Phase 1's `maternityPatient.js` answers "which pregnancy is she in"; this
// answers "where in that pregnancy is she today". Both read the server's own
// records rather than deciding anything: the labour's `status`, whether a
// delivery hangs off it, how many babies it produced. `refundPolicy.js` is
// the pattern.

/** The stages of the episode, in the order they happen. */
export const BEFORE_LABOUR = "before_labour";
export const IN_LABOUR = "in_labour";
export const DELIVERED = "delivered";

export const STAGE_TITLE = {
  [BEFORE_LABOUR]: "Not in labour",
  [IN_LABOUR]: "In labour",
  [DELIVERED]: "Delivered",
};

/** The labour she is in right now, or null. */
export function openLabour(labours) {
  return (labours ?? []).find((labour) => labour.status === "in_progress") ?? null;
}

/** The most recent labour that ended in a delivery, or null. */
export function deliveredLabour(labours) {
  return (labours ?? []).find((labour) => labour.status === "delivered") ?? null;
}

/**
 * Where this pregnancy has got to.
 *
 * Read off the labour rows, so it cannot disagree with what the ward sees —
 * and `null` before anything has loaded, never "not in labour", because
 * offering to open a labour while her record loads is the same mistake the
 * booking screen avoids.
 */
export function episodeStage(labours) {
  if (labours == null) return null;
  if (openLabour(labours)) return IN_LABOUR;
  if (deliveredLabour(labours)) return DELIVERED;
  return BEFORE_LABOUR;
}

/**
 * What maternity may do next.
 *
 * Exactly one path forward at a time: she cannot be admitted to a labour she
 * is not in, and a delivery cannot be recorded twice. The server refuses each
 * of these again (`labour_already_open`, `labour_not_open`), so this only
 * spares a round trip.
 */
export function episodeActions(labours) {
  const open = openLabour(labours);
  return {
    openLabour: episodeStage(labours) === BEFORE_LABOUR,
    observe: Boolean(open),
    admit: Boolean(open) && !open.admission,
    deliver: Boolean(open),
    postpartum: Boolean(deliveredLabour(labours)),
  };
}

/** "3 cm · 4/10 min · FHR 140" — only what was actually measured. */
export function observationSummary(observation) {
  return [
    observation?.cervical_dilation_cm != null
      ? `${observation.cervical_dilation_cm} cm` : null,
    observation?.contractions_per_10min != null
      ? `${observation.contractions_per_10min}/10 min` : null,
    observation?.fetal_heart_rate != null ? `FHR ${observation.fetal_heart_rate}` : null,
    observation?.membranes_label || null,
  ].filter(Boolean).join(" · ");
}

/** Where she is lying, read through the admission the ward made. */
export function whereSheIs(labour) {
  if (!labour?.ward_name) return "Not admitted";
  return `${labour.ward_name} · Bed ${labour.bed_number}`;
}

/** The babies of a delivery, in birth order. */
export function babiesOf(delivery) {
  return [...(delivery?.newborns ?? [])].sort((a, b) => a.birth_order - b.birth_order);
}

/** "Twins", "Triplets", or the one baby's description. */
export function birthDescription(delivery) {
  const babies = babiesOf(delivery);
  if (babies.length === 0) return "No baby recorded";
  if (babies.length === 1) return "One baby";
  return { 2: "Twins", 3: "Triplets", 4: "Quadruplets" }[babies.length]
    ?? `${babies.length} babies`;
}

/**
 * What recording a delivery submits.
 *
 * Identities and findings — and **a list of babies**, which is how twins are
 * recorded: one delivery, several newborns, one pregnancy throughout.
 */
export function deliveryBody({ deliveryType, outcome, midwifeId, doctorId, babies }) {
  const body = { delivery_type: Number(deliveryType) };
  if (outcome) body.outcome = Number(outcome);
  if (midwifeId) body.midwife = Number(midwifeId);
  if (doctorId) body.doctor = Number(doctorId);
  body.newborns = (babies ?? []).map((baby, index) => ({
    birth_order: index + 1,
    sex: baby.sex,
    ...(baby.name ? { name: baby.name } : {}),
    ...(baby.birth_weight_grams ? { birth_weight_grams: Number(baby.birth_weight_grams) } : {}),
    ...(baby.apgar_1_min ? { apgar_1_min: Number(baby.apgar_1_min) } : {}),
    ...(baby.apgar_5_min ? { apgar_5_min: Number(baby.apgar_5_min) } : {}),
  }));
  return body;
}

/** A delivery needs its type and at least one baby. */
export function canRecordDelivery({ deliveryType, babies }) {
  return Boolean(deliveryType) && (babies ?? []).some((baby) => baby.sex);
}
