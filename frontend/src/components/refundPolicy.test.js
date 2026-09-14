import { describe, expect, it } from "vitest";
import { refundProblem, refundState } from "./refundPolicy.js";

// The decision behind the Refund button, on its own. Both screens that offer a
// refund read this, so what it says here is what a cashier sees on either.
const naira = (n) => `NGN${Number(n).toFixed(0)}`;
const cashier = { role: "cashier" };

const payment = (amount, refunded = 0) => ({
  id: 7,
  amount: String(amount),
  amount_refunded: String(refunded),
  refundable_balance: String(amount - refunded),
});

describe("who may refund", () => {
  it("is offered to the roles the backend's REFUND_ROLES allows", () => {
    for (const role of ["cashier", "accountant", "admin", "hospital_admin"]) {
      expect(refundState(payment(10000), { role }).visible).toBe(true);
    }
  });

  it("is not offered to a role the API would refuse", () => {
    for (const role of ["reception", "pharmacist", "doctor", "nurse", "laboratory"]) {
      expect(refundState(payment(10000), { role }).visible).toBe(false);
    }
  });

  it("is not offered when nobody is signed in", () => {
    expect(refundState(payment(10000), null).visible).toBe(false);
  });
});

describe("what is left to refund", () => {
  it("offers the full amount on an untouched payment", () => {
    const state = refundState(payment(10000), cashier, { format: naira });
    expect(state.enabled).toBe(true);
    expect(state.refundable).toBe(10000);
    expect(state.label).toBe("Refund NGN10000");
  });

  it("offers only the remainder after a partial refund", () => {
    // ₦10,000 taken, ₦3,000 refunded — ₦7,000 is what is left.
    const state = refundState(payment(10000, 3000), cashier, { format: naira });
    expect(state.enabled).toBe(true);
    expect(state.refunded).toBe(3000);
    expect(state.refundable).toBe(7000);
    expect(state.label).toBe("Refund NGN7000");
  });

  it("refuses a payment that has already gone back in full", () => {
    const state = refundState(payment(10000, 10000), cashier, { format: naira });
    expect(state.enabled).toBe(false);
    expect(state.reason).toBe("Fully refunded");
  });

  it("refuses a payment with nothing on it", () => {
    expect(refundState({ id: 7, amount: "0.00" }, cashier).enabled).toBe(false);
    expect(refundState(null, cashier).enabled).toBe(false);
    expect(refundState({ amount: "500" }, cashier).enabled).toBe(false);  // no id
  });

  it("falls back to the payment amount when the serializer sent no figures", () => {
    // An older cached row. The server settles it either way.
    const state = refundState({ id: 7, amount: "4000.00" }, cashier, { format: naira });
    expect(state.enabled).toBe(true);
    expect(state.refundable).toBe(4000);
  });
});

describe("what the form will not send", () => {
  const ok = { amount: "3000", reason: "Overcharged", confirmed: true, refundable: 7000 };

  it("accepts a complete, in-range request", () => {
    expect(refundProblem(ok)).toBeNull();
  });

  it("requires an amount, a reason and the confirmation", () => {
    expect(refundProblem({ ...ok, amount: "" })).toMatch(/enter the amount/i);
    expect(refundProblem({ ...ok, reason: "   " })).toMatch(/reason is required/i);
    expect(refundProblem({ ...ok, confirmed: false })).toMatch(/confirm/i);
  });

  it("refuses zero, a negative and a non-number", () => {
    for (const amount of ["0", "-100", "abc"]) {
      expect(refundProblem({ ...ok, amount })).toMatch(/greater than zero/i);
    }
  });

  it("refuses more than is left on the payment", () => {
    expect(refundProblem({ ...ok, amount: "7001" })).toMatch(/more than is left/i);
    expect(refundProblem({ ...ok, amount: "7000" })).toBeNull();
  });
});
