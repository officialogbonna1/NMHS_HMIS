import { describe, expect, it } from "vitest";

import { allowsType, judge, judgeCart, offeredTypes, readPolicy } from "./posDiscountPolicy.js";

// The till's own reading of the discount policy. It refuses early and decides
// nothing — `sales/discount_policy.py` re-judges every one of these on the
// priced amount, inside the transaction that moves the stock. What these hold
// is that the screen refuses *for the same reasons*, so a cashier is never
// told one thing and then another.

const policy = (overrides = {}) => readPolicy({
  pos_discounts_enabled: true,
  pos_discount_types: "both",
  pos_discount_limit_percent: "100.00",
  pos_max_discount_percent: "100.00",
  pos_discount_limit_amount: "0.00",
  pos_max_discount_amount: "0.00",
  pos_discount_preset_values: ["5", "10", "20"],
  pos_discount_reason_options: ["Staff discount", "Loyal customer"],
  ...overrides,
});

describe("readPolicy", () => {
  it("falls back to the behaviour that was there before a policy existed", () => {
    // No settings loaded yet: the dialog must still open, and open permissive
    // — the server is what refuses, and it has the real policy.
    const fallback = readPolicy(undefined);
    expect(fallback).toMatchObject({ enabled: true, types: "both", limitPercent: 100,
                                     maxPercent: 100, limitAmount: 0, maxAmount: 0 });
    expect(fallback.presets).toEqual([5, 10, 15, 20, 25]);
  });

  it("drops a preset that is not a usable percentage", () => {
    expect(policy({ pos_discount_preset_values: ["5", "oops", "0", "400", "20"] }).presets)
      .toEqual([5, 20]);
  });
});

describe("what the till offers", () => {
  it("offers both kinds by default and one when the hospital says so", () => {
    expect(offeredTypes(policy()).map(([key]) => key)).toEqual(["percent", "amount"]);
    expect(offeredTypes(policy({ pos_discount_types: "percent" })).map(([key]) => key))
      .toEqual(["percent"]);
    expect(allowsType(policy({ pos_discount_types: "percent" }), "amount")).toBe(false);
  });

  it("offers nothing at all when discounts are switched off", () => {
    expect(offeredTypes(policy({ pos_discounts_enabled: false }))).toEqual([]);
  });
});

describe("judge", () => {
  it("passes a discount inside the limits", () => {
    const ruling = judge({ policy: policy(), type: "percent", amount: 400, base: 4000 });
    expect(ruling).toMatchObject({ ok: true, needsApproval: false, code: null });
  });

  it("refuses a discount bigger than the line it comes off", () => {
    expect(judge({ policy: policy(), type: "amount", amount: 4500, base: 4000 }))
      .toMatchObject({ ok: false, code: "discount_over_line" });
  });

  it("asks for an approval above the cashier's limit", () => {
    const ruling = judge({ policy: policy({ pos_discount_limit_percent: "10.00" }),
                           type: "percent", amount: 1000, base: 4000, label: "Paracetamol" });
    expect(ruling).toMatchObject({ ok: true, needsApproval: true,
                                   code: "discount_needs_approval" });
    expect(ruling.message).toContain("Paracetamol");
    expect(ruling.message).toContain("25%");
  });

  it("measures a fixed sum as the percentage it really is", () => {
    // The bypass the policy exists to close: ₦500 off ₦1,000 is 50%, whatever
    // the cashier called it.
    expect(judge({ policy: policy({ pos_discount_limit_percent: "10.00" }),
                   type: "amount", amount: 500, base: 1000 }))
      .toMatchObject({ needsApproval: true, code: "discount_needs_approval" });
  });

  it("keeps a limit exactly at the limit allowed", () => {
    expect(judge({ policy: policy({ pos_discount_limit_percent: "10.00" }),
                   type: "percent", amount: 400, base: 4000 }).needsApproval).toBe(false);
  });

  it("refuses above the maximum outright — no supervisor can clear it", () => {
    const ruling = judge({ policy: policy({ pos_discount_limit_percent: "10.00",
                                            pos_max_discount_percent: "50.00" }),
                           type: "percent", amount: 2400, base: 4000 });
    expect(ruling).toMatchObject({ ok: false, needsApproval: false,
                                   code: "discount_over_maximum" });
  });

  it("applies a fixed-sum ceiling of its own", () => {
    expect(judge({ policy: policy({ pos_discount_limit_amount: "300.00" }),
                   type: "amount", amount: 500, base: 4000 }))
      .toMatchObject({ needsApproval: true });
    expect(judge({ policy: policy({ pos_max_discount_amount: "300.00" }),
                   type: "amount", amount: 500, base: 4000 }))
      .toMatchObject({ ok: false, code: "discount_over_maximum" });
  });

  it("refuses everything when discounts are off, and a kind that is not offered", () => {
    expect(judge({ policy: policy({ pos_discounts_enabled: false }),
                   type: "percent", amount: 100, base: 4000 }).code).toBe("discounts_disabled");
    expect(judge({ policy: policy({ pos_discount_types: "percent" }),
                   type: "amount", amount: 100, base: 4000 }).code)
      .toBe("discount_type_not_allowed");
  });

  it("says nothing about a cart with no discount on it", () => {
    expect(judge({ policy: policy(), type: "percent", amount: 0, base: 4000 }))
      .toMatchObject({ ok: false, needsApproval: false, code: null });
  });
});

describe("judgeCart", () => {
  const lines = [
    { type: "percent", amount: 400, base: 4000, label: "Paracetamol 500mg" },
    { type: null, amount: 0, base: 6000, label: "Amoxicillin 500mg" },
  ];

  it("passes a cart whose discounts are all within the limit", () => {
    expect(judgeCart({ policy: policy(), lines, saleDiscount: null, saleBase: 9600 }))
      .toMatchObject({ refused: null, needsApproval: false });
  });

  it("asks once for a cart with one over-limit line in it", () => {
    const verdict = judgeCart({
      policy: policy({ pos_discount_limit_percent: "5.00" }),
      lines, saleDiscount: null, saleBase: 9600,
    });
    expect(verdict.needsApproval).toBe(true);
    expect(verdict.approvalMessage).toContain("Paracetamol 500mg");
    expect(verdict.refused).toBeNull();
  });

  it("judges the sale-wide discount by the same limit", () => {
    const verdict = judgeCart({
      policy: policy({ pos_discount_limit_percent: "10.00" }),
      lines: [], saleDiscount: { type: "percent", amount: 3000 }, saleBase: 10000,
    });
    expect(verdict.needsApproval).toBe(true);
    expect(verdict.approvalMessage).toContain("this sale");
  });

  it("reports an outright refusal separately from one a supervisor can clear", () => {
    const verdict = judgeCart({
      policy: policy({ pos_max_discount_percent: "5.00" }),
      lines, saleDiscount: null, saleBase: 9600,
    });
    expect(verdict.refused?.code).toBe("discount_over_maximum");
  });

  it("ignores lines with no discount rather than calling them refusals", () => {
    expect(judgeCart({ policy: policy({ pos_discounts_enabled: false }),
                       lines: [{ type: null, amount: 0, base: 4000 }],
                       saleDiscount: null, saleBase: 4000 }))
      .toMatchObject({ refused: null, needsApproval: false });
  });
});
