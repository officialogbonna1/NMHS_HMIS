import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithApp } from "../test/harness.jsx";
import { PosReceiptSheet } from "./DepartmentDocuments.jsx";

// The receipt is what the customer takes away and what accounts reads back, so
// the discount has to be on it as its own figure — never folded into the
// selling price, which is exactly what "do not hide the discount" means.

const sale = (overrides = {}) => ({
  reference: "POS-000001", created_at: "2026-09-14T10:00:00Z",
  completed_at: "2026-09-14T10:00:00Z",
  customer_type: "walk_in", customer_name: "Musa Bello", customer_label: "Musa Bello",
  sold_by_name: "Ada Cash", subtotal: "520.00", discount_amount: "2.00",
  sale_discount_amount: "0.00", total_amount: "518.00",
  payment_method_label: "Cash", amount_tendered: "520.00", change_due: "2.00",
  amount_returned: "0.00", discount_reason: "Loyal customer", discount_by_name: "Ada Cash",
  discount_approved_by_name: null,
  lines: [
    { id: 1, item_name: "Paracetamol 500mg", quantity: 1, unit_label: "tablet",
      unit_price: "20.00", gross: "20.00", line_discount_type: "percent",
      line_discount_value: "10.00", line_discount: "2.00", discount: "2.00", net: "18.00" },
    { id: 2, item_name: "Amoxicillin 500mg", quantity: 1, unit_label: "capsule",
      unit_price: "500.00", gross: "500.00", line_discount_type: "", line_discount_value: "0.00",
      line_discount: "0.00", discount: "0.00", net: "500.00" },
  ],
  ...overrides,
});

describe("the POS receipt", () => {
  it("shows the discount on the line it came off and in the totals", () => {
    renderWithApp(<PosReceiptSheet sale={sale()} onClose={() => {}} />,
                  { user: { role: "cashier" } });

    // Per line: the discounted one names its percentage and its amount, on
    // the row beneath the product it came off.
    const discountRow = screen.getByText(/less discount \(10%\)/).closest("tr");
    expect(discountRow).toHaveTextContent("− ₦2");

    // And the totals carry it as its own row rather than a smaller price.
    expect(screen.getByText("Subtotal").closest("div")).toHaveTextContent("₦520");
    expect(screen.getByText("Item discounts").closest("div")).toHaveTextContent("− ₦2");
    expect(screen.getByText("Total").closest("p")).toHaveTextContent("₦518");
  });

  it("names who gave the discount and why, for accounts to read back", () => {
    renderWithApp(<PosReceiptSheet sale={sale()} onClose={() => {}} />,
                  { user: { role: "cashier" } });
    expect(screen.getByText(/Loyal customer/)).toBeInTheDocument();
    expect(screen.getByText(/given by Ada Cash/)).toBeInTheDocument();
  });

  it("names the authoriser separately when one was required", () => {
    renderWithApp(
      <PosReceiptSheet sale={sale({ discount_approved_by_name: "Ngozi Books" })} onClose={() => {}} />,
      { user: { role: "cashier" } });
    // Who gave it and who allowed it are different people, and the receipt
    // says so rather than calling the cashier the approver.
    expect(screen.getByText(/given by Ada Cash/)).toBeInTheDocument();
    expect(screen.getByText(/authorised by Ngozi Books/)).toBeInTheDocument();
  });

  it("does not invent a hospital number for a walk-in customer", () => {
    renderWithApp(<PosReceiptSheet sale={sale()} onClose={() => {}} />,
                  { user: { role: "cashier" } });
    expect(screen.getByText(/Musa Bello/)).toBeInTheDocument();
    expect(screen.queryByText(/NMHS-P/)).not.toBeInTheDocument();
  });

  it("shows no discount rows at all on an undiscounted sale", () => {
    renderWithApp(
      <PosReceiptSheet
        sale={sale({ discount_amount: "0.00", total_amount: "520.00", discount_reason: "",
                     lines: sale().lines.map((line) => ({
                       ...line, line_discount: "0.00", line_discount_type: "",
                       discount: "0.00", net: line.gross })) })}
        onClose={() => {}} />,
      { user: { role: "cashier" } });
    expect(screen.queryByText(/less discount/)).not.toBeInTheDocument();
    expect(screen.queryByText("Item discounts")).not.toBeInTheDocument();
  });
});
