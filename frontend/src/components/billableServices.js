// One reading of the hospital's billable services, for every screen that
// offers one.
//
// The bug this module exists to close: Reception's billing panel and the
// chart's billing tab each fetched `/billing-items/` and offered whatever
// came back, so the Laboratory tab showed the *one* row the price list
// happened to hold while the doctor was ordering from sixty-six. The desk
// could not bill the test that had just been ordered, because the desk was
// reading a different book.
//
// `/billable-services/` is the backend's composed view over the catalogues
// that already define those services (`billing/catalogue.py`) — the price
// list plus the laboratory catalogue, each service once, each at its own
// configured price. Everything here is a reader of it: no list is written
// down in this file, and no price is ever computed in the browser.

export const SERVICE_QUERY_KEY = "billable-services";

// A category the desk types the amount for rather than picking it. "Other"
// is the one-off — a medical report, an ambulance run — which by definition
// has no catalogue row behind it.
export const WRITE_IN_CATEGORY = "other";

export function isWriteIn(category) {
  return category === WRITE_IN_CATEGORY;
}

/** The query TanStack Query should run for one category. */
export function serviceQuery(api, { category, search = "" } = {}) {
  const term = (search ?? "").trim();
  return {
    queryKey: [SERVICE_QUERY_KEY, category ?? "all", term],
    queryFn: () =>
      api
        .get("/billable-services/", {
          params: { ...(category ? { category } : {}), ...(term ? { search: term } : {}) },
        })
        // The endpoint is deliberately unpaginated — a picker over
        // configuration must not stop at the first page — but `results ?? data`
        // is kept so nothing breaks if that ever changes.
        .then((r) => r.data.results ?? r.data),
    staleTime: 60000,
  };
}

/**
 * Narrow a fetched list in the browser, for typing that is faster than the
 * round trip. The server filters too; this only keeps the list responsive
 * between keystrokes and must never be the only filter.
 */
export function matching(services, term) {
  const needle = (term ?? "").trim().toLowerCase();
  if (!needle) return services ?? [];
  return (services ?? []).filter(
    (service) =>
      service.name?.toLowerCase().includes(needle) ||
      service.detail?.toLowerCase().includes(needle),
  );
}

/** Group a list under its own headings, in the order the server sent it. */
export function grouped(services) {
  const groups = new Map();
  for (const service of services ?? []) {
    const heading = service.detail?.split(" · ")[0] || service.category_label || "Services";
    if (!groups.has(heading)) groups.set(heading, []);
    groups.get(heading).push(service);
  }
  return [...groups.entries()];
}

/** The figure a service is billed at — the catalogue's, never the screen's. */
export function priceOf(service) {
  return Number(service?.price ?? 0);
}

/**
 * What a chosen service is posted to `/charges/` as.
 *
 * `source_type` is the category, exactly as the counter has always posted it,
 * so department attribution (`billing/departments.py`) is untouched and no
 * new kind of charge is created.
 */
export function chargeBody(patientId, service) {
  return {
    patient: patientId,
    description: service.name,
    amount: service.price,
    source_type: service.source_type ?? service.category,
  };
}

/** The total a set of chosen services will add to a patient's bill. */
export function totalOf(services) {
  return (services ?? []).reduce((sum, service) => sum + priceOf(service), 0);
}

/**
 * Tick or untick one service in a selection, without disturbing the rest.
 *
 * Pure, so the "select, unselect, select again" behaviour is a unit test
 * rather than a click-through: the same service twice is on then off, and
 * nothing else in the list moves.
 */
export function toggle(selected, service) {
  const current = selected ?? [];
  return current.some((s) => s.key === service.key)
    ? current.filter((s) => s.key !== service.key)
    : [...current, service];
}

/**
 * What a whole selection is billed as: identities only.
 *
 * The prices on the screen are for the person reading it.
 * `/charges/bill-services/` resolves every key against the catalogue and
 * prices the bill itself, so a stale tab cannot bill last week's price and a
 * request body can never be money.
 */
export function billServicesBody(patientId, selected) {
  return { patient: patientId, services: (selected ?? []).map((service) => service.key) };
}
