// What a service's payment status is called, and what the screen should do
// about it. One module, because five places ask the same question — the
// laboratory bench, the imaging station, the eye clinic, the doctor's chart
// and the Service Cancellations desk — and a bench reading "Part paid" while
// the desk reads "PARTIALLY PAID" for the same ₦3,000 is how a patient gets
// sent back and forth.
//
// It decides nothing about the money. Every figure here comes off the
// `billing` block the API sends, which `apps/billing/status.py` renders from
// the charge itself — one authoritative balance, computed in one place. This
// is the vocabulary and the tone, nothing more.
//
// It is a *reading*, never a gate: the laboratory runs a sample that has been
// drawn and imaging scans the patient on the couch (rules 24 and 51). What a
// unit gets from this is that it knows rather than assumes.

/** The word each state wears, matching `billing.status.LABELS` on the server. */
export const PAYMENT_LABEL = {
  unbilled: "NOT BILLED",
  unpaid: "UNPAID",
  partial: "PARTIALLY PAID",
  // An alias the API has never sent, kept because a cached row written by an
  // older build might. One vocabulary means one answer for both spellings.
  part_paid: "PARTIALLY PAID",
  paid: "PAID",
  // A waiver is not an unpaid bill. Showing "UNPAID ₦3,500" against a service
  // the hospital has written off sends the patient to a counter that has
  // nothing to collect from them.
  waived: "NO PAYMENT REQUIRED",
  deferred: "PAY LATER",
  cancelled: "CANCELLED",
};

/** `ui.jsx` Badge tones. Only money still owed is loud. */
export const PAYMENT_TONE = {
  unbilled: "neutral",
  unpaid: "danger",
  partial: "warning",
  part_paid: "warning",
  paid: "success",
  waived: "violet",
  deferred: "info",
  cancelled: "neutral",
};

const naira = (value) => `₦${Number(value ?? 0).toLocaleString("en-NG", {
  minimumFractionDigits: 0, maximumFractionDigits: 2,
})}`;

export { naira as formatNaira };

/**
 * Everything a screen needs to render one service's payment position.
 *
 * Returns `{ known, status, label, tone, requiresPayment, billed, amount,
 * paid, outstanding, deferred, summary, detail }`.
 *
 *   known           — was a `billing` block sent at all (an older cached row
 *                     or an endpoint that does not carry one sends none, and
 *                     a badge invented from nothing would be a lie)
 *   requiresPayment — money is owed and nobody has authorised proceeding.
 *                     Read from the server's own `requires_payment` where it
 *                     is sent, derived from the status where it is not.
 *   summary         — "PAID · ₦8,000" / "UNPAID · ₦8,000 outstanding"
 *   detail          — the sentence under a heading: what is owed, and that
 *                     the counter is where it is settled.
 */
export function paymentState(billing) {
  if (!billing) return { known: false, status: null, label: "", tone: "neutral",
                         requiresPayment: false, billed: false, amount: 0, paid: 0,
                         outstanding: 0, deferred: false, summary: "", detail: "" };

  const status = billing.status ?? "unbilled";
  const amount = Number(billing.amount ?? billing.total ?? 0);
  const paid = Number(billing.paid ?? 0);
  const outstanding = Number(billing.outstanding ?? 0);
  const requiresPayment = billing.requires_payment !== undefined
    ? Boolean(billing.requires_payment)
    : ["unpaid", "partial", "part_paid"].includes(status);
  const label = PAYMENT_LABEL[status] ?? String(status).toUpperCase();

  let summary;
  let detail;
  switch (status) {
    case "unbilled":
      summary = label;
      detail = "No charge was raised for this request, so there is nothing to collect.";
      break;
    case "waived":
      summary = label;
      detail = `${naira(amount)} was written off. The patient owes nothing for this.`;
      break;
    case "cancelled":
      summary = label;
      detail = "This bill was withdrawn. The patient owes nothing for it.";
      break;
    case "paid":
      summary = `${label} · ${naira(paid || amount)}`;
      detail = `${naira(paid || amount)} settled in full. Nothing outstanding.`;
      break;
    case "deferred":
      summary = `${label} · ${naira(outstanding)}`;
      detail = `${naira(outstanding)} is still owed, but the patient has been authorised to settle it afterwards.`;
      break;
    case "partial":
    case "part_paid":
      summary = `${label} · ${naira(outstanding)} outstanding`;
      detail = `${naira(paid)} of ${naira(amount)} paid — ${naira(outstanding)} still owed at Reception or the cash desk.`;
      break;
    default:
      summary = `${label} · ${naira(outstanding || amount)}`;
      detail = `${naira(outstanding || amount)} is owed. The patient settles it at Reception or the cash desk.`;
  }

  return {
    known: true, status, label, tone: PAYMENT_TONE[status] ?? "neutral",
    requiresPayment, billed: Boolean(billing.billed), amount, paid, outstanding,
    deferred: Boolean(billing.deferred), summary, detail,
  };
}

/**
 * What the unit is warned about before it marks an unpaid request done.
 *
 * The server asks the same question and is what actually decides
 * (`workflow.views._bill_outstanding`, answering `code: "payment_outstanding"`
 * on the first attempt). This only spares the station a round trip to be told
 * something the screen already knew — and, like every other early refusal in
 * this application, it loses the argument with the server rather than winning
 * it.
 */
export function needsPaymentAcknowledgement(billing) {
  return paymentState(billing).requiresPayment;
}
