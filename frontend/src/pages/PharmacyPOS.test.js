import { describe, expect, it } from "vitest";

import { cartTotals, discountOf, lineDiscountOf } from "./PharmacyPOS.jsx";

// The till's preview arithmetic. The server decides what a sale costs; these
// hold the screen to the same rules, so the cashier is never shown a total the
// server will then refuse or charge differently.

const line = (price, quantity, discount = null) => ({ item: price, name: `Item ${price}`, price, quantity, discount });

describe("discountOf", () => {
  it("takes a percentage or a fixed amount, never more than the base", () => {
    expect(discountOf(5000, { type: "percent", value: "10" })).toBe(500);
    expect(discountOf(5000, { type: "amount", value: "750" })).toBe(750);
    expect(discountOf(100, { type: "amount", value: "250" })).toBe(100);
  });

  it("gives nothing for a missing, zero or unreadable discount", () => {
    expect(discountOf(5000, null)).toBe(0);
    expect(discountOf(5000, { type: "percent", value: "" })).toBe(0);
    expect(discountOf(5000, { type: "percent", value: "abc" })).toBe(0);
  });
});

describe("cartTotals", () => {
  it("₦5,000 at 10% off is ₦4,500", () => {
    const totals = cartTotals([line(200, 25)], { type: "percent", value: "10" });
    expect(totals).toMatchObject({ subtotal: 5000, totalDiscount: 500, total: 4500, hasDiscount: true });
  });

  it("takes a line's own discount first and the sale discount off what is left", () => {
    const cart = [line(50, 2), line(200, 25, { type: "percent", value: "10" })];
    const totals = cartTotals(cart, { type: "amount", value: "100" });
    expect(lineDiscountOf(cart[1])).toBe(500);
    expect(totals).toMatchObject({ subtotal: 5100, lineDiscounts: 500, saleDiscount: 100, total: 4500 });

    // A sale-wide percentage is of the amount after item discounts: 50% of 80.
    const halfOff = cartTotals([line(100, 1, { type: "amount", value: "20" })], { type: "percent", value: "50" });
    expect(halfOff).toMatchObject({ lineDiscounts: 20, saleDiscount: 40, total: 40 });
  });

  it("flags a discount that covers the whole sale and one bigger than its line", () => {
    expect(cartTotals([line(200, 1)], { type: "percent", value: "100" }).coversEverything).toBe(true);
    const over = cartTotals([line(50, 1, { type: "amount", value: "60" }), line(200, 1)], null);
    expect(over.overLine.map((l) => l.price)).toEqual([50]);
    expect(over.total).toBeGreaterThanOrEqual(0);
  });

  it("says there is no discount when there is none", () => {
    expect(cartTotals([line(200, 2)], null)).toMatchObject({ subtotal: 400, total: 400, hasDiscount: false });
  });
});
