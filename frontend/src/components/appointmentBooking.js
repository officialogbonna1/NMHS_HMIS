// The booking form's decisions, lifted out of the DOM.
//
// Every list this module narrows comes from one server read —
// `GET /api/appointments/booking-options/` — which answers with the units
// that take appointments, the services each offers at the catalogue's own
// price, and who may be named on each. **Nothing here is a list of
// departments, services, providers or prices**: they are all configuration,
// and a copy kept in JavaScript is a copy that goes stale the day somebody
// ticks a service in Django admin.
//
// What this file holds is the *shape* of the form: what becomes selectable
// once something else is chosen, what a change has to clear behind it, and
// what the summary says. `refundPolicy.js` is the pattern — a pure module the
// page renders, and a plain unit test beside it.
//
// It refuses early only to spare a round trip. `POST /api/appointments/`
// re-resolves the service and re-checks the provider against the same rule
// (`appointments/booking.py`), so a stale screen loses that argument rather
// than winning it.

import { naira } from "./ui.jsx";

/**
 * The value `serviceKey` holds when reception has deliberately chosen *not*
 * to name a service — the general consultation that reception has always
 * booked. Distinct from "" (nothing chosen yet), because the two mean
 * different things on screen: one offers a provider, the other waits.
 *
 * It is never submitted. `bookingBody` drops it, so the request is the
 * patient, the provider and the reason, exactly as it has always been.
 */
export const GENERAL = "general";

/** The departments on offer, from the server's payload. */
export function departmentsOf(options) {
  return options?.departments ?? [];
}

/**
 * The fast path the server describes: which department a general consultation
 * belongs to, and who may take one. Read rather than worked out here — a
 * department code spelled into JavaScript is a copy of configuration.
 */
export function generalOf(options) {
  return options?.general ?? { department: null, providers: [], label: "General consultation" };
}

/** Whether the general consultation is offered under this department. */
export function allowsGeneral(options, departmentId) {
  const general = generalOf(options);
  return general.department != null && String(general.department) === String(departmentId);
}

/** One department's services, or none until a department is chosen. */
export function servicesFor(options, departmentId) {
  if (departmentId === "" || departmentId == null) return [];
  const department = departmentsOf(options).find((d) => String(d.id) === String(departmentId));
  return department?.services ?? [];
}

/** The service a key names, within the chosen department. */
export function serviceFor(options, departmentId, serviceKey) {
  if (!serviceKey || serviceKey === GENERAL) return null;
  return servicesFor(options, departmentId).find((s) => s.key === serviceKey) ?? null;
}

/**
 * Who may be named — the server's own answer either way, so the dropdown and
 * the API's refusal are built from one rule.
 *
 * A named service carries its own eligible people; the general consultation
 * carries the hospital's consultation providers, which is the list the old
 * form fetched as `/users/?role=doctor`.
 */
export function providersFor(service, options, serviceKey) {
  if (serviceKey === GENERAL) return generalOf(options).providers ?? [];
  return service?.providers ?? [];
}

/**
 * Who may be named on a basket of services.
 *
 * Services in one department share a category and therefore an eligible list,
 * so this is the first one's — and it is *intersected* with the rest, so a
 * department that ever mixed categories would narrow rather than over-offer.
 * The server checks every service again (`booking.refusal_for`) and is what
 * actually decides.
 */
export function providersForAll(selected, options, serviceKey) {
  if (serviceKey === GENERAL) return generalOf(options).providers ?? [];
  if (!selected?.length) return [];
  const [first, ...rest] = selected;
  return (first.providers ?? []).filter((person) =>
    rest.every((service) => (service.providers ?? []).some((p) => p.id === person.id)));
}

/**
 * Tick or untick one service, leaving the rest where they were.
 *
 * The same decision `billableServices.toggle` makes for the billing counter,
 * kept here because an appointment service row is a different shape — but the
 * rule is the identical one: selection is a set keyed by the catalogue key,
 * and toggling is its own inverse.
 */
export function toggleService(selected, service) {
  const already = (selected ?? []).some((s) => s.key === service.key);
  return already
    ? (selected ?? []).filter((s) => s.key !== service.key)
    : [...(selected ?? []), service];
}

/** What the ticked services come to. The screen's figure; the server re-prices. */
export function totalFee(selected) {
  return (selected ?? []).reduce((sum, service) => sum + Number(service.fee ?? 0), 0);
}

/**
 * The selected rows in the shape `SelectedServices` renders — the chips,
 * the count and the running total the billing counter and the pharmacy till
 * already use. `price` is that component's key for a figure; `fee` is this
 * API's. Mapped here rather than duplicated in the payload.
 */
export function asChips(selected) {
  return (selected ?? []).map((service) => ({ ...service, price: service.fee }));
}

