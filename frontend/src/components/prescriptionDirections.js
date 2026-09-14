// How a prescription is to be taken, said once.
//
// A script carries the dose (`dosage_instructions`) and, beside it, the
// frequency, duration, route and notes. The pharmacy queue, the chart, the
// printed script and the dispensing note all print them, so the joining lives
// here rather than as four slightly different template strings.

/** Mirrors `Prescription.ROUTE_CHOICES` in `apps/pharmacy/models.py`. */
export const ROUTES = [
  ["oral", "Oral"], ["sublingual", "Sublingual"], ["iv", "Intravenous (IV)"],
  ["im", "Intramuscular (IM)"], ["sc", "Subcutaneous (SC)"], ["topical", "Topical"],
  ["inhaled", "Inhaled"], ["nasal", "Nasal"], ["ophthalmic", "Eye (ophthalmic)"],
  ["otic", "Ear (otic)"], ["rectal", "Rectal"], ["vaginal", "Vaginal"], ["other", "Other"],
];

const ROUTE_LABEL = Object.fromEntries(ROUTES);

/** Suggestions only — frequency is free text, because a prescriber writes what they mean. */
export const FREQUENCIES = [
  "Once daily", "Twice daily", "Three times daily", "Four times daily",
  "Every 8 hours", "Every 12 hours", "At night", "As needed",
];

const clean = (value) => String(value ?? "").trim();

/** "1 tablet · Three times daily · for 5 days · Oral" — only the parts that were written. */
export function directionsOf(prescription) {
  if (!prescription) return "";
  const duration = clean(prescription.duration);
  const route = clean(prescription.route_label) || ROUTE_LABEL[prescription.route] || "";
  return [
    clean(prescription.dosage_instructions),
    clean(prescription.frequency),
    duration && !/^for\b/i.test(duration) ? `for ${duration}` : duration,
    route,
  ].filter(Boolean).join(" · ");
}

/** "Amoxicillin · 500 mg · Capsule" — a product with what the catalogue knows of it. */
export function productLabel(item, name = item?.name) {
  return [clean(name), clean(item?.strength), clean(item?.dosage_form)].filter(Boolean).join(" · ");
}
