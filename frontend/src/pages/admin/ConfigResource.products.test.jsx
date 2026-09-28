import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../../api/client";
import { renderWithApp } from "../../test/harness.jsx";
import ConfigResource from "./ConfigResource.jsx";

// Administration → Products: the Hospital Admin's front door onto the same
// `inventory.Item` rows Django admin's Add item form writes. Create, edit,
// archive, restore and delete — where deleting is offered only when the server
// says nothing points at the row, and a 409 is shown as the server wrote it.

const CATEGORIES = [{ id: 5, name: "Analgesics" }];
const UNITS = [{ id: 7, name: "Tablet" }];

const item = (overrides) => ({
  id: 1, name: "Paracetamol", strength: "500 mg", dosage_form: "Tablet",
  category: 5, category_name: "Analgesics", unit: 7, unit_label: "tab",
  sku: "PH-PARA-500", barcode: "6001234500012", reorder_threshold: 25,
  is_active: true, total_quantity: 500, is_low_stock: false,
  by_location: [{ location: 1, code: "main-store", name: "Main Store", quantity: 400 },
                { location: 2, code: "pharmacy", name: "Pharmacy", quantity: 100 }],
  references: { batches: 2 }, is_deletable: false,
  ...overrides,
});

let rows;

function mockApi() {
  return vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/items/") return Promise.resolve({ data: rows });
    if (url === "/item-categories/") return Promise.resolve({ data: CATEGORIES });
    if (url === "/units/") return Promise.resolve({ data: UNITS });
    return Promise.resolve({ data: [] });
  });
}

function renderProducts(role = "hospital_admin") {
  return renderWithApp(
    <Routes><Route path="/admin/:resource" element={<ConfigResource />} /></Routes>,
    { user: { role }, route: "/admin/products" },
  );
}

const rowFor = async (name) => (await screen.findByText(name)).closest("tr");

beforeEach(() => {
  rows = [item({}), item({ id: 2, name: "Crepe bandage", strength: "", dosage_form: "",
                           sku: null, barcode: null, total_quantity: 0, by_location: [],
                           references: {}, is_deletable: true })];
  mockApi();
});
afterEach(() => vi.restoreAllMocks());

describe("Administration → Products", () => {
  it("lists SKU, barcode, category, unit, stock by location and reorder threshold", async () => {
    renderProducts();
    const row = await rowFor("Paracetamol · 500 mg · Tablet");
    for (const text of ["PH-PARA-500", "6001234500012", "Analgesics", "tab", "500",
                        "Main Store 400 · Pharmacy 100", "25", "Active"]) {
      expect(within(row).getByText(text)).toBeInTheDocument();
    }
  });

  it("adds a product with the Django admin form's fields, grouped the same way", async () => {
    const post = vi.spyOn(api, "post").mockImplementation((url, body) => {
      rows = [...rows, item({ id: 3, ...body, is_deletable: true })];
      return Promise.resolve({ data: { id: 3, ...body } });
    });
    const user = userEvent.setup();
    renderProducts();
    await user.click(await screen.findByRole("button", { name: "+ New product" }));

    for (const section of ["Basic information", "Counter identifiers", "Reordering"]) {
      expect(screen.getByText(section, { selector: "legend" })).toBeInTheDocument();
    }
    await user.type(screen.getByLabelText(/^Name/), "Amoxicillin");
    await user.type(screen.getByLabelText(/^Strength/), "500 mg");
    await user.type(screen.getByLabelText(/^Dosage form/), "Capsule");
    await waitFor(() => expect(screen.getByRole("option", { name: "Analgesics" })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText(/^Category/), "5");
    await user.selectOptions(screen.getByLabelText(/^Unit/), "7");
    await user.type(screen.getByLabelText(/^SKU/), "PH-AMOX");
    await user.clear(screen.getByLabelText(/^Reorder threshold/));
    await user.type(screen.getByLabelText(/^Reorder threshold/), "15");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith("/items/", {
      name: "Amoxicillin", strength: "500 mg", dosage_form: "Capsule", category: 5, unit: 7,
      is_active: true, sku: "PH-AMOX", barcode: "", reorder_threshold: 15,
    });
    // The list reloads, and the new product is in it.
    expect(await screen.findByText("Amoxicillin · 500 mg · Capsule")).toBeInTheDocument();
  });

  it("shows the server's validation, e.g. a SKU another product already holds", async () => {
    vi.spyOn(api, "post").mockRejectedValue({
      response: { status: 400, data: { sku: ["item with this sku already exists."] } },
    });
    const user = userEvent.setup();
    renderProducts();
    await user.click(await screen.findByRole("button", { name: "+ New product" }));
    await user.type(screen.getByLabelText(/^Name/), "Duplicate");
    await user.type(screen.getByLabelText(/^SKU/), "PH-PARA-500");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
  });

  it("edits a product with a PATCH of the same fields", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderProducts();
    const row = await rowFor("Paracetamol · 500 mg · Tablet");
    await user.click(within(row).getByRole("button", { name: "Edit" }));

    const threshold = screen.getByLabelText(/^Reorder threshold/);
    expect(threshold).toHaveValue(25);
    await user.clear(threshold);
    await user.type(threshold, "50");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][0]).toBe("/items/1/");
    expect(patch.mock.calls[0][1]).toMatchObject({ reorder_threshold: 50, sku: "PH-PARA-500",
                                                   strength: "500 mg" });
  });

  it("archives through the named action after confirming, and restores the same way", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderProducts();
    const row = await rowFor("Paracetamol · 500 mg · Tablet");
    await user.click(within(row).getByRole("button", { name: "Archive" }));
    expect(await screen.findByText("Archive Paracetamol?")).toBeInTheDocument();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/items/1/archive/"));

    rows = [item({ is_active: false })];
    await user.click(screen.getByRole("button", { name: "Show archived" }));
    const archived = await rowFor("Paracetamol · 500 mg · Tablet");
    expect(within(archived).getByText("Archived")).toBeInTheDocument();
    await user.click(within(archived).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/items/1/restore/"));
  });

  it("offers Delete only where the server says nothing points at the product", async () => {
    renderProducts();
    const used = await rowFor("Paracetamol · 500 mg · Tablet");
    expect(within(used).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(within(used).getByText(/archive it instead/)).toBeInTheDocument();
    const unused = await rowFor("Crepe bandage");
    expect(within(unused).getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("asks before deleting, and shows the server's refusal when history appeared meanwhile", async () => {
    const del = vi.spyOn(api, "delete").mockRejectedValue({
      response: { status: 409, data: {
        detail: "Crepe bandage is used by 1 batches, so deleting it would break records that "
          + "already exist. Deactivate it instead.", code: "in_use" } },
    });
    const user = userEvent.setup();
    renderProducts();
    const row = await rowFor("Crepe bandage");
    await user.click(within(row).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Delete this permanently?")).toBeInTheDocument();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/items/2/"));
    expect(await screen.findByText(/would break records that already exist/)).toBeInTheDocument();
  });
});
