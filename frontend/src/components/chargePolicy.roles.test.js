import { describe, expect, it, vi } from "vitest";

// CANCEL_ROLES and REFUND_ROLES hold the same people today. Pull them apart and
// the screen must follow the backend: being able to cancel is never, by itself,
// a way into the drawer.
vi.mock("../auth/roles.js", async (importOriginal) => ({
  ...(await importOriginal()),
  CANCEL_ROLES: ["cashier", "accountant"],
  REFUND_ROLES: ["accountant"],
}));

const { chargeActions } = await import("./refundPolicy.js");

const paid = {
  amount: "4000.00", amount_paid: "4000.00", refundable_amount: "4000.00",
  untraceable_amount: "0.00", outstanding: "0.00", status: "paid",
};

describe("a role that may cancel but not refund", () => {
  it("can still cancel an unpaid service", () => {
    const actions = chargeActions({ ...paid, amount_paid: "0.00", refundable_amount: "0.00",
                                    outstanding: "4000.00", status: "unpaid" },
                                  { role: "cashier" });
    expect(actions.cancel).toBe(true);
  });

  it("is not offered Cancel & refund, and is told why", () => {
    const actions = chargeActions(paid, { role: "cashier" });
    expect(actions.visible).toBe(true);
    expect(actions.cancelAndRefund).toBe(false);
    expect(actions.reason).toMatch(/somebody who can refund/i);
  });

  it("while a role holding both is", () => {
    expect(chargeActions(paid, { role: "accountant" }).cancelAndRefund).toBe(true);
  });
});
