import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import { CountImportExport } from "./StockPanels.jsx";

// Validate → preview → confirm → apply. The screen's job is to make the
// preview impossible to skip: a file with a problem cannot be applied, and a
// clean one is applied only after a confirmation that says what will move.

const PHARMACY = { id: 2, name: "Pharmacy", is_dispensing_point: true };

const previewOf = (overrides = {}) => ({
  id: 9, reference: "IMP-000009", filename: "count.csv", status: "previewed", is_applicable: true,
  uploaded_by_name: "Ngozi", created_at: "2026-09-11T09:00:00Z",
  summary: { rows_read: 2, counted: 2, not_counted: 0, unchanged: 0, increase_lines: 1, units_added: 8,
             decrease_lines: 1, units_removed: 8, locations: ["Pharmacy"] },
  errors: [],
  rows: [
    { row: 2, line: 11, item_name: "Paracetamol 500mg", sku: "PARA-500", batch_no: "P1", expiry: "2027-01-01",
      location_name: "Pharmacy", system_quantity: 100, counted_quantity: 92, difference: -8 },
    { row: 3, line: 12, item_name: "Amoxicillin 250mg", sku: "", batch_no: "A1", expiry: "2027-02-01",
      location_name: "Pharmacy", system_quantity: 100, counted_quantity: 108, difference: 8 },
  ],
  counts: [],
  ...overrides,
});

beforeEach(() => {
  vi.spyOn(api, "get").mockImplementation(() => Promise.resolve({ data: [] }));
});
afterEach(() => vi.restoreAllMocks());

async function uploadFile(user) {
  const file = new File(["Line ID,Product\n11,Paracetamol 500mg\n"], "count.csv", { type: "text/csv" });
  await user.upload(screen.getByLabelText("Counted CSV file"), file);
  await user.click(screen.getByRole("button", { name: "Upload and preview" }));
}

describe("the CSV stock count", () => {
  it("pins the export to the pharmacy shelf when the workspace is locked to it", () => {
    renderWithApp(<CountImportExport locations={[PHARMACY]} lockedLocation={PHARMACY} />, { user: { role: "pharmacist" } });
    expect(screen.getByLabelText("Location")).toHaveValue("Pharmacy");
    expect(screen.getByLabelText("Location")).toBeDisabled();
  });

  it("shows every problem in a broken file and will not apply it", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: previewOf({ is_applicable: false, rows: [],
                        errors: [{ row: 2, column: "Counted Qty", message: 'Counted Qty "abc" must be a whole number, 0 or more.' }] }),
    });
    const user = userEvent.setup();
    renderWithApp(<CountImportExport locations={[PHARMACY]} lockedLocation={PHARMACY} />, { user: { role: "pharmacist" } });

    await uploadFile(user);
    expect(await screen.findByText(/1 problem — nothing can be applied from this file/)).toBeInTheDocument();
    expect(screen.getByText('Counted Qty "abc" must be a whole number, 0 or more.')).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply count…" })).toBeDisabled();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe("/stock-count-imports/");
  });

  it("previews the differences and applies only after confirming", async () => {
    const post = vi.spyOn(api, "post").mockImplementation((url) => Promise.resolve({
      data: url.endsWith("/apply/")
        ? previewOf({ status: "applied", applied_by_name: "Ngozi", applied_at: "2026-09-11T09:05:00Z",
                      counts: [{ id: 4, reference: "CNT-000004", location: 2 }] })
        : previewOf(),
    }));
    const user = userEvent.setup();
    renderWithApp(<CountImportExport locations={[PHARMACY]} lockedLocation={PHARMACY} />, { user: { role: "pharmacist" } });

    await uploadFile(user);
    expect(await screen.findByText("Paracetamol 500mg")).toBeInTheDocument();
    expect(screen.getByText("-8")).toBeInTheDocument();
    // Once on the "Increases" figure, once on the line that goes up.
    expect(screen.getAllByText("+8")).toHaveLength(2);
    expect(post).toHaveBeenCalledTimes(1);   // uploading applied nothing

    await user.click(screen.getByRole("button", { name: "Apply count…" }));
    expect(await screen.findByText("Apply stock count IMP-000009?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Apply count" }));

    await waitFor(() => expect(post).toHaveBeenLastCalledWith("/stock-count-imports/9/apply/"));
    expect(await screen.findByText(/Posted as CNT-000004/)).toBeInTheDocument();
  });
});
