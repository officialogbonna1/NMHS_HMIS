import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import { Till } from "./PharmacyPOS.jsx";

// The till's discount interaction: pick items, discount one or several, and be
// told which kind of "no" a refusal is before the payment rather than after it.
//
// The server decides — `sales/discount_policy.py` re-judges everything on the
// priced amount — so what these hold is that the screen asks the same
// questions and sends the right body.

const REGISTER = { id: 1, reference: "REG-000001", location_name: "Pharmacy" };
const CASHIER = { id: 4, role: "cashier", username: "cash" };

const PRODUCTS = [
  { id: 1, name: "Paracetamol 500mg", sku: "PARA500", category: 5, category_name: "Pain Relief",
    unit_label: "tablet", available: 100, price: "1000.00", strength: "500 mg", dosage_form: "Tablet" },
  { id: 2, name: "Amoxicillin 500mg", sku: "AMOX500", category: 6, category_name: "Antibiotics",
    unit_label: "capsule", available: 50, price: "1000.00" },
];

const settings = (overrides = {}) => ({
  name: "NMHS", full_name: "Ngozi Maternity and Hospital Services",
  pos_discounts_enabled: true, pos_discount_types: "both",
  pos_discount_limit_percent: "100.00", pos_max_discount_percent: "100.00",
  pos_discount_limit_amount: "0.00", pos_max_discount_amount: "0.00",
  pos_discount_preset_values: ["5", "10", "20"],
  pos_discount_reason_options: ["Staff discount", "Loyal customer"],
  ...overrides,
});

function mockApi(overrides = {}) {
  return vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/sales/products/") {
      return Promise.resolve({ data: { location: "Pharmacy", categories: [], results: PRODUCTS } });
    }
    if (url === "/hospital-settings/current/") return Promise.resolve({ data: settings(overrides) });
    return Promise.resolve({ data: { results: [] } });
  });
}

const renderTill = () =>
  renderWithApp(<Till register={REGISTER} summary={{}} user={CASHIER} />, { user: CASHIER });

/** Put a product in the cart by clicking its card. */
async function addToCart(user, name) {
  await user.click(await screen.findByRole("button", { name: new RegExp(name) }));
}

afterEach(() => vi.restoreAllMocks());

describe("discounting one item", () => {
  beforeEach(() => { mockApi(); });

  it("shows the original struck through and the net beside it", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");

    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));
    await user.type(await screen.findByLabelText("Percentage off"), "10");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Loyal customer");

    // The dialog does the arithmetic before anything is committed.
    expect(screen.getByText("New total").closest("div"))
      .toHaveTextContent("₦900");
    await user.click(screen.getByRole("button", { name: "Apply discount" }));

    const cart = within(screen.getByLabelText("Cart"));
    expect(await cart.findByText("₦900")).toBeInTheDocument();
    expect(cart.getByText("₦1,000")).toHaveClass("line-through");
    expect(cart.getByText(/10% off/)).toBeInTheDocument();
  });

  it("offers the hospital's own preset percentages and reasons", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));

    // 5/10/20 from the settings — not the five that used to be in the file.
    for (const percent of ["5%", "10%", "20%"]) {
      expect(screen.getByRole("button", { name: percent })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "25%" })).not.toBeInTheDocument();
    const reasons = screen.getByLabelText("Reason preset");
    expect(within(reasons).getByText("Loyal customer")).toBeInTheDocument();
  });
});

describe("discounting several selected items", () => {
  beforeEach(() => { mockApi(); });

  it("discounts only the ticked lines", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await addToCart(user, "Amoxicillin 500mg");

    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));

    await user.type(await screen.findByLabelText("Percentage off"), "10");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Staff discount");
    await user.click(screen.getByRole("button", { name: "Apply discount" }));

    const cart = within(screen.getByLabelText("Cart"));
    // The ticked line is discounted; the other keeps its price.
    expect(await cart.findByText("₦900")).toBeInTheDocument();
    expect(cart.getAllByText("₦1,000").length).toBeGreaterThan(0);
    expect(cart.getAllByText(/10% off/)).toHaveLength(1);
  });
});

