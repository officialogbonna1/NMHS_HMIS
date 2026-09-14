import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import { CountImportExport, StockOnHand } from "./StockPanels.jsx";

// The category on the stock screens: a way of narrowing what you are looking
// at, and a label on the line. It never changes a quantity — the panel's
// actions still post the same counts and write-offs they always did — and the
// CSV import reports a category it does not recognise rather than creating it.

const PHARMACY = { id: 2, name: "Pharmacy", code: "pharmacy", is_dispensing_point: true };
const STORE = { id: 1, name: "Main Store", code: "main-store" };

const CATEGORIES = [
  { id: 5, name: "Pain Relief / Analgesics", item_count: 3 },
  { id: 6, name: "Antibiotics", item_count: 2 },
];

const line = (overrides) => ({
  id: 1, batch: 11, batch_no: "B001", item: 1, item_name: "Paracetamol 500mg",
  item_unit: "tablet", item_sku: "PARA500", item_category: 5,
  item_category_name: "Pain Relief / Analgesics",
  location: 2, location_code: "pharmacy", location_name: "Pharmacy",
  quantity: 100, expiry_date: "2027-04-30", is_expired: false,
  ...overrides,
});

const RECORDS = [
  line({}),
  line({ id: 2, batch: 12, batch_no: "B002", item: 2, item_name: "Amoxicillin 500mg",
         item_unit: "capsule", item_sku: "AMOX500", item_category: 6,
         item_category_name: "Antibiotics", quantity: 50 }),
];

/** The API as these panels see it, with the stock list honouring the filter. */
function mockApi({ records = RECORDS } = {}) {
  return vi.spyOn(api, "get").mockImplementation((url, config = {}) => {
    const params = config.params ?? {};
    if (url === "/stock-records/") {
      const wanted = params.batch__item__category;
      return Promise.resolve({
        data: wanted
          ? records.filter((r) => String(r.item_category) === String(wanted))
          : records,
      });
    }
    if (url === "/item-categories/") return Promise.resolve({ data: CATEGORIES });
    if (url === "/items/") return Promise.resolve({ data: [] });
    return Promise.resolve({ data: [] });
  });
}

afterEach(() => vi.restoreAllMocks());

describe("stock on hand, by category", () => {
  beforeEach(() => { mockApi(); });

  it("shows the product, its category, SKU, unit, location, batch, expiry, quantity and status", async () => {
    renderWithApp(<StockOnHand locations={[STORE, PHARMACY]} />, { user: { role: "inventory_manager" } });

    const row = (await screen.findByText("Paracetamol 500mg")).closest("tr");
    for (const cell of ["Pain Relief / Analgesics", "PARA500", "tablet", "Pharmacy",
                        "B001", "2027-04-30", "100", "In stock"]) {
      expect(within(row).getByText(cell)).toBeInTheDocument();
    }
  });

  it("narrows the list to one category, on the server rather than in the browser", async () => {
    const user = userEvent.setup();
    renderWithApp(<StockOnHand locations={[STORE, PHARMACY]} />, { user: { role: "inventory_manager" } });

    await screen.findByText("Paracetamol 500mg");
    expect(screen.getByText("Amoxicillin 500mg")).toBeInTheDocument();

    await user.selectOptions(await screen.findByLabelText("Category"), "6");

    await waitFor(() => expect(screen.queryByText("Paracetamol 500mg")).not.toBeInTheDocument());
    expect(screen.getByText("Amoxicillin 500mg")).toBeInTheDocument();
    // The filter went to the API — filtering a page of 500 rows in the browser
    // would hide whatever did not arrive on it.
    expect(api.get).toHaveBeenCalledWith("/stock-records/",
      expect.objectContaining({ params: expect.objectContaining({ batch__item__category: "6" }) }));
  });

  it("searches the same list by product, SKU, category or batch", async () => {
    const user = userEvent.setup();
    renderWithApp(<StockOnHand locations={[STORE, PHARMACY]} />, { user: { role: "inventory_manager" } });

    await screen.findByText("Paracetamol 500mg");
    await user.type(screen.getByPlaceholderText("Product, SKU, category or batch…"), "AMOX500");

    await waitFor(() => expect(screen.queryByText("Paracetamol 500mg")).not.toBeInTheDocument());
    expect(screen.getByText("Amoxicillin 500mg")).toBeInTheDocument();
  });

  it("flags an expired line and one that is running out of date", async () => {
    const soon = new Date(Date.now() + 10 * 864e5).toISOString().slice(0, 10);
    vi.restoreAllMocks();
    mockApi({ records: [
      line({ id: 3, item_name: "Old lot", is_expired: true }),
      line({ id: 4, item_name: "Short dated", expiry_date: soon }),
    ] });

    renderWithApp(<StockOnHand locations={[STORE, PHARMACY]} />, { user: { role: "pharmacist" } });

    const expired = (await screen.findByText("Old lot")).closest("tr");
    expect(within(expired).getByText("Expired")).toBeInTheDocument();
    const short = screen.getByText("Short dated").closest("tr");
    expect(within(short).getByText(/Expires in \d+d/)).toBeInTheDocument();
  });

  it("says so when a category has nothing in it, instead of looking broken", async () => {
    const user = userEvent.setup();
    vi.restoreAllMocks();
    mockApi({ records: [line({})] });   // nothing under Antibiotics

    renderWithApp(<StockOnHand locations={[STORE, PHARMACY]} />, { user: { role: "inventory_manager" } });
    await screen.findByText("Paracetamol 500mg");
    await user.selectOptions(await screen.findByLabelText("Category"), "6");

    expect(await screen.findByText("Nothing matches that")).toBeInTheDocument();
    // and it says which category is empty, rather than just going blank
    expect(screen.getByText(/No stock here under Antibiotics/)).toBeInTheDocument();
  });
});

