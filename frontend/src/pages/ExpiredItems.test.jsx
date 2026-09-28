import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import { ReceiveStock, StockOnHand } from "../components/StockPanels.jsx";
import ExpiredItems from "./ExpiredItems.jsx";

// Expired Items is a register of *lots*: the server decides what is expired
// (`/stock-records/expired/`), the page lists it, and the actions — write off,
// mark a batch expired, return one to use — are the inventory administrator's.

const LOCATIONS = [
  { id: 1, name: "Main Store", code: "main-store", is_default_receiving: true },
  { id: 2, name: "Pharmacy", code: "pharmacy", is_dispensing_point: true },
];

const expiredLine = (overrides) => ({
  id: 10, batch: 11, batch_no: "A", item: 1, item_name: "Paracetamol 500mg",
  item_sku: "PH-PARA-500", item_unit: "tab", location: 2, location_name: "Pharmacy",
  location_code: "pharmacy", quantity: 30, expiry_date: "2026-09-01", is_expired: true,
  expiry_status: "expired", cost_price: "8.00", sale_price: "15.00", supplier: "Emzor",
  received_date: "2026-01-10", marked_expired_at: null, marked_expired_by_name: null,
  marked_expired_reason: "",
  ...overrides,
});

let register;

function mockApi() {
  return vi.spyOn(api, "get").mockImplementation((url, config = {}) => {
    if (url === "/stock-records/expired/") {
      return Promise.resolve({ data: { count: register.length, results: register,
                                       units: register.reduce((t, r) => t + r.quantity, 0) } });
    }
    if (url === "/stock-locations/") return Promise.resolve({ data: LOCATIONS });
    if (url === "/stock-movements/") {
      return Promise.resolve({ data: [{ id: 1, created_at: "2026-01-10T09:00:00Z",
                                        reason_label: "Stock received", change: 30,
                                        location_name: "Pharmacy", performed_by_name: "Stores" }] });
    }
    if (url === "/batches/" && config.params?.usable) {
      return Promise.resolve({ data: [{ id: 12, item_name: "Amoxicillin 500mg", batch_no: "AM-1",
                                        expiry_date: "2027-05-01", total_quantity: 40,
                                        item_unit: "cap" }] });
    }
    if (url === "/items/") {
      return Promise.resolve({ data: [{ id: 1, name: "Paracetamol 500mg" },
                                      { id: 3, name: "Amoxicillin (new)" }] });
    }
    return Promise.resolve({ data: [] });
  });
}

beforeEach(() => {
  register = [
    expiredLine({}),
    expiredLine({ id: 11, batch: 12, batch_no: "M", expiry_date: "2027-03-01",
                  expiry_status: "marked_expired", marked_expired_at: "2026-09-20T10:00:00Z",
                  marked_expired_by_name: "Hospital Admin", marked_expired_reason: "Recalled" }),
  ];
  mockApi();
});
afterEach(() => vi.restoreAllMocks());

const rowFor = async (batchNo) => (await screen.findByText(batchNo, { selector: "td" })).closest("tr");

