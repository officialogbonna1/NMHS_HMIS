// Whether a payment can still be refunded, whether a service can be cancelled,
// and what the controls should say.
//
// One module, because several screens ask the same questions — the Refunds and
// Service Cancellations desks, the Billing counter, Transaction History and the
// chart's Billing tab — and a cashier must never see "Refund ₦7,000" on one and
// "Fully refunded" on another for the same row.
//
// It is a *courtesy*, never the control. `billing.services` re-reads every
// figure under `select_for_update` and refuses anything past it, so a stale
// figure here loses an argument with the server rather than winning one. What
// this buys is that the desk is not offered an action that would only fail.
import { CANCEL_ROLES, hasRole, REFUND_ROLES } from "../auth/roles.js";

/** The money figures on a payment, whatever the API happened to send. */
function figures(payment) {
  const amount = Number(payment?.amount ?? 0);
  // `refundable_balance` and `amount_refunded` are computed by the serializer.
  // An older cached row may carry neither, in which case the whole payment is
  // treated as refundable and the server settles it.
  const refunded = Number(payment?.amount_refunded ?? 0);
  const refundable = payment?.refundable_balance === undefined
    ? Math.max(amount - refunded, 0)
    : Number(payment.refundable_balance);
  return { amount, refunded, refundable };
}

/**
 * What the refund control should do for this payment and this user.
 *
 * Returns `{ visible, enabled, amount, refunded, refundable, label, reason }`:
 *
 *   visible     — is this user allowed to refund at all (REFUND_ROLES)
 *   enabled     — is there anything left to refund on this payment
 *   label       — "Refund ₦7,000", the amount spelled out so nobody has to
 *                 work out what is left before clicking
 *   reason      — why it is disabled, shown in place of the button
 */
export function refundState(payment, user, { format = (n) => n } = {}) {
  const visible = hasRole(user, REFUND_ROLES);
  const { amount, refunded, refundable } = figures(payment);

  if (!payment || !payment.id || !Number.isFinite(amount) || amount <= 0) {
    return { visible, enabled: false, amount, refunded, refundable,
             label: "Refund", reason: "Not refundable" };
  }
  if (!(refundable > 0)) {
    return { visible, enabled: false, amount, refunded, refundable,
             label: "Refund", reason: "Fully refunded" };
  }
  return {
    visible, enabled: true, amount, refunded, refundable,
    label: `Refund ${format(refundable)}`,
    reason: null,
  };
}

/**
 * What the refund dialog will not send. The server checks all of this again —
 * see the module note above — and this only spares the desk a round trip to be
 * told something the screen already knew.
 */
export function refundProblem({ amount, reason, confirmed, refundable }) {
  if (amount === "" || amount === null || amount === undefined) {
    return "Enter the amount to refund.";
  }
  const typed = Number(amount);
  if (!Number.isFinite(typed) || typed <= 0) return "A refund must be greater than zero.";
  if (typed > Number(refundable)) return "That is more than is left to refund on this payment.";
  if (!String(reason ?? "").trim()) return "A reason is required.";
  if (!confirmed) return "Confirm that this money is being handed back.";
  return null;
}


// ---------------------------------------------------------------------------
// Services: cancelling one, with or without its money going back
// ---------------------------------------------------------------------------

/**
 * Which cancellation a service is eligible for, for this user.
 *
 *   cancel            — nothing has been paid: withdraw the bill. No money
 *                       moves and the outstanding on it becomes zero.
 *   cancelAndRefund   — money is on the bill: withdraw it *and* hand back all of
 *                       it, atomically. The amount is never a choice here — a
 *                       partial refund against a withdrawn bill would leave the
 *                       patient in credit (`billing.services.FullRefundRequired`).
 *                       Giving back part of a payment for a service that stays
 *                       active is the Refunds desk.
 *
 * `visible` is CANCEL_ROLES. Cancel & refund also needs REFUND_ROLES — the pair
 * the API requires — so being able to cancel is never a way into the drawer.
 *
 * Returns `{ visible, cancel, cancelAndRefund, refundable, untraceable,
 * outstanding, cancelled, reason }`, where `reason` explains why neither action
 * is offered.
 */
export function chargeActions(charge, user, { format = (n) => n } = {}) {
  const visible = hasRole(user, CANCEL_ROLES);
  const mayRefund = hasRole(user, REFUND_ROLES);
  const paid = Number(charge?.amount_paid ?? 0);
  const refundable = Number(charge?.refundable_amount ?? paid);
  const untraceable = Number(charge?.untraceable_amount ?? 0);
  const outstanding = Number(charge?.outstanding ?? 0);
  const base = { visible, cancel: false, cancelAndRefund: false, refundable, untraceable,
                 outstanding, cancelled: false, reason: null };

  if (charge?.status === "cancelled" || charge?.is_cancelled === true) {
    return { ...base, outstanding: 0, cancelled: true, reason: "This service is cancelled." };
  }
  if (charge?.status === "waived") {
    return { ...base, reason: "This charge was written off." };
  }
  // Nothing paid: a plain cancellation is the whole answer.
  if (!(refundable > 0)) return { ...base, cancel: true };
  if (!mayRefund) {
    return { ...base, reason: "Money was paid for this service — cancelling it needs somebody who can refund." };
  }
  // The server refuses to guess which payment the money came from, so the
  // button is not offered for it either.
  if (untraceable > 0) {
    return { ...base, reason: `${format(untraceable)} paid on this service cannot be traced to a payment — refer it to accounts.` };
  }
  return { ...base, cancelAndRefund: true };
}

/** What a cancellation, with or without a refund, will not send. */
export function cancellationProblem({ reason, confirmed }) {
  if (!String(reason ?? "").trim()) return "A reason is required.";
  if (!confirmed) return "Confirm that this service is being cancelled.";
  return null;
}

/**
 * The badge a service wears — read off the charge's own status, which
 * `billing.services` maintains on every payment, discount, waiver, refund and
 * cancellation, so the badge and the server's `?status=` filter always agree.
 */
export function serviceStatus(charge) {
  switch (charge?.status) {
    case "cancelled": return { key: "cancelled", label: "CANCELLED", tone: "neutral" };
    case "waived": return { key: "waived", label: "WAIVED", tone: "violet" };
    case "paid": return { key: "paid", label: "PAID", tone: "success" };
    case "partial": return { key: "partial", label: "PARTIALLY PAID", tone: "warning" };
    default: return { key: "unpaid", label: "UNPAID", tone: "danger" };
  }
}
