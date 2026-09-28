/**
 * Receive a delivery: the item field is "Medical item" — a medicine, cotton
 * wool, a syringe — and it is searchable. What it submits is unchanged: the
 * inventory Item's id, on the same `POST /batches/` body.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import { ReceiveStock } from "./StockPanels.jsx";

const LOCATIONS = [
  { id: 1, name: "Main Store", code: "main-store", is_default_receiving: true },
  { id: 2, name: "Pharmacy", code: "pharmacy", is_dispensing_point: true },
];
const ITEMS = [
  { id: 11, name: "Amoxciline" },
  { id: 12, name: "Paracetamol 500mg" },
  { id: 13, name: "Cotton Wool" },
  { id: 14, name: "Cotton Buds" },
  { id: 15, name: "Syringe 5ml" },
];

beforeEach(() => {
  vi.spyOn(api, "get").mockImplementation((url) =>
    Promise.resolve({ data: url === "/items/" ? ITEMS : [] }));
});
afterEach(() => vi.restoreAllMocks());

function renderForm() {
  return renderWithApp(<ReceiveStock locations={LOCATIONS} store={LOCATIONS[0]} />,
                       { user: { role: "inventory_manager" } });
}

const field = () => screen.getByLabelText(/^Medical item/);
// The picker's own list — not the "Receive into" dropdown's <option>s beside it.
const optionNames = () => {
  const list = screen.queryByRole("listbox");
  return list ? within(list).queryAllByRole("option").map((o) => o.textContent) : [];
};

async function fillTheRest(user) {
  await user.type(screen.getByLabelText(/^Batch number/), "B-01");
  await user.type(screen.getByLabelText(/^Quantity/), "50");
  await user.type(screen.getByLabelText(/^Cost price/), "10");
  await user.type(screen.getByLabelText(/^Sale price/), "20");
  await user.type(screen.getByLabelText(/^Expiry date/), "2027-09-30");
}

describe("Receive a delivery — the medical item field", () => {
  it("is labelled for any stocked item, not only drugs", () => {
    renderForm();
    expect(field()).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Drug/)).not.toBeInTheDocument();
  });

  it("opens the whole catalogue when clicked, and an item is chosen from it", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.click(field());
    await waitFor(() => expect(optionNames()).toEqual(ITEMS.map((i) => i.name)));
    await user.click(screen.getByRole("option", { name: "Paracetamol 500mg" }));
    expect(field()).toHaveValue("Paracetamol 500mg");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("narrows the list by name as you type", async () => {
    const user = userEvent.setup();
    renderForm();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/items/", expect.anything()));
    await user.type(field(), "cotton");
    expect(optionNames()).toEqual(["Cotton Wool", "Cotton Buds"]);
    await user.clear(field());
    await user.type(field(), "para");
    expect(optionNames()).toEqual(["Paracetamol 500mg"]);
    await user.clear(field());
    await user.type(field(), "zzz");
    expect(screen.getByText("No item by that name.")).toBeInTheDocument();
  });

  it("submits the chosen search result's own item id, on the unchanged body", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderForm();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/items/", expect.anything()));
    await user.type(field(), "cotton w");
    await user.click(screen.getByRole("option", { name: "Cotton Wool" }));
    await fillTheRest(user);
    await user.selectOptions(screen.getByLabelText(/^Receive into/), "2");
    await user.click(screen.getByRole("button", { name: /Receive/ }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith("/batches/", {
      item: 13, batch_no: "B-01", quantity: "50", opening_quantity: 50, cost_price: "10",
      sale_price: "20", expiry_date: "2027-09-30", supplier: "", location: 2,
    });
  });

  it("is chosen from the keyboard too", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderForm();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/items/", expect.anything()));
    await user.type(field(), "syr{Enter}");
    expect(field()).toHaveValue("Syringe 5ml");
    await fillTheRest(user);
    await user.click(screen.getByRole("button", { name: /Receive/ }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1].item).toBe(15);
  });

  it("will not submit without an item", async () => {
    const post = vi.spyOn(api, "post");
    const user = userEvent.setup();
    renderForm();
    await fillTheRest(user);
    await user.click(screen.getByRole("button", { name: /Receive/ }));
    expect(post).not.toHaveBeenCalled();
  });
});
