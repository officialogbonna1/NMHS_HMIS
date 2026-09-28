/**
 * Inventory → Add item. The same catalogue form Administration → Products
 * renders (`ResourceForm` over `CONFIG_RESOURCES.products`), posting to the
 * same `/api/items/`, offered only to the administrators the server lets write
 * items — and whatever it creates is at once selectable in Receive stock.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import InventoryDashboard from "./InventoryDashboard.jsx";

const LOCATIONS = [
  { id: 1, name: "Main Store", code: "main-store", is_default_receiving: true, total_units: 0 },
  { id: 2, name: "Pharmacy", code: "pharmacy", is_dispensing_point: true, total_units: 0 },
];
const CATEGORIES = [{ id: 5, name: "Analgesics" }, { id: 6, name: "Medical Consumables" }];
const UNITS = [{ id: 7, name: "Tablet" }, { id: 8, name: "Pack" }];

let items;

beforeEach(() => {
  items = [{ id: 1, name: "Paracetamol 500mg" }];
  vi.spyOn(api, "get").mockImplementation((url) => {
    const data = {
      "/stock-locations/": LOCATIONS, "/item-categories/": CATEGORIES, "/units/": UNITS,
      "/items/": items,
      // The count sheet is an object, not a list — the shape the panel reads.
      "/stock-counts/sheet/": { location: LOCATIONS[0], lines: [] },
    }[url] ?? [];
    return Promise.resolve({ data });
  });
});
afterEach(() => vi.restoreAllMocks());

function renderAs(role, route = "/inventory?tab=add-item") {
  return renderWithApp(<InventoryDashboard />, { user: { id: 1, role }, route });
}

// `TabBar` is a <nav> of buttons, the active one marked `aria-current`.
const tabBar = () => screen.getByRole("navigation", { name: "Inventory sections" });
const tabNames = () => within(tabBar()).getAllByRole("button").map((t) => t.textContent);
const tab = (name) => within(tabBar()).getByRole("button", { name });
// The form's own submit, not the tab of the same name.
const submit = () => screen.getAllByRole("button", { name: "Add item" })
  .find((b) => b.getAttribute("type") === "submit");

async function fillCottonWool(user) {
  await user.type(screen.getByLabelText(/^Name/), "Cotton Wool");
  await waitFor(() => expect(screen.getByRole("option", { name: "Medical Consumables" })).toBeInTheDocument());
  await user.selectOptions(screen.getByLabelText(/^Category/), "6");
  await user.selectOptions(screen.getByLabelText(/^Unit/), "8");
  await user.type(screen.getByLabelText(/^SKU/), "CON-COT-01");
}

describe("the Add item tab", () => {
  it("sits second in the tab row for both administrators", () => {
    for (const role of ["hospital_admin", "admin"]) {
      const { unmount } = renderAs(role, "/inventory");
      expect(tabNames()).toEqual(["Stock on hand", "Add item", "Receive stock", "Transfer",
                                  "Physical count", "Import / export", "Movement log"]);
      unmount();
    }
  });

  it("is not offered to the inventory manager or the pharmacy, even by its address", () => {
    for (const role of ["inventory_manager", "pharmacist"]) {
      const { unmount } = renderAs(role);
      expect(tabNames()).not.toContain("Add item");
      expect(tabNames()).toContain("Receive stock");
      expect(screen.queryByText("New medical item")).not.toBeInTheDocument();
      unmount();
    }
  });

  it("renders every field of the Django admin form, grouped the same way", async () => {
    renderAs("hospital_admin");
    expect(await screen.findByText("New medical item")).toBeInTheDocument();
    for (const section of ["Basic information", "Counter identifiers", "Reordering"]) {
      expect(screen.getByText(section, { selector: "legend" })).toBeInTheDocument();
    }
    for (const label of [/^Name/, /^Strength/, /^Dosage form/, /^Category/, /^Unit/, /^SKU/,
                         /^Barcode/, /^Reorder threshold/]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.getByLabelText(/Is active/)).toBeChecked();
    expect(screen.getByText(/cannot be received, transferred or newly prescribed/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Reorder threshold/)).toHaveValue(10);
  });

  it("offers the existing categories and units, and posts their ids", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { id: 9, name: "Cotton Wool" } });
    const user = userEvent.setup();
    renderAs("hospital_admin");
    await fillCottonWool(user);
    expect(within(screen.getByLabelText(/^Unit/)).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["None", "Tablet", "Pack"]);
    await user.click(submit());
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith("/items/", {
      name: "Cotton Wool", strength: "", dosage_form: "", category: 6, unit: 8, is_active: true,
      sku: "CON-COT-01", barcode: "", reorder_threshold: 10,
    });
  });

  it("shows the server's SKU and barcode refusals under their own fields", async () => {
    vi.spyOn(api, "post").mockRejectedValue({ response: { status: 400, data: {
      sku: ["item with this sku already exists."],
      barcode: ["item with this barcode already exists."],
    } } });
    const user = userEvent.setup();
    renderAs("hospital_admin");
    await fillCottonWool(user);
    await user.type(screen.getByLabelText(/^Barcode/), "600123");
    await user.click(submit());
    const sku = screen.getByLabelText(/^SKU/);
    await waitFor(() => expect(sku).toHaveAttribute("aria-invalid", "true"));
    expect(screen.getByLabelText(/^Barcode/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getAllByText(/item with this sku already exists/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/item with this barcode already exists/).length).toBeGreaterThan(0);
    // Nothing was cleared: the person corrects the one field.
    expect(screen.getByLabelText(/^Name/)).toHaveValue("Cotton Wool");
  });

  it("confirms, resets, and the new item is selectable in Receive stock at once", async () => {
    vi.spyOn(api, "post").mockImplementation((url, body) => {
      items = [...items, { id: 9, name: body.name }];
      return Promise.resolve({ data: { id: 9, ...body } });
    });
    const user = userEvent.setup();
    renderAs("hospital_admin");
    await fillCottonWool(user);
    await user.click(submit());

    expect(await screen.findByText("Cotton Wool added")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Name/)).toHaveValue("");         // a fresh form
    expect(screen.getByLabelText(/^SKU/)).toHaveValue("");

    // The button in the confirmation, not the tab.
    await user.click(screen.getAllByRole("button", { name: "Receive stock" })
      .find((b) => !tabBar().contains(b)));
    const picker = await screen.findByLabelText(/^Medical item/);
    await user.type(picker, "cotton");
    expect(await screen.findByRole("option", { name: "Cotton Wool" })).toBeInTheDocument();
  });

  it("stacks on a phone and goes two-up from the small breakpoint", async () => {
    renderAs("hospital_admin");
    const name = await screen.findByLabelText(/^Name/);
    const grid = name.closest(".grid");
    expect(grid.className).toMatch(/\bsm:grid-cols-2\b/);
    expect(grid.className).not.toMatch(/(^|\s)grid-cols-2\b/);   // one column below `sm`
  });
});

describe("the existing Inventory tabs are unchanged", () => {
  it("still open their own panels", async () => {
    const user = userEvent.setup();
    renderAs("hospital_admin", "/inventory");
    await user.click(tab("Receive stock"));
    expect(await screen.findByText("Receive a delivery")).toBeInTheDocument();
    expect(screen.queryByText("New medical item")).not.toBeInTheDocument();
    for (const name of ["Transfer", "Physical count", "Import / export", "Movement log",
                        "Stock on hand"]) {
      await user.click(tab(name));
      expect(tab(name)).toHaveAttribute("aria-current", "page");
    }
  });

  it("gives the inventory manager every tab it had", () => {
    renderAs("inventory_manager", "/inventory");
    expect(tabNames()).toEqual(["Stock on hand", "Receive stock", "Transfer", "Physical count",
                                "Import / export", "Movement log"]);
  });
});
