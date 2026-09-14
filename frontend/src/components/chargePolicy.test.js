import { describe, expect, it } from "vitest";
import { cancellationProblem, chargeActions, serviceStatus } from "./refundPolicy.js";

// Which cancellation a service is eligible for, and the badge it wears. The
// distinction under test throughout: cancelling ends an obligation; Cancel &
// refund also returns *everything* paid for it; a partial refund is not a
// cancellation at all and is not offered here.
const cashier = { role: "cashier" };
const naira = (n) => `₦${Number(n).toLocaleString("en-NG")}`;

const charge = (over = {}) => ({
  id: 1, description: "Laboratory: FBC", department_name: "Laboratory",
  amount: "4000.00", amount_paid: "0.00", amount_refunded: "0.00",
  refundable_amount: "0.00", untraceable_amount: "0.00", outstanding: "4000.00",
  status: "unpaid", is_cancelled: false, created_at: "2026-09-01T09:00:00Z", ...over,
});

const paid = (over = {}) => charge({
  amount_paid: "4000.00", refundable_amount: "4000.00", outstanding: "0.00",
  status: "paid", ...over,
});

describe("who may act", () => {
  it("offers cancellation to the roles the backend's CANCEL_ROLES allows", () => {
    for (const role of ["cashier", "accountant", "admin", "hospital_admin"]) {
      expect(chargeActions(charge(), { role }).visible).toBe(true);
    }
  });

  it("offers nothing to a role the API would refuse", () => {
    for (const role of ["reception", "pharmacist", "doctor", "nurse", "laboratory"]) {
      expect(chargeActions(charge(), { role }).visible).toBe(false);
    }
    expect(chargeActions(charge(), null).visible).toBe(false);
  });
});

describe("which cancellation fits the service", () => {
  it("an unpaid service is simply cancelled — no money moves", () => {
    const actions = chargeActions(charge(), cashier);
    expect(actions.cancel).toBe(true);
    expect(actions.cancelAndRefund).toBe(false);
  });

  it("a paid service is cancelled *and* refunded in full, never cancelled alone", () => {
    const actions = chargeActions(paid(), cashier);
    expect(actions.cancelAndRefund).toBe(true);
    expect(actions.refundable).toBe(4000);
    expect(actions.cancel).toBe(false);
  });

  it("a part-paid service refunds exactly what was paid", () => {
    const actions = chargeActions(charge({
      amount_paid: "2000.00", refundable_amount: "2000.00", outstanding: "2000.00", status: "partial",
    }), cashier);
    expect(actions.cancelAndRefund).toBe(true);
    expect(actions.refundable).toBe(2000);
    expect(actions.cancel).toBe(false);
  });

  it("no longer offers a refund that leaves the service active — that is the Refunds desk", () => {
    expect(chargeActions(paid(), cashier)).not.toHaveProperty("refund");
  });

  it("refuses to offer Cancel & refund on money that cannot be traced to a payment", () => {
    const actions = chargeActions(paid({ untraceable_amount: "1500.00" }), cashier, { format: naira });
    expect(actions.cancelAndRefund).toBe(false);
    expect(actions.cancel).toBe(false);
    expect(actions.reason).toMatch(/₦1,500 .*cannot be traced/);
  });

  it("an already cancelled service offers nothing and says so", () => {
    const actions = chargeActions(charge({ status: "cancelled", is_cancelled: true, outstanding: "0.00" }), cashier);
    expect(actions.cancel).toBe(false);
    expect(actions.cancelAndRefund).toBe(false);
    expect(actions.cancelled).toBe(true);
    expect(actions.reason).toMatch(/cancelled/i);
  });

  it("a written-off charge is left alone", () => {
    const actions = chargeActions(charge({ status: "waived" }), cashier);
    expect(actions.cancel).toBe(false);
    expect(actions.cancelAndRefund).toBe(false);
    expect(actions.reason).toMatch(/written off/i);
  });
});

describe("the badge a service wears", () => {
  it("follows the charge's own status, so it agrees with the server's filter", () => {
    expect(serviceStatus(charge()).label).toBe("UNPAID");
    expect(serviceStatus(paid()).label).toBe("PAID");
    expect(serviceStatus(charge({ status: "partial" })).label).toBe("PARTIALLY PAID");
    expect(serviceStatus(charge({ status: "waived" })).label).toBe("WAIVED");
    expect(serviceStatus(paid({ status: "cancelled", amount_refunded: "4000.00" })).label)
      .toBe("CANCELLED");
  });
});

describe("what a cancellation will not send", () => {
  it("needs a reason and an explicit confirmation", () => {
    expect(cancellationProblem({ reason: "Not run", confirmed: true })).toBeNull();
    expect(cancellationProblem({ reason: "", confirmed: true })).toMatch(/reason/i);
    expect(cancellationProblem({ reason: "   ", confirmed: true })).toMatch(/reason/i);
    expect(cancellationProblem({ reason: "Not run", confirmed: false })).toMatch(/confirm/i);
  });
});
