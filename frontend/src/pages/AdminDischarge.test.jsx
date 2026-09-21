import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import AdminDischarge from "./AdminDischarge.jsx";

// The Admin Discharge workspace: see who is on a ward, read the admission,
// discharge, and be stopped cleanly if it has already been done.
//
// A second *door* onto the ward's discharge, never a second system — every
// write posts to `/admin-discharges/discharge/`, which runs the same
// `inpatient/services.discharge_patient` the bed board calls. These tests
// assert the screen, and that it sends what the one service expects.

const SUPER_ADMIN = { id: 1, role: "admin", first_name: "Ngozi", last_name: "Admin" };

const admission = (over = {}) => ({
  id: 4, patient: 3, patient_name: "Okoro, Ada", patient_number: "NMHS-P000012",
  patient_sex: "Female", patient_age: "31 years",
  reference: "ADM-000004", ward_name: "Maternity", bed_number: "M1",
  admitted_at: "2026-09-14T09:00:00Z", discharged_at: null,
  status: "admitted", status_label: "Admitted",
  length_of_stay: "2 days", length_of_stay_days: 2,
  attending_doctor_name: "Dr Chidi Nwosu", admitted_by_name: "Nurse Bisi",
  diagnosis: "Fever for investigation",
  discharge_reference: "", discharge_id: null,
  ...over,
});

const WARDS = [{ id: 1, name: "Maternity" }, { id: 2, name: "Male Medical" }];

function mockWard(rows = [admission()], { transfers = [] } = {}) {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/admin-discharges/dischargeable/") return Promise.resolve({ data: rows });
    if (url === "/wards/") return Promise.resolve({ data: WARDS });
    if (url === "/bed-transfers/") return Promise.resolve({ data: transfers });
    return Promise.resolve({ data: [] });
  });
}

/** Fill the discharge form to the point the button enables. */
async function fillDischarge(user) {
  await user.type(screen.getByLabelText(/Discharge diagnosis/), "Pneumonia, resolved");
  await user.type(screen.getByLabelText(/Summary of the admission/), "Four days of IV antibiotics.");
}

afterEach(() => vi.restoreAllMocks());

describe("the ward list", () => {
  it("shows what the desk needs to pick a patient", async () => {
    mockWard();
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });

    expect(await screen.findByText("Okoro, Ada")).toBeInTheDocument();
    expect(screen.getByText("NMHS-P000012")).toBeInTheDocument();
    expect(screen.getByText("ADM-000004")).toBeInTheDocument();
    // Ward and bed are stacked in one cell: the ward reads first and the bed
    // beneath it, so a long ward name never pushes the row sideways.
    const row = screen.getByText("Okoro, Ada").closest("tr");
    expect(within(row).getByText("Maternity")).toBeInTheDocument();
    expect(within(row).getByText("Bed M1")).toBeInTheDocument();
    expect(screen.getByText("2 days")).toBeInTheDocument();
    expect(screen.getByText("Dr Chidi Nwosu")).toBeInTheDocument();
    // "Admitted" is both a column heading and the status badge.
    expect(screen.getAllByText("Admitted").length).toBeGreaterThan(1);
  });

  it("offers both View and Discharge on a row", async () => {
    mockWard();
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    const row = (await screen.findByText("Okoro, Ada")).closest("tr");
    expect(within(row).getByRole("button", { name: "View" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Discharge" })).toBeInTheDocument();
  });

  it("asks the server to search and to filter by ward, never the browser", async () => {
    const user = userEvent.setup();
    mockWard();
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    await screen.findByText("Okoro, Ada");

    await user.selectOptions(screen.getByLabelText("Ward"), "2");
    await vi.waitFor(() => {
      const call = api.get.mock.calls
        .filter(([url]) => url === "/admin-discharges/dischargeable/").at(-1);
      expect(call[1].params).toMatchObject({ ward: "2" });
    });
  });

  it("says so plainly when the wards are empty", async () => {
    mockWard([]);
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    expect(await screen.findByText("Nobody is currently admitted")).toBeInTheDocument();
  });
});

describe("the page's own shape", () => {
  it("presents the two counts as figures, with what each one counts", async () => {
    // Not "0 on the wards" as a run of prose under the title: the number
    // leads, the label sits under it, on the card chrome the finance summary
    // already uses.
    // Two patients, both in Maternity: 2 on the wards, 1 ward occupied.
    mockWard([admission(), admission({ id: 5, patient_name: "Eze, Bo" })]);
    const { container } = renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    await screen.findByText("Eze, Bo");

    const summary = container.querySelector('[aria-label="Ward summary"]');
    expect(within(summary).getByText("2")).toBeInTheDocument();
    expect(within(summary).getByText("Patients on wards")).toBeInTheDocument();
    expect(within(summary).getByText("1")).toBeInTheDocument();
    expect(within(summary).getByText("Ward occupied")).toBeInTheDocument();
  });

  it("counts the wards the list actually spans", async () => {
    mockWard([
      admission(),
      admission({ id: 5, patient_name: "Eze, Bo", ward_name: "Male Medical" }),
    ]);
    const { container } = renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    await screen.findByText("Eze, Bo");
    const summary = container.querySelector('[aria-label="Ward summary"]');
    // Two patients across two wards — both figures read 2.
    expect(within(summary).getAllByText("2")).toHaveLength(2);
    expect(within(summary).getByText("Wards occupied")).toBeInTheDocument();
  });

  it("gives the search box a visible label so it lines up with the ward select", async () => {
    // The alignment fix: `SearchInput`'s label is an aria-label by default,
    // so in a filter row it floated above every `Field` beside it.
    mockWard();
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    await screen.findByText("Okoro, Ada");
    const search = screen.getByLabelText("Search");
    expect(search.id).toBeTruthy();
    expect(document.querySelector(`label[for="${search.id}"]`)).toHaveTextContent("Search");
  });

  it("offers a way out of an empty list that filters caused", async () => {
    const user = userEvent.setup();
    mockWard([]);
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });

    expect(await screen.findByText("Nobody is currently admitted")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open the bed board/ })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Search"), "nobody");
    expect(await screen.findByText("No admitted patient matches")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByLabelText("Search")).toHaveValue("");
  });
});

