import { describe, expect, it } from "vitest";
import {
  needsPaymentAcknowledgement, PAYMENT_LABEL, paymentState,
} from "./billingStatus.js";

// The vocabulary a department reads, and what it does with it. Every figure
// here comes off the `billing` block the server sends, which
// `apps/billing/status.py` renders from the charge itself — one authoritative
// balance. Nothing in this module adds, subtracts or decides anything about
// the money; these tests hold that it also never *disagrees* with it.

const billing = (over = {}) => ({
  billed: true, status: "unpaid", label: "UNPAID", requires_payment: true,
  amount: "8000.00", discounted: "0.00", waived: "0.00", payable: "8000.00",
  paid: "0.00", outstanding: "8000.00", deferred: false, ...over,
});

describe("what a department is told about a service's bill", () => {
  it("says PAYMENT REQUIRED for an unpaid one, with the amount", () => {
    const state = paymentState(billing());
    expect(state.label).toBe("UNPAID");
    expect(state.tone).toBe("danger");
    expect(state.requiresPayment).toBe(true);
    expect(state.summary).toBe("UNPAID · ₦8,000");
    expect(state.detail).toMatch(/₦8,000 is owed/);
  });

  it("says PARTIALLY PAID with what is actually left", () => {
    const state = paymentState(billing({
      status: "partial", paid: "5000.00", outstanding: "3000.00",
    }));
    expect(state.label).toBe("PARTIALLY PAID");
    expect(state.requiresPayment).toBe(true);
    expect(state.summary).toBe("PARTIALLY PAID · ₦3,000 outstanding");
    expect(state.detail).toBe(
      "₦5,000 of ₦8,000 paid — ₦3,000 still owed at Reception or the cash desk.");
  });

  it("says PAID with nothing outstanding", () => {
    const state = paymentState(billing({
      status: "paid", paid: "8000.00", outstanding: "0.00", requires_payment: false,
    }));
    expect(state.label).toBe("PAID");
    expect(state.tone).toBe("success");
    expect(state.requiresPayment).toBe(false);
    expect(state.summary).toBe("PAID · ₦8,000");
  });

  it("never shows a waived service as an unpaid bill", () => {
    // The whole point of the distinction: "UNPAID ₦3,500" against a bill
    // nobody will collect sends the patient to a counter with nothing to take.
    const state = paymentState(billing({
      status: "waived", amount: "3500.00", waived: "3500.00", outstanding: "0.00",
      requires_payment: false,
    }));
    expect(state.label).toBe("NO PAYMENT REQUIRED");
    expect(state.requiresPayment).toBe(false);
    expect(state.detail).toMatch(/owes nothing/);
  });

  it("treats pay-later as owed but authorised, never as paid", () => {
    const state = paymentState(billing({
      status: "deferred", outstanding: "8000.00", deferred: true, requires_payment: false,
    }));
    expect(state.label).toBe("PAY LATER");
    expect(state.outstanding).toBe(8000);
    // Somebody with the authority has said the patient may proceed, so the
    // unit is not asked to second-guess it.
    expect(state.requiresPayment).toBe(false);
  });

  it("says a cancelled bill is owed by nobody", () => {
    const state = paymentState(billing({ status: "cancelled", requires_payment: false }));
    expect(state.label).toBe("CANCELLED");
    expect(state.requiresPayment).toBe(false);
  });

  it("distinguishes a service with no charge from an unpaid one", () => {
    const state = paymentState({ billed: false, status: "unbilled", amount: "0.00" });
    expect(state.label).toBe("NOT BILLED");
    expect(state.requiresPayment).toBe(false);
  });

  it("renders nothing at all when the row carries no billing block", () => {
    // A badge invented from an absent field would be a claim the server
    // never made.
    expect(paymentState(undefined).known).toBe(false);
    expect(paymentState(null).label).toBe("");
  });
});

describe("falling back when the server sent an older shape", () => {
  it("derives requires_payment from the status when the field is absent", () => {
    expect(paymentState({ billed: true, status: "unpaid" }).requiresPayment).toBe(true);
    expect(paymentState({ billed: true, status: "paid" }).requiresPayment).toBe(false);
    expect(paymentState({ billed: true, status: "waived" }).requiresPayment).toBe(false);
  });

  it("reads the old part_paid spelling as partially paid", () => {
    expect(PAYMENT_LABEL.part_paid).toBe("PARTIALLY PAID");
    expect(paymentState({ billed: true, status: "part_paid" }).requiresPayment).toBe(true);
  });

  it("reads an order-level block, which carries `total` rather than `amount`", () => {
    const state = paymentState({
      billed: true, status: "partial", total: "20000.00", paid: "8000.00",
      outstanding: "12000.00", requires_payment: true,
    });
    expect(state.amount).toBe(20000);
    expect(state.summary).toBe("PARTIALLY PAID · ₦12,000 outstanding");
  });
});

describe("what the station asks before it closes an unpaid request", () => {
  it("asks only where money is genuinely owed", () => {
    expect(needsPaymentAcknowledgement(billing())).toBe(true);
    expect(needsPaymentAcknowledgement(billing({ status: "partial" }))).toBe(true);
  });

  it("does not ask about a paid, waived, deferred or unbilled service", () => {
    for (const status of ["paid", "waived", "deferred", "cancelled", "unbilled"]) {
      expect(needsPaymentAcknowledgement(billing({ status, requires_payment: false })))
        .toBe(false);
    }
  });
});
