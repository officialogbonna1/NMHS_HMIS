/**
 * What the till may take off, as the screen understands it.
 *
 * A pure module for the same reason `refundPolicy.js` is one: the decisions
 * here are arithmetic and rules, not markup, and they are worth testing
 * without rendering a POS.
 *
 * **It refuses early; it never decides.** Every rule below is enforced again
 * in `sales/discount_policy.py`, on the priced amount, inside the transaction
 * that moves the stock — so a stale policy on screen loses that argument
 * rather than winning it. What this buys is a cashier finding out before they
 * have typed a cart, and knowing *which* kind of refusal it is: "ask a
 * supervisor" and "nobody may do this" look identical in a 403 and could not
 * be more different at a counter.
 */

const HUNDRED = 100;

/** The policy the server sent, with the defaults the backend also uses. */
export function readPolicy(settings) {
  const row = settings ?? {};
  return {
    enabled: row.pos_discounts_enabled ?? true,
    types: row.pos_discount_types ?? "both",
    limitPercent: number(row.pos_discount_limit_percent, HUNDRED),
    maxPercent: number(row.pos_max_discount_percent, HUNDRED),
    limitAmount: number(row.pos_discount_limit_amount, 0),
    maxAmount: number(row.pos_max_discount_amount, 0),
    presets: (row.pos_discount_preset_values ?? ["5", "10", "15", "20", "25"])
      .map((value) => Number(value)).filter((value) => value > 0 && value <= HUNDRED),
    reasons: row.pos_discount_reason_options ?? [],
  };
}

function number(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Whether this kind of discount is offered at all. */
export function allowsType(policy, type) {
  return policy.enabled && (policy.types === "both" || policy.types === type);
}

export function offeredTypes(policy) {
  return [["percent", "Percentage"], ["amount", "Fixed amount"]]
    .filter(([key]) => allowsType(policy, key));
}

/**
 * Judge a discount the way the server will: on the **amount**, against the
 * base it comes off.
 *
 * A percentage limit that only looked at `type === "percent"` would be walked
 * past by a fixed sum — ₦500 off a ₦1,000 line is 50% however it was typed —
 * so the effective percentage is what is compared, exactly as the backend
 * does it.
 *
 * Returns `{ ok, needsApproval, code, message }`. `needsApproval` is the one a
 * supervisor can clear; `code: "discount_over_maximum"` is the one nobody can.
 */
export function judge({ policy, type, amount, base, label = "this item" }) {
  if (!policy.enabled) {
    return refuse("discounts_disabled", "Discounts are switched off at the pharmacy till.");
  }
  if (!allowsType(policy, type)) {
    const offered = policy.types === "percent" ? "percentages" : "fixed amounts";
    return refuse("discount_type_not_allowed", `This till takes ${offered} only.`);
  }
  if (!(amount > 0)) return { ok: false, needsApproval: false, code: null, message: null };
  if (amount > base) {
    return refuse("discount_over_line",
                  `A discount cannot be more than the ${money(base)} it comes off.`);
  }
  const percent = base > 0 ? (amount / base) * HUNDRED : HUNDRED;

  if (policy.maxPercent < HUNDRED && percent > policy.maxPercent + 1e-9) {
    return refuse("discount_over_maximum",
                  `${pc(percent)}% off ${label} is more than the ${pc(policy.maxPercent)}% `
                  + "this hospital allows on any discount.");
  }
  if (policy.maxAmount > 0 && amount > policy.maxAmount) {
    return refuse("discount_over_maximum",
                  `${money(amount)} is more than the ${money(policy.maxAmount)} `
                  + "this hospital allows on any discount.");
  }
  const overPercent = policy.limitPercent < HUNDRED && percent > policy.limitPercent + 1e-9;
  const overAmount = policy.limitAmount > 0 && amount > policy.limitAmount;
  if (overPercent || overAmount) {
    return {
      ok: true,
      needsApproval: true,
      code: "discount_needs_approval",
      message: overPercent
        ? `${pc(percent)}% off ${label} is above the ${pc(policy.limitPercent)}% a cashier `
          + "may give. An accountant or an administrator has to authorise it."
        : `${money(amount)} is above the ${money(policy.limitAmount)} a cashier may give. `
          + "An accountant or an administrator has to authorise it.",
    };
  }
  return { ok: true, needsApproval: false, code: null, message: null };
}

/**
 * The whole cart's verdict: does anything on this receipt need authorising,
 * and is anything on it refused outright?
 *
 * Checked line by line and then on the sale-wide discount, the way the server
 * checks it, so the till asks for an authorisation once and for the right
 * reason.
 */
export function judgeCart({ policy, lines, saleDiscount, saleBase }) {
  const verdicts = lines
    .filter((line) => line.amount > 0)
    .map((line) => judge({ policy, type: line.type, amount: line.amount,
                           base: line.base, label: line.label }));
  if (saleDiscount?.amount > 0) {
    verdicts.push(judge({ policy, type: saleDiscount.type, amount: saleDiscount.amount,
                          base: saleBase, label: "this sale" }));
  }
  const refused = verdicts.find((verdict) => !verdict.ok && verdict.code);
  return {
    refused: refused ?? null,
    needsApproval: verdicts.some((verdict) => verdict.needsApproval),
    approvalMessage: verdicts.find((verdict) => verdict.needsApproval)?.message ?? null,
  };
}

function refuse(code, message) {
  return { ok: false, needsApproval: false, code, message };
}

/** 25, or 12.5 — never 25.00. */
function pc(value) {
  return String(Math.round(value * 10) / 10);
}

function money(value) {
  return new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN",
                                          maximumFractionDigits: 2 }).format(value ?? 0);
}