describe("reading the admission before discharging it", () => {
  it("opens the admission on View, with the stay and the identity", async () => {
    const user = userEvent.setup();
    mockWard();
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    const row = (await screen.findByText("Okoro, Ada")).closest("tr");
    await user.click(within(row).getByRole("button", { name: "View" }));

    expect(await screen.findByText("Bed history")).toBeInTheDocument();
    expect(screen.getByText("31 years")).toBeInTheDocument();
    expect(screen.getByText("Nurse Bisi")).toBeInTheDocument();
    expect(screen.getByText("Fever for investigation")).toBeInTheDocument();
    // No form until Discharge is pressed — View is reading.
    expect(screen.queryByLabelText(/Discharge diagnosis/)).not.toBeInTheDocument();
  });

  it("shows the bed moves the ward already recorded", async () => {
    const user = userEvent.setup();
    mockWard([admission()], {
      transfers: [{
        id: 2, from_bed_number: "M1", to_bed_number: "M2",
        from_ward_name: "Maternity", to_ward_name: "Maternity",
        transferred_by_name: "Nurse Bisi", reason: "Closer to the station",
        created_at: "2026-09-15T10:00:00Z",
      }],
    });
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    const row = (await screen.findByText("Okoro, Ada")).closest("tr");
    await user.click(within(row).getByRole("button", { name: "View" }));

    expect(await screen.findByText(/Maternity · Bed M2/)).toBeInTheDocument();
    expect(screen.getByText(/Closer to the station/)).toBeInTheDocument();
  });
});

describe("discharging", () => {
  it("will not submit without a diagnosis and a summary", async () => {
    const user = userEvent.setup();
    mockWard();
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    const row = (await screen.findByText("Okoro, Ada")).closest("tr");
    await user.click(within(row).getByRole("button", { name: "Discharge" }));

    expect(await screen.findByRole("button", { name: /Review and discharge/ })).toBeDisabled();
    expect(screen.getByText(/A discharge diagnosis and a summary are required/))
      .toBeInTheDocument();
  });

  it("confirms with the patient, admission, ward and bed before writing anything", async () => {
    const user = userEvent.setup();
    mockWard();
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    const row = (await screen.findByText("Okoro, Ada")).closest("tr");
    await user.click(within(row).getByRole("button", { name: "Discharge" }));
    await fillDischarge(user);
    await user.click(screen.getByRole("button", { name: /Review and discharge/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Okoro, Ada")).toBeInTheDocument();
    expect(within(dialog).getByText("ADM-000004")).toBeInTheDocument();
    expect(within(dialog).getByText("Maternity")).toBeInTheDocument();
    expect(within(dialog).getByText("Bed M1")).toBeInTheDocument();
    expect(within(dialog).getByText(/releases/)).toBeInTheDocument();
    // Nothing is written until it is confirmed.
    expect(post).not.toHaveBeenCalled();
  });

  it("posts to the one discharge endpoint with the admission and the form", async () => {
    const user = userEvent.setup();
    mockWard();
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: { patient_name: "Okoro, Ada", reference: "DCH-000001" },
    });
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    const row = (await screen.findByText("Okoro, Ada")).closest("tr");
    await user.click(within(row).getByRole("button", { name: "Discharge" }));
    await fillDischarge(user);
    await user.click(screen.getByRole("button", { name: /Review and discharge/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Confirm discharge" }));

    await vi.waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/admin-discharges/discharge/");
    expect(body).toMatchObject({
      admission: 4,
      diagnosis: "Pneumonia, resolved",
      summary: "Four days of IV antibiotics.",
    });
  });

  it("shows the existing discharge rather than writing a second one", async () => {
    // The server answers 409 `already_discharged` carrying the record
    // (`inpatient/services.AlreadyDischarged`); the screen shows it and offers
    // its letter instead of repeating the discharge.
    const user = userEvent.setup();
    mockWard();
    vi.spyOn(api, "post").mockRejectedValue({
      response: {
        status: 409,
        data: {
          code: "already_discharged",
          detail: "This admission was already discharged (DCH-000001).",
          discharge: {
            id: 1, reference: "DCH-000001", patient_name: "Okoro, Ada",
            discharged_at: "2026-09-16T11:00:00Z", completed_by_name: "Ngozi Admin",
          },
        },
      },
    });
    renderWithApp(<AdminDischarge />, { user: SUPER_ADMIN });
    const row = (await screen.findByText("Okoro, Ada")).closest("tr");
    await user.click(within(row).getByRole("button", { name: "Discharge" }));
    await fillDischarge(user);
    await user.click(screen.getByRole("button", { name: /Review and discharge/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Confirm discharge" }));

    expect(await screen.findByText("This admission has already been discharged"))
      .toBeInTheDocument();
    expect(screen.getByText("DCH-000001")).toBeInTheDocument();
    expect(screen.getByText(/It has not been discharged again/)).toBeInTheDocument();
  });
});