describe("the policy on screen", () => {
  it("offers only the kind of discount the hospital allows", async () => {
    const user = userEvent.setup();
    mockApi({ pos_discount_types: "percent" });
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));

    expect(await screen.findByText(/the only kind this till offers/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fixed amount" })).not.toBeInTheDocument();
  });

  it("warns that an over-limit discount will need authorising, and still allows it", async () => {
    const user = userEvent.setup();
    mockApi({ pos_discount_limit_percent: "10.00" });
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));
    await user.type(await screen.findByLabelText("Percentage off"), "25");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Management approval");

    expect(await screen.findByText(/This needs authorising/)).toBeInTheDocument();
    // Allowed to apply — the authorisation is asked for at payment.
    expect(screen.getByRole("button", { name: "Apply discount" })).toBeEnabled();
  });

  it("refuses a discount above the maximum outright", async () => {
    const user = userEvent.setup();
    mockApi({ pos_discount_limit_percent: "10.00", pos_max_discount_percent: "20.00" });
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));
    await user.type(await screen.findByLabelText("Percentage off"), "50");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Too much");

    expect(await screen.findByText("Not allowed")).toBeInTheDocument();
    // No supervisor can clear this one, so there is nothing to apply.
    expect(screen.getByRole("button", { name: "Apply discount" })).toBeDisabled();
  });
});

describe("authorising at the payment", () => {
  it("asks for a supervisor's credentials and resends the sale with them", async () => {
    const user = userEvent.setup();
    mockApi({ pos_discount_limit_percent: "10.00" });
    const post = vi.spyOn(api, "post")
      .mockRejectedValueOnce({
        response: { status: 403, data: { code: "discount_needs_approval",
                                         detail: "25% is above your 10% limit." } },
      })
      .mockResolvedValueOnce({ data: { reference: "POS-000001", total_amount: "3000.00",
                                       payment_method_label: "Cash", change_due: "0.00",
                                       lines: [], discount_amount: "1000.00" } });

    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));
    await user.type(await screen.findByLabelText("Percentage off"), "25");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Management approval");
    await user.click(screen.getByRole("button", { name: "Apply discount" }));

    await user.click(await screen.findByRole("button", { name: /Take Charge/ }));
    await user.click(await screen.findByRole("button", { name: "Complete sale" }));

    // The server refused; the till asks rather than only complaining.
    expect(await screen.findByText("Authorise this discount")).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Authorising staff username/), "acc");
    await user.type(screen.getByLabelText(/^Password/), "super-secret");
    await user.click(screen.getByRole("button", { name: "Authorise and complete" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    const [, body] = post.mock.calls[1];
    expect(body.authorization).toEqual({ username: "acc", password: "super-secret" });
    // The same cart and the same discount, not a retyped one.
    expect(body.lines[0].discount).toEqual({ type: "percent", value: "25" });
    expect(body.discount_reason).toBe("Management approval");
  });
});

// --------------------------------------------------------------------------
// The acceptance walk: a cashier standing at the till, with the figures from
// the brief. Everything below is the POS cart itself — no payment screen is
// entered until the discount is already applied and the totals have moved.