/**
 * What a change at one step leaves valid at the steps below it.
 *
 * Choosing a different department cannot leave last department's service
 * selected, and choosing a different service cannot leave a provider who is
 * not eligible for it — which is the one that actually bites, because the two
 * lists overlap (an ophthalmologist appears under more than one eye service).
 */
export function afterDepartmentChange(selection, departmentId) {
  return { ...selection, departmentId, serviceKey: "", services: [], providerId: "" };
}

/**
 * Tick or untick a service, and keep the provider only if they can still do
 * everything that is ticked.
 */
export function afterServiceToggle(options, selection, service) {
  const services = toggleService(selection.services, service);
  const stillEligible = providersForAll(services, options, "")
    .some((p) => String(p.id) === String(selection.providerId));
  return {
    ...selection, services, serviceKey: "",
    providerId: stillEligible ? selection.providerId : "",
  };
}

/** Switch between the general consultation and picking services. */
export function afterModeChange(selection, serviceKey) {
  return { ...selection, serviceKey, services: [], providerId: "" };
}

/**
 * The fee, and what the hospital does about it — two different things, kept
 * apart deliberately.
 *
 * A fee is what the service costs. It is **not** a payment, not a charge that
 * has been settled, and not a gate on the appointment: the bill is raised when
 * the appointment is queued and the patient settles it through the existing
 * billing counter, exactly as they settle a laboratory test ordered by a
 * doctor. So this says what it will cost and where it is paid, and never
 * offers to take money.
 */
export function feeFor(selected) {
  const services = Array.isArray(selected) ? selected : (selected ? [selected] : []);
  if (!services.length) {
    return { known: false, billable: false, amount: 0, count: 0, label: "", note: "" };
  }
  const amount = totalFee(services);
  const billable = amount > 0;
  return {
    known: true,
    billable,
    amount,
    count: services.length,
    label: billable ? naira(amount) : "No charge",
    note: billable
      ? `${services.length > 1 ? "One bill per service is" : "A bill is"} raised when the `
        + "appointment is queued. The patient settles it at Reception or the cash desk, "
        + "like any other charge."
      : "Nothing here is priced, so queueing it raises no bill.",
  };
}

/** Whether the form has everything the server needs. */
export function canQueue(selection) {
  if (!selection?.patient || !selection?.providerId) return false;
  // A department was picked but nothing in it was ticked: the general
  // consultation is the one booking that names no service.
  if (selection.serviceKey === GENERAL) return true;
  return Boolean(selection.services?.length);
}

/**
 * What the form submits.
 *
 * Identities only — patient, provider, service key, reason. **No amount**, no
 * department and no charge: the server resolves the price from the catalogue,
 * the department from the service, and raises the bill itself. A figure in a
 * request body is not money in this system (rule 52), and the same is true of
 * a department: sending one would be inviting the appointment and its bill to
 * name two different units.
 */
export function bookingBody(selection) {
  const body = {
    patient: selection.patient?.id,
    doctor: Number(selection.providerId),
    reason: (selection.reason ?? "").trim(),
  };
  // `GENERAL` is a screen state, never a service: a general consultation is
  // submitted the way it always was, with no service at all.
  // Identities, and as many of them as were ticked. `GENERAL` is a screen
  // state rather than a service: a general consultation is submitted the way
  // it always was, with no service at all.
  const keys = (selection.services ?? []).map((service) => service.key);
  if (selection.serviceKey !== GENERAL && keys.length) {
    body.service = keys.length === 1 ? keys[0] : keys;
  }
  return body;
}

/**
 * The summary, as rows a screen renders — computed here so the page cannot
 * describe the booking differently from the one it is about to submit.
 */
export function summaryRows(options, selection) {
  const selected = selection.serviceKey === GENERAL ? [] : (selection.services ?? []);
  const department = departmentsOf(options)
    .find((d) => String(d.id) === String(selection.departmentId));
  const provider = providersForAll(selected, options, selection.serviceKey)
    .find((p) => String(p.id) === String(selection.providerId));
  const fee = feeFor(selected);
  const names = selected.map((service) => service.name);
  return [
    { label: "Patient", value: selection.patient ? patientLine(selection.patient) : "—" },
    { label: "Department", value: department?.name ?? "—" },
    {
      label: names.length > 1 ? `Services (${names.length})` : "Service",
      value: names.length ? names.join(" + ") : "General consultation",
    },
    { label: "Provider", value: provider?.name ?? "—" },
    { label: "Fee", value: fee.known ? fee.label : "No charge" },
    { label: "Payment", value: fee.billable ? "Existing billing workflow" : "Nothing to collect" },
  ];
}

function patientLine(patient) {
  const name = [patient.last_name, patient.first_name].filter(Boolean).join(", ")
    || patient.display_name || patient.name || "";
  return [name, patient.patient_number].filter(Boolean).join(" · ");
}
