// The payment status of a service, rendered the one way.
//
// `billingStatus.js` decides the word and the tone; these two components are
// how it appears — a badge on a worklist row, and a panel where the unit is
// about to act on the patient. Both read the `billing` block the API sends,
// which the server renders from the charge itself (`apps/billing/status.py`),
// so a department never computes a balance and two departments can never
// disagree about one.
import { Badge } from "./ui.jsx";
import { paymentState } from "./billingStatus.js";

/**
 * The worklist badge: one word, and the figure that matters.
 *
 * Nothing is rendered when the row carries no `billing` block — a badge
 * invented from an absent field would be a claim the server never made.
 */
export function PaymentBadge({ billing, withAmount = true, className = "" }) {
  const state = paymentState(billing);
  if (!state.known) return null;
  return (
    <Badge tone={state.tone} className={className}>
      <span title={state.detail}>{withAmount ? state.summary : state.label}</span>
    </Badge>
  );
}

/**
 * The panel a unit reads before it starts: what was asked for, what it costs,
 * and where the money stands.
 *
 * Loud only where money is actually owed. A paid or waived service gets one
 * quiet line — a station that shouts on every request is a station where
 * nobody reads the shouting.
 */
export function PaymentNotice({ billing, heading, className = "" }) {
  const state = paymentState(billing);
  if (!state.known || state.status === "unbilled") return null;

  const tone = state.requiresPayment
    ? "border-red-300 bg-red-50 text-red-800"
    : state.status === "deferred"
      ? "border-sky-300 bg-sky-50 text-sky-900"
      : "border-emerald-300 bg-emerald-50 text-emerald-900";

  return (
    <div className={`min-w-0 rounded-lg border px-3 py-2 ${tone} ${className}`}>
      <p className="text-sm font-semibold">
        {heading ?? (state.requiresPayment ? "PAYMENT REQUIRED" : state.label)}
      </p>
      <p className="mt-0.5 text-sm">{state.detail}</p>
    </div>
  );
}

/**
 * The per-service breakdown — every service keeps its own amount, its own
 * paid figure and its own status, because a patient can settle one test and
 * still owe for the next. Collapsing them into one line is exactly what this
 * exists not to do.
 */
export function PaymentLines({ services, className = "" }) {
  if (!services?.length) return null;
  return (
    <ul className={`min-w-0 space-y-1.5 ${className}`}>
      {services.map((service) => (
        <li key={service.id ?? service.name}
            className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className="min-w-0 font-medium text-slate-800">{service.name}</span>
          <PaymentBadge billing={service.billing} />
        </li>
      ))}
    </ul>
  );
}