describe("the Discount button in the cart", () => {
  beforeEach(() => { mockApi(); });

  it("is visible in the cart's action area, not hidden on a row", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");

    // A real button, reachable without opening anything first.
    const button = await screen.findByRole("button", { name: "Discount" });
    expect(button).toBeVisible();
  });

  it("is disabled until a line is ticked, and says so", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");

    expect(await screen.findByRole("button", { name: "Discount" })).toBeDisabled();
    expect(screen.getByText(/Tick an item to discount it/)).toBeInTheDocument();

    await user.click(screen.getByLabelText("Select Paracetamol 500mg"));
    expect(await screen.findByRole("button", { name: "Discount 1 item" })).toBeEnabled();
  });

  it("selects one line, several lines, or all of them", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await addToCart(user, "Amoxicillin 500mg");

    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    expect(await screen.findByRole("button", { name: "Discount 1 item" })).toBeInTheDocument();

    await user.click(screen.getByLabelText("Select Amoxicillin 500mg"));
    expect(await screen.findByRole("button", { name: "Discount 2 items" })).toBeInTheDocument();

    // Select all is one control, not a hunt down the list.
    await user.click(screen.getByLabelText("Select all items"));
    expect(await screen.findByRole("button", { name: "Discount" })).toBeDisabled();
    await user.click(screen.getByLabelText("Select all items"));
    expect(await screen.findByRole("button", { name: "Discount 2 items" })).toBeInTheDocument();
  });

  it("names the selected products and their subtotal in the dialog", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await addToCart(user, "Amoxicillin 500mg");
    await user.click(await screen.findByLabelText("Select all items"));
    await user.click(await screen.findByRole("button", { name: "Discount 2 items" }));

    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("Selected items (2)")).toBeInTheDocument();
    expect(dialog.getByText(/Paracetamol/)).toBeInTheDocument();
    expect(dialog.getByText(/Amoxicillin/)).toBeInTheDocument();
    expect(dialog.getByText("Selected subtotal").closest("div")).toHaveTextContent("₦2,000");
  });

  it("previews a fixed amount as well as a percentage", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));

    await user.click(screen.getByRole("button", { name: "Fixed amount" }));
    await user.type(await screen.findByLabelText("Amount off (₦)"), "300");
    expect(screen.getByText("New total").closest("div")).toHaveTextContent("₦700");
  });

  it("leaves an unselected line at its original price", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await addToCart(user, "Amoxicillin 500mg");

    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));
    await user.type(await screen.findByLabelText("Percentage off"), "10");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Loyal customer");
    await user.click(screen.getByRole("button", { name: "Apply discount" }));

    const cart = within(await screen.findByLabelText("Cart"));
    const discounted = cart.getByText(/Paracetamol/).closest("li");
    expect(within(discounted).getByText("₦900")).toBeInTheDocument();
    // The other line has no discount badge and keeps its price.
    const untouched = cart.getByText(/Amoxicillin/).closest("li");
    expect(within(untouched).queryByText(/% off/)).not.toBeInTheDocument();
    expect(within(untouched).getByText("₦1,000")).toBeInTheDocument();
  });

  it("shows the discount in the totals even when it is zero", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");

    const totals = (await screen.findByText("Subtotal")).closest("div");
    expect(within(totals).getByText("Discount")).toBeInTheDocument();
    expect(within(totals).getByText("₦0")).toBeInTheDocument();
  });

  it("keeps a discount when another product is added afterwards", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));
    await user.type(await screen.findByLabelText("Percentage off"), "10");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Loyal customer");
    await user.click(screen.getByRole("button", { name: "Apply discount" }));

    await addToCart(user, "Amoxicillin 500mg");

    const cart = within(await screen.findByLabelText("Cart"));
    // The first line keeps its discount; the new one does not inherit it.
    expect(within(cart.getByText(/Paracetamol/).closest("li")).getByText(/10% off/))
      .toBeInTheDocument();
    expect(within(cart.getByText(/Amoxicillin/).closest("li")).queryByText(/% off/))
      .not.toBeInTheDocument();
  });

  it("removes a discount and restores the line's own price", async () => {
    const user = userEvent.setup();
    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));
    await user.type(await screen.findByLabelText("Percentage off"), "10");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Loyal customer");
    await user.click(screen.getByRole("button", { name: "Apply discount" }));
    const cart = within(await screen.findByLabelText("Cart"));
    expect(await cart.findByText("₦900")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove" }));

    expect(cart.queryByText(/% off/)).not.toBeInTheDocument();
    expect(cart.getByText("₦1,000")).toBeInTheDocument();
    expect(cart.queryByText("₦900")).not.toBeInTheDocument();
  });

  it("tells a pharmacist why there is no Discount button, rather than showing nothing", async () => {
    const user = userEvent.setup();
    mockApi();
    // A pharmacist operates the till but may never discount (rule 18/39) —
    // which is exactly what made the feature look missing before.
    renderWithApp(<Till register={REGISTER} summary={{}} user={{ id: 9, role: "pharmacist" }} />,
                  { user: { id: 9, role: "pharmacist" } });
    await addToCart(user, "Paracetamol 500mg");

    expect(await screen.findByText(/Discounts are given by a cashier/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Discount/ })).not.toBeInTheDocument();
  });
});