describe("Expired Items", () => {
  it("lists expired lots with item, batch, location, quantity, expiry date and why", async () => {
    renderWithApp(<ExpiredItems />, { user: { role: "hospital_admin" } });
    const past = await rowFor("A");
    for (const text of ["Paracetamol 500mg", "Pharmacy", "30", "2026-09-01", "Past expiry date"]) {
      expect(within(past).getByText(text)).toBeInTheDocument();
    }
    expect(within(await rowFor("M")).getByText("Marked expired")).toBeInTheDocument();
  });

  it("opens a batch's details and movement history", async () => {
    const user = userEvent.setup();
    renderWithApp(<ExpiredItems />, { user: { role: "hospital_admin" } });
    await user.click(within(await rowFor("M")).getByRole("button", { name: "Details" }));
    expect(await screen.findByText(/Recalled/)).toBeInTheDocument();
    expect(await screen.findByText("Stock received")).toBeInTheDocument();
    expect(screen.getByText("Emzor")).toBeInTheDocument();
  });

  it("writes a lot off at its location after confirming", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderWithApp(<ExpiredItems />, { user: { role: "hospital_admin" } });
    await user.click(within(await rowFor("A")).getByRole("button", { name: "Write off" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Write it off" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/batches/11/write_off/", { location: 2 }));
  });

  it("marks a usable batch expired with a reason", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: { item_name: "Amoxicillin 500mg", batch_no: "AM-1" } });
    const user = userEvent.setup();
    renderWithApp(<ExpiredItems />, { user: { role: "hospital_admin" } });
    await user.click(await screen.findByRole("button", { name: "Mark a batch expired" }));
    const dialog = screen.getByRole("dialog");
    const submit = within(dialog).getByRole("button", { name: "Mark expired" });
    expect(submit).toBeDisabled();   // a batch and a reason are both required

    await waitFor(() => expect(within(dialog).getByRole("option", { name: /AM-1/ })).toBeInTheDocument());
    await user.selectOptions(within(dialog).getByLabelText(/^Batch/), "12");
    await user.type(within(dialog).getByLabelText(/^Reason/), "Cold chain broken");
    await user.click(submit);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/batches/12/mark-expired/",
                                                          { reason: "Cold chain broken" }));
  });

  it("returns a manually marked batch to use, with a reason", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderWithApp(<ExpiredItems />, { user: { role: "hospital_admin" } });
    expect(within(await rowFor("A")).queryByRole("button", { name: "Return to use" }))
      .not.toBeInTheDocument();   // a date is arithmetic, not a decision
    await user.click(within(await rowFor("M")).getByRole("button", { name: "Return to use" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Why it is usable again/), "Recall lifted");
    await user.click(within(dialog).getByRole("button", { name: "Return to use" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/batches/12/return-to-use/",
                                                          { reason: "Recall lifted" }));
  });

  it("says so when nothing is expired", async () => {
    register = [];
    renderWithApp(<ExpiredItems />, { user: { role: "hospital_admin" } });
    expect(await screen.findByText("Nothing expired")).toBeInTheDocument();
  });
});

describe("who changes stock on the shared stock panels", () => {
  const records = [{
    id: 1, batch: 11, batch_no: "A", item: 1, item_name: "Paracetamol 500mg", item_unit: "tab",
    item_sku: "", item_category_name: "", location: 2, location_code: "pharmacy",
    location_name: "Pharmacy", quantity: 30, expiry_date: "2026-09-01", is_expired: true,
    expiry_status: "expired",
  }];
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "get").mockImplementation((url) => Promise.resolve({
      data: url === "/stock-records/" ? records : [] }));
  });

  it("shows a pharmacist the shelf with no count or write-off controls", async () => {
    renderWithApp(<StockOnHand locations={LOCATIONS} lockedLocation={LOCATIONS[1]} />,
                  { user: { role: "pharmacist" } });
    const row = (await screen.findByText("Paracetamol 500mg", { selector: "td" })).closest("tr");
    expect(within(row).getByText("Expired")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Count" })).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Write off" })).not.toBeInTheDocument();
  });

  it("gives the inventory manager and the hospital admin both", async () => {
    for (const role of ["inventory_manager", "hospital_admin"]) {
      const { unmount } = renderWithApp(<StockOnHand locations={LOCATIONS} />, { user: { role } });
      const row = (await screen.findByText("Paracetamol 500mg", { selector: "td" })).closest("tr");
      expect(within(row).getByRole("button", { name: "Count" })).toBeInTheDocument();
      expect(within(row).getByRole("button", { name: "Write off" })).toBeInTheDocument();
      unmount();
    }
  });
});

describe("Receive a delivery is unchanged", () => {
  beforeEach(() => { mockApi(); });

  it("offers a newly created product and posts the same delivery it always did", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderWithApp(<ReceiveStock locations={LOCATIONS} store={LOCATIONS[0]} />,
                  { user: { role: "hospital_admin" } });

    await user.click(screen.getByLabelText(/^Medical item/));
    await user.click(await screen.findByRole("option", { name: "Amoxicillin (new)" }));
    await user.type(screen.getByLabelText(/^Batch number/), "AMX-01");
    await user.type(screen.getByLabelText(/^Quantity/), "120");
    await user.type(screen.getByLabelText(/^Cost price/), "10");
    await user.type(screen.getByLabelText(/^Sale price/), "20");
    await user.type(screen.getByLabelText(/^Expiry date/), "2027-09-30");
    await user.type(screen.getByLabelText(/^Supplier/), "Emzor");
    await user.selectOptions(screen.getByLabelText(/^Receive into/), "2");
    await user.click(screen.getByRole("button", { name: /Receive/ }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith("/batches/", expect.objectContaining({
      item: 3, batch_no: "AMX-01", opening_quantity: 120, cost_price: "10", sale_price: "20",
      expiry_date: "2027-09-30", supplier: "Emzor", location: 2,
    }));
  });
});