describe("the CSV count and categories", () => {
  const preview = (summary, rows = []) => ({
    id: 9, reference: "IMP-000009", filename: "count.csv", status: "previewed",
    is_applicable: false, uploaded_by_name: "Ngozi", created_at: "2026-09-11T09:00:00Z",
    summary, errors: [], rows, counts: [],
  });

  beforeEach(() => { mockApi(); });

  it("names a category it did not recognise and says nothing was created", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post").mockResolvedValue({
      data: preview({ rows_read: 1, counted: 0, unknown_categories: ["Pain Relif"] }),
    });

    renderWithApp(<CountImportExport locations={[PHARMACY]} lockedLocation={PHARMACY} />,
                  { user: { role: "pharmacist" } });

    const file = new File(["Line ID,Product\n11,Paracetamol\n"], "count.csv", { type: "text/csv" });
    await user.upload(screen.getByLabelText("Counted CSV file"), file);
    await user.click(screen.getByRole("button", { name: "Upload and preview" }));

    expect(await screen.findByText(/1 category name was not recognised/)).toBeInTheDocument();
    expect(screen.getByText(/Pain Relif/)).toBeInTheDocument();
    expect(screen.getByText(/never creates a category/)).toBeInTheDocument();
  });

  it("shows the category of every counted line in the preview", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post").mockResolvedValue({
      data: preview(
        { rows_read: 1, counted: 1, unchanged: 0, increase_lines: 0, decrease_lines: 1,
          units_removed: 8, locations: ["Pharmacy"], categories: ["Pain Relief / Analgesics"],
          unknown_categories: [] },
        [{ row: 2, line: 11, item_name: "Paracetamol 500mg", sku: "PARA500",
           category_name: "Pain Relief / Analgesics", batch_no: "B001", expiry: "2027-04-30",
           location_name: "Pharmacy", system_quantity: 100, counted_quantity: 92, difference: -8 }],
      ),
    });

    renderWithApp(<CountImportExport locations={[PHARMACY]} lockedLocation={PHARMACY} />,
                  { user: { role: "pharmacist" } });

    const file = new File(["Line ID,Product\n11,Paracetamol\n"], "count.csv", { type: "text/csv" });
    await user.upload(screen.getByLabelText("Counted CSV file"), file);
    await user.click(screen.getByRole("button", { name: "Upload and preview" }));

    const row = (await screen.findByText("Paracetamol 500mg")).closest("tr");
    expect(within(row).getByText("Pain Relief / Analgesics")).toBeInTheDocument();
    expect(within(row).getByText("−8".replace("−", "-"))).toBeInTheDocument();
  });

  it("offers the export one category at a time", async () => {
    renderWithApp(<CountImportExport locations={[PHARMACY]} lockedLocation={PHARMACY} />,
                  { user: { role: "pharmacist" } });

    const picker = await screen.findByLabelText("Category");
    await waitFor(() => expect(within(picker).getByText("Antibiotics")).toBeInTheDocument());
    expect(within(picker).getByText("Every category")).toBeInTheDocument();
  });
});