describe("the brief's acceptance walk", () => {
  // ₦20 paracetamol and ₦500 amoxicillin, exactly as the brief sets it out.
  const PENNY_PRODUCTS = [
    { ...PRODUCTS[0], price: "20.00" },
    { ...PRODUCTS[1], price: "500.00" },
  ];

  it("discounts one item by 10% and charges the recalculated total", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockImplementation((url) => {
      if (url === "/sales/products/") {
        return Promise.resolve({ data: { location: "Pharmacy", categories: [], results: PENNY_PRODUCTS } });
      }
      if (url === "/hospital-settings/current/") return Promise.resolve({ data: settings() });
      return Promise.resolve({ data: { results: [] } });
    });
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: { reference: "POS-000001", subtotal: "520.00", discount_amount: "2.00",
              total_amount: "518.00", payment_method_label: "Cash", change_due: "0.00", lines: [] },
    });

    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await addToCart(user, "Amoxicillin 500mg");

    await user.click(await screen.findByLabelText("Select Paracetamol 500mg"));
    await user.click(await screen.findByRole("button", { name: "Discount 1 item" }));
    await user.type(await screen.findByLabelText("Percentage off"), "10");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Loyal customer");
    await user.click(screen.getByRole("button", { name: "Apply discount" }));

    // Paracetamol ₦20 → ₦18; amoxicillin untouched at ₦500.
    const cart = within(await screen.findByLabelText("Cart"));
    expect(within(cart.getByText(/Paracetamol/).closest("li")).getByText("₦18"))
      .toBeInTheDocument();
    expect(within(cart.getByText(/Amoxicillin/).closest("li")).getByText("₦500"))
      .toBeInTheDocument();

    // Subtotal ₦520, discount ₦2, total ₦518.
    const totals = screen.getByText("Subtotal").closest("div");
    expect(totals).toHaveTextContent("₦520");
    expect(totals).toHaveTextContent("− ₦2");
    expect(totals).toHaveTextContent("₦518");

    // Take Charge carries the discounted total into the payment screen.
    await user.click(screen.getByRole("button", { name: "Take Charge · ₦518" }));
    expect(await screen.findByText("Take payment — ₦518")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Complete sale" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    const [, body] = post.mock.calls[0];
    // The discount travels on its own line, and only on that line.
    expect(body.lines).toEqual([
      { item: 1, quantity: 1, discount: { type: "percent", value: "10" } },
      { item: 2, quantity: 1 },
    ]);
    expect(body.discount_reason).toBe("Loyal customer");
    // Walk-in: no patient is invented to carry the discount.
    expect(body.customer_type).toBe("walk_in");
    expect(body.patient).toBeFalsy();
  });

  it("applies a general discount to several selected items and no others", async () => {
    const user = userEvent.setup();
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: { reference: "POS-000002", total_amount: "1800.00", payment_method_label: "Cash",
              change_due: "0.00", lines: [] },
    });

    renderTill();
    await addToCart(user, "Paracetamol 500mg");
    await addToCart(user, "Amoxicillin 500mg");
    await user.click(await screen.findByLabelText("Select all items"));
    await user.click(await screen.findByRole("button", { name: "Discount 2 items" }));
    await user.type(await screen.findByLabelText("Percentage off"), "10");
    await user.type(screen.getByPlaceholderText(/Staff purchase/), "Staff discount");
    await user.click(screen.getByRole("button", { name: "Apply discount" }));

    await user.click(await screen.findByRole("button", { name: /Take Charge/ }));
    await user.click(await screen.findByRole("button", { name: "Complete sale" }));

    await waitFor(() => expect(post).toHaveBeenCalled());
    const [, body] = post.mock.calls[0];
    expect(body.lines).toEqual([
      { item: 1, quantity: 1, discount: { type: "percent", value: "10" } },
      { item: 2, quantity: 1, discount: { type: "percent", value: "10" } },
    ]);
    // No sale-wide discount was invented — each selected line carries its own,
    // which is what the existing backend prices and audits.
    expect(body.discount).toBeNull();
  });
});
