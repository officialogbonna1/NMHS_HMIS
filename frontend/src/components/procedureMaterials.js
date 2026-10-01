// The materials a procedure record documents — the pure half, so the rule can
// be tested without a DOM (the `refundPolicy.js` pattern).
//
// The server is what decides (`workflow/procedures.clean_materials`); this only
// tells the person at the bedside before they press Save, so a figure that does
// not add up is caught while the gauze count is still in front of them. It
// never corrects a number.
//
//   received = used + remaining + wastage
//
// Where `received` is blank there is nothing to balance; used / remaining /
// wastage are simply recorded. A blank counts as zero in the sum.

export const QUANTITY_KEYS = ["quantity_received", "quantity_used", "quantity_remaining", "wastage"];

export function emptyRow() {
  return { name: "", category: "", unit: "", quantity_received: "", quantity_used: "",
           quantity_remaining: "", wastage: "", notes: "" };
}

function amount(value) {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : NaN;
}

/** A row nobody has typed anything into — dropped rather than sent. */
export function isBlank(row) {
  return Object.values(row ?? {}).every((v) => String(v ?? "").trim() === "");
}

/**
 * What is wrong with one row, or null. The wording matches the server's, so
 * the message the person sees is the same whichever side caught it.
 */
export function rowProblem(row) {
  if (isBlank(row)) return null;
  if (!String(row.name ?? "").trim()) return "Name the item or material.";
  const values = Object.fromEntries(QUANTITY_KEYS.map((key) => [key, amount(row[key])]));
  if (Object.values(values).some((v) => Number.isNaN(v))) return "Quantities must be numbers.";
  if (Object.values(values).some((v) => v !== null && v < 0)) return "Quantities cannot be negative.";
  if (Object.values(values).every((v) => v === null)) return "Record how much was used (or received).";
  if (values.quantity_received !== null) {
    // Cents, so 4.5 + 0.5 is 5 and not 4.999….
    const cents = (v) => Math.round((v ?? 0) * 100);
    const accounted = cents(values.quantity_used) + cents(values.quantity_remaining) + cents(values.wastage);
    if (accounted !== cents(values.quantity_received)) {
      return `Received (${values.quantity_received}) must equal used + remaining + wastage (${accounted / 100}).`;
    }
  }
  return null;
}

/** The rows as the server takes them: blank rows dropped, every field trimmed. */
export function materialsBody(rows) {
  return (rows ?? []).filter((row) => !isBlank(row)).map((row) => Object.fromEntries(
    Object.entries(row).map(([key, value]) => [key, String(value ?? "").trim()])
      .filter(([, value]) => value !== ""),
  ));
}
