import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import DischargedPatients from "./DischargedPatients.jsx";

// The register behind the workspace: every completed discharge, the record
// read back, and its letter.
//
// These are the same `DischargeSummary` rows the ward writes — there is no
// separate administrative discharge record — so what is asserted here is that
// the screen reads them, narrows them in the database rather than the browser,
// and never offers a way to edit a signed discharge.

const SUPER_ADMIN = { id: 1, role: "admin" };

const discharge = (over = {}) => ({
  id: 7, admission: 4, reference: "DCH-000007",
  patient: 3, patient_name: "Okoro, Ada", patient_number: "NMHS-P000012",
  patient_uuid: "8b0e5f1e-1111-4000-8000-000000000000",
  admission_reference: "ADM-000004", ward_name: "Maternity", bed_number: "M1",
  admitted_at: "2026-09-14T09:00:00Z", discharged_at: "2026-09-18T11:30:00Z",
  admission_status: "discharged", completed_by_name: "Ngozi Admin",
  condition: "Recovered", diagnosis: "Pneumonia, resolved",
  summary: "Admitted with fever. Four days of IV antibiotics.",
  instructions: "Complete the oral course.", follow_up: "2026-10-02",
  ...over,
});

const ADMISSION = {
  id: 4, patient_name: "Okoro, Ada", patient_number: "NMHS-P000012",
  patient_sex: "Female", patient_age: "31 years", reference: "ADM-000004",
  ward_name: "Maternity", bed_number: "M1", admitted_at: "2026-09-14T09:00:00Z",
  discharged_at: "2026-09-18T11:30:00Z", status: "discharged",
  status_label: "Discharged", length_of_stay: "4 days",
  attending_doctor_name: "Dr Chidi Nwosu", admitted_by_name: "Nurse Bisi",
  diagnosis: "Fever for investigation",
};

function mockRegister(rows = [discharge()], { transfers = [] } = {}) {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/admin-discharges/") return Promise.resolve({ data: rows });
    if (url === "/admissions/4/") return Promise.resolve({ data: ADMISSION });
    if (url === "/bed-transfers/") return Promise.resolve({ data: transfers });
    return Promise.resolve({ data: [] });
  });
}

afterEach(() => vi.restoreAllMocks());

describe("the register", () => {
  it("lists a completed discharge with both references", async () => {
    mockRegister();
    renderWithApp(<DischargedPatients />, { user: SUPER_ADMIN });

    expect(await screen.findByText("Okoro, Ada")).toBeInTheDocument();
    expect(screen.getByText("NMHS-P000012")).toBeInTheDocument();
    expect(screen.getByText("ADM-000004")).toBeInTheDocument();
    expect(screen.getByText("DCH-000007")).toBeInTheDocument();
    expect(screen.getByText("Ngozi Admin")).toBeInTheDocument();
    expect(screen.getByText("Recovered")).toBeInTheDocument();
  });

  it("offers View and the discharge letter on each row", async () => {
    mockRegister();
    renderWithApp(<DischargedPatients />, { user: SUPER_ADMIN });
    const row = (await screen.findByText("Okoro, Ada")).closest("tr");
    expect(within(row).getByRole("button", { name: "View" })).toBeInTheDocument();
    expect(within(row).getByText(/Discharge letter/)).toBeInTheDocument();
  });

  it("narrows in the database, sending the filters as query parameters", async () => {
    const user = userEvent.setup();
    mockRegister();
    renderWithApp(<DischargedPatients />, { user: SUPER_ADMIN });
    await screen.findByText("Okoro, Ada");

    await user.type(screen.getByLabelText("Search"), "DCH-000007");
    await vi.waitFor(() => {
      const call = api.get.mock.calls.filter(([url]) => url === "/admin-discharges/").at(-1);
      expect(call[1].params).toMatchObject({ search: "DCH-000007" });
    });
  });

  it("tells an empty register apart from filters that match nothing", async () => {
    // Two different problems, and the difference is the whole point: an empty
    // register is waiting for a discharge, while filters that match nothing
    // are hiding one. Saying "No discharges found" to both left somebody
    // widening a date range that was never narrow.
    const user = userEvent.setup();
    mockRegister([]);
    renderWithApp(<DischargedPatients />, { user: SUPER_ADMIN });

    expect(await screen.findByText("No discharges yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Discharge a patient/ })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Search"), "nobody");
    expect(await screen.findByText("No discharges match these filters")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Clear/ }).length).toBeGreaterThan(0);
  });

  it("counts the filters that are narrowing the list, and clears them", async () => {
    const user = userEvent.setup();
    mockRegister();
    renderWithApp(<DischargedPatients />, { user: SUPER_ADMIN });
    await screen.findByText("Okoro, Ada");

    await user.type(screen.getByLabelText("Search"), "Ada");
    await user.selectOptions(screen.getByLabelText("Admission status"), "discharged");
    const clear = await screen.findByRole("button", { name: "Clear 2 filters" });

    await user.click(clear);
    expect(screen.getByLabelText("Search")).toHaveValue("");
    expect(screen.queryByRole("button", { name: /^Clear \d/ })).not.toBeInTheDocument();
  });
});

describe("opening a discharged patient", () => {
  async function open(user) {
    const row = (await screen.findByText("Okoro, Ada")).closest("tr");
    await user.click(within(row).getByRole("button", { name: "View" }));
  }

  it("shows DISCHARGED with the date, and the whole record", async () => {
    const user = userEvent.setup();
    mockRegister();
    renderWithApp(<DischargedPatients />, { user: SUPER_ADMIN });
    await open(user);

    expect(await screen.findByText("DISCHARGED")).toBeInTheDocument();
    expect(screen.getByText("Pneumonia, resolved")).toBeInTheDocument();
    expect(screen.getByText("Admitted with fever. Four days of IV antibiotics."))
      .toBeInTheDocument();
    expect(screen.getByText("Complete the oral course.")).toBeInTheDocument();
  });

  it("shows the admission history behind the discharge", async () => {
    const user = userEvent.setup();
    mockRegister();
    renderWithApp(<DischargedPatients />, { user: SUPER_ADMIN });
    await open(user);

    expect(await screen.findByText("Admission history")).toBeInTheDocument();
    expect(screen.getByText("4 days")).toBeInTheDocument();
    expect(screen.getByText("Dr Chidi Nwosu")).toBeInTheDocument();
    expect(screen.getByText("Bed history")).toBeInTheDocument();
  });

  it("offers the letter from the opened record", async () => {
    const user = userEvent.setup();
    mockRegister();
    renderWithApp(<DischargedPatients />, { user: SUPER_ADMIN });
    await open(user);
    expect(await screen.findByText(/Print discharge letter/)).toBeInTheDocument();
  });

  it("is read-only — a signed discharge is never edited in place", async () => {
    const user = userEvent.setup();
    mockRegister();
    renderWithApp(<DischargedPatients />, { user: SUPER_ADMIN });
    await open(user);
    await screen.findByText("DISCHARGED");

    // No form controls over the record itself, and it says why.
    expect(screen.queryByRole("textbox", { name: /diagnosis/i })).not.toBeInTheDocument();
    expect(screen.getByText(/historical record and is not edited/)).toBeInTheDocument();
  });
});
