import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import Maternity from "./Maternity.jsx";

// **Click, and the ward's mothers are there** — and the front desk stops at
// the door of the clinical record.
//
// The picker's old failure looked like a UI bug (a dropdown arrow that opened
// onto nothing) and was a scoping one: `/patients/` returned no rows for a
// midwife, so the only way to reach anybody was to type a hospital number in
// full. These tests hold the behaviour, and the endpoint it reads.

const MIDWIFE = { id: 5, role: "maternity_nurse" };
const RECEPTION = { id: 2, role: "reception" };

const WARD = [
  { id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000", name: "Eze, Ngozi",
    first_name: "Ngozi", last_name: "Eze", patient_number: "NMHS-P000015",
    phone_number: "0803 111 2222", pregnancy_number: 1, pregnancy_status: "active",
    gestation: "30w 4d", assigned_nurse: "Grace Nwosu", assigned_nurse_id: 5,
    assigned_doctor: "Dara Ibe", assigned_doctor_id: 8 },
  { id: 9, uuid: "8b0e5f1e-2222-4000-8000-000000000000", name: "Bala, Sade",
    first_name: "Sade", last_name: "Bala", patient_number: "NMHS-P000021",
    phone_number: "0803 333 4444", pregnancy_number: 2, pregnancy_status: "active",
    gestation: "12w 0d", assigned_nurse: null, assigned_nurse_id: null,
    assigned_doctor: null, assigned_doctor_id: null },
];

const NURSES = [
  { id: 5, name: "Grace Nwosu", role: "maternity_nurse", role_label: "Maternity" },
  { id: 6, name: "Ada Okafor", role: "maternity_nurse", role_label: "Maternity" },
];
const DOCTORS = [
  { id: 8, name: "Dara Ibe", role: "doctor", role_label: "Doctor" },
  { id: 9, name: "Paul Eze", role: "doctor", role_label: "Doctor" },
];

const PREGNANCY = {
  id: 7, number: 1, reference: "PRG-000007", is_active: true, gestation: "30w 4d",
  encounter_count: 1, last_encounter: null,
};

function mockApi({ assignment = { in_maternity: true, department: "Maternity",
                                  department_code: "maternity", route_status: "queued",
                                  assigned_nurse: { id: 5, name: "Grace Nwosu" },
                                  assigned_doctor: { id: 8, name: "Dara Ibe" } } } = {}) {
  const calls = [];
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    calls.push([url, config?.params]);
    if (url === "/maternity/patients/") {
      const term = config?.params?.search;
      const rows = term
        ? WARD.filter((r) => `${r.name} ${r.patient_number}`.toLowerCase()
          .includes(String(term).toLowerCase()))
        : WARD;
      return Promise.resolve({ data: { results: rows } });
    }
    if (url === "/maternity/staff/") {
      // Each column asks for its own roster — a mixed list filtered in the
      // browser would be exactly the frontend-as-security this avoids.
      return Promise.resolve({
        data: config?.params?.for === "doctor" ? DOCTORS : NURSES,
      });
    }
    if (url === "/maternity/assignment/") return Promise.resolve({ data: assignment });
    if (url === "/maternity/lookup/") {
      return Promise.resolve({ data: {
        known: true,
        patient: { id: 3, name: "Eze, Ngozi", patient_number: "NMHS-P000015" },
        active_pregnancy: PREGNANCY, history: [PREGNANCY],
        can_continue: true, can_start: false, has_history: true, visit_types: [],
      } });
    }
    if (url.startsWith("/pregnancies/")) {
      return Promise.resolve({ data: { ...PREGNANCY, encounters: [] } });
    }
    return Promise.resolve({ data: [] });
  });
  return calls;
}

const openPicker = (user) =>
  user.click(screen.getByLabelText(/Search maternity patients/i));

afterEach(() => vi.restoreAllMocks());

describe("clicking the picker", () => {
  it("shows the ward's mothers with nothing typed", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await openPicker(user);

    expect(await screen.findByRole("option", { name: /Eze, Ngozi/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Bala, Sade/ })).toBeInTheDocument();
  });

  it("does not make the desk press Enter to see anybody", async () => {
    const user = userEvent.setup();
    const calls = mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await openPicker(user);
    await screen.findByRole("option", { name: /Eze, Ngozi/ });

    const [, params] = calls.find(([url]) => url === "/maternity/patients/");
    expect(params).not.toHaveProperty("search");
  });

  it("reads the maternity endpoint, never the generic patient list", async () => {
    const user = userEvent.setup();
    const calls = mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await openPicker(user);
    await screen.findByRole("option", { name: /Eze, Ngozi/ });

    expect(calls.some(([url]) => url === "/patients/")).toBe(false);
  });

  it("shows enough of each row to tell two mothers apart", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await openPicker(user);
    const row = await screen.findByRole("option", { name: /Eze, Ngozi/ });
    expect(within(row).getByText(/NMHS-P000015/)).toBeInTheDocument();
    expect(within(row).getByText("0803 111 2222")).toBeInTheDocument();
    expect(within(row).getByText("Grace Nwosu")).toBeInTheDocument();
  });

  it("says plainly when nobody is responsible rather than leaving a blank", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await openPicker(user);
    const row = await screen.findByRole("option", { name: /Bala, Sade/ });
    expect(within(row).getByText("No midwife named")).toBeInTheDocument();
  });
});

describe("the list is the front desk's", () => {
  // Same rows, same A–Z sections, same letter rail — `patientListing.jsx`,
  // which reception's Patients page renders too.
  it("groups mothers under the initial of the surname", async () => {
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await screen.findByRole("option", { name: /Eze, Ngozi/ });
    expect(screen.getByRole("heading", { name: "B" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "E" })).toBeInTheDocument();
  });

  it("lists the A–Z rail down the side", async () => {
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await screen.findByRole("option", { name: /Eze, Ngozi/ });
    const rail = screen.getByRole("navigation", { name: "Jump to letter" });
    expect(within(rail).getAllByRole("button")).toHaveLength(26);
    // A letter somebody is filed under is reachable; one nobody is, is not.
    expect(within(rail).getByRole("button", { name: "Jump to E" })).toBeEnabled();
    expect(within(rail).getByRole("button", { name: "Jump to Q" })).toBeDisabled();
  });

  it("shows each mother the way the front desk shows a patient", async () => {
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    const row = await screen.findByRole("option", { name: /Eze, Ngozi/ });
    // Name as "Last, First", the hospital number as a chip, the phone beneath.
    expect(within(row).getByText("NMHS-P000015")).toBeInTheDocument();
    expect(within(row).getByText("0803 111 2222")).toBeInTheDocument();
  });

  it("keeps the pregnancy off the row — it is on the record below", async () => {
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    const row = await screen.findByRole("option", { name: /Eze, Ngozi/ });
    expect(within(row).queryByText(/Pregnancy #/)).not.toBeInTheDocument();
    expect(within(row).queryByText(/30w 4d/)).not.toBeInTheDocument();
  });

  it("is a list on the page, not a dropdown to be opened", async () => {
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    // Nothing clicked, nothing typed: the ward is simply there.
    expect(await screen.findByRole("option", { name: /Eze, Ngozi/ })).toBeInTheDocument();
    expect(screen.getByRole("listbox", { name: "Maternity patients" })).toBeInTheDocument();
  });
});

describe("the search box itself", () => {
  // It is the application's one patient search — same magnifier, same clear
  // button, same 44px target as the front desk's — rather than a maternity
  // spelling of the same idea.
  it("is the shared SearchInput, magnifier and all", async () => {
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    const box = await screen.findByLabelText(/Search maternity patients/i);
    expect(box).toHaveAttribute("type", "search");
    expect(box.className).toMatch(/pl-9/);
    expect(box.className).toMatch(/min-h-\[44px\]/);
    expect(box.parentElement.querySelector("svg circle")).not.toBeNull();
  });

  it("uses the same words the front desk searches by", async () => {
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    expect(await screen.findByPlaceholderText("Name or file number (e.g. NMHS-000001)"))
      .toBeInTheDocument();
  });

  it("clears itself back to the whole ward", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    const box = await screen.findByLabelText(/Search maternity patients/i);
    await user.type(box, "Bala");
    await user.click(await screen.findByRole("button", { name: /Clear search/i }));

    expect(box).toHaveValue("");
    expect(await screen.findByRole("option", { name: /Eze, Ngozi/ })).toBeInTheDocument();
  });

  it("still opens the ward's list on a click, with nothing typed", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await openPicker(user);
    expect(await screen.findByRole("option", { name: /Eze, Ngozi/ })).toBeInTheDocument();
  });
});

describe("typing in the picker", () => {
  it("filters through the same request", async () => {
    const user = userEvent.setup();
    const calls = mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await user.type(screen.getByLabelText(/Search maternity patients/i), "Dora");
    await waitFor(() => expect(
      calls.some(([url, params]) => url === "/maternity/patients/" && params?.search === "Dora"),
    ).toBe(true));
  });

  it("finds her by hospital number too", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await user.type(screen.getByLabelText(/Search maternity patients/i),
                    "NMHS-P000021");
    // The list keeps the previous rows while the narrowed request lands
    // (`keepPreviousData`, as reception's does), so the one that must *go* is
    // what says the search arrived.
    await waitFor(() => expect(
      screen.queryByRole("option", { name: /Eze, Ngozi/ })).not.toBeInTheDocument());
    expect(screen.getByRole("option", { name: /Bala, Sade/ })).toBeInTheDocument();
  });
});

describe("stepping outside Maternity", () => {
  it("is offered to the desk, which has to find a mother not on the ward yet", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });

    await openPicker(user);
    expect(await screen.findByRole("button", { name: /Search all patients you can see/ }))
      .toBeInTheDocument();
  });

  it("is not offered to a midwife, whose list is the ward's already", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await openPicker(user);
    await screen.findByRole("option", { name: /Eze, Ngozi/ });
    expect(screen.queryByRole("button", { name: /Search all patients you can see/ }))
      .not.toBeInTheDocument();
  });
});

describe("the assignment panel", () => {
  async function chooseMother(user) {
    await openPicker(user);
    await user.click(await screen.findByRole("option", { name: /Eze, Ngozi/ }));
  }

  it("is the desk's, not the midwife's", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await chooseMother(user);

    await waitFor(() => expect(
      screen.queryByRole("group", { name: "Maternity assignment" })).not.toBeInTheDocument());
  });

  it("shows the department and both responsibilities as three separate things", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await chooseMother(user);

    const panel = await screen.findByRole("group", { name: "Maternity assignment" });
    expect(within(panel).getByText("Department")).toBeInTheDocument();
    expect(within(panel).getByText("Maternity department")).toBeInTheDocument();
    expect(within(panel).getByText("Assigned Nurse")).toBeInTheDocument();
    expect(within(panel).getByText("Grace Nwosu")).toBeInTheDocument();
    expect(within(panel).getByText("Assigned Doctor")).toBeInTheDocument();
    expect(within(panel).getByText("Dara Ibe")).toBeInTheDocument();
  });

  it("says the names do not limit who can see her", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await chooseMother(user);

    const panel = await screen.findByRole("group", { name: "Maternity assignment" });
    expect(within(panel).getByText(/do not limit who has access/)).toBeInTheDocument();
  });

  it("says 'Not assigned' rather than leaving either blank", async () => {
    const user = userEvent.setup();
    mockApi({ assignment: { in_maternity: true, department: "Maternity",
                            department_code: "maternity", assigned_nurse: null,
                            assigned_doctor: null } });
    renderWithApp(<Maternity />, { user: RECEPTION });
    await chooseMother(user);

    const panel = await screen.findByRole("group", { name: "Maternity assignment" });
    expect(within(panel).getAllByText("Not assigned").length).toBeGreaterThanOrEqual(2);
  });

  it("fills each column from its own roster", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await chooseMother(user);

    const panel = await screen.findByRole("group", { name: "Maternity assignment" });
    const nurses = within(panel).getByRole("combobox", { name: "Assigned Nurse selector" });
    const doctors = within(panel).getByRole("combobox", { name: "Assigned Doctor selector" });
    expect([...nurses.options].map((o) => o.textContent))
      .toEqual(["Not assigned", "Grace Nwosu — Maternity", "Ada Okafor — Maternity"]);
    expect([...doctors.options].map((o) => o.textContent))
      .toEqual(["Not assigned", "Dara Ibe — Doctor", "Paul Eze — Doctor"]);
  });

  it("changes the midwife without sending a doctor or a department", async () => {
    const user = userEvent.setup();
    mockApi();
    const patch = vi.spyOn(api, "patch").mockResolvedValue({
      data: { in_maternity: true, department: "Maternity", department_code: "maternity",
              assigned_nurse: { id: 6, name: "Ada Okafor" },
              assigned_doctor: { id: 8, name: "Dara Ibe" } },
    });
    renderWithApp(<Maternity />, { user: RECEPTION });
    await chooseMother(user);

    const panel = await screen.findByRole("group", { name: "Maternity assignment" });
    await user.selectOptions(
      within(panel).getByRole("combobox", { name: "Assigned Nurse selector" }), "6");
    await user.click(within(panel).getByRole("button", { name: "Change midwife" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith("/maternity/assignment/",
                                                           { patient: 3, nurse: 6 }));
    expect(patch.mock.calls[0][1]).not.toHaveProperty("doctor");
    expect(patch.mock.calls[0][1]).not.toHaveProperty("department");
  });

  it("changes the doctor without sending a nurse or a department", async () => {
    const user = userEvent.setup();
    mockApi();
    const patch = vi.spyOn(api, "patch").mockResolvedValue({
      data: { in_maternity: true, department: "Maternity", department_code: "maternity",
              assigned_nurse: { id: 5, name: "Grace Nwosu" },
              assigned_doctor: { id: 9, name: "Paul Eze" } },
    });
    renderWithApp(<Maternity />, { user: RECEPTION });
    await chooseMother(user);

    const panel = await screen.findByRole("group", { name: "Maternity assignment" });
    await user.selectOptions(
      within(panel).getByRole("combobox", { name: "Assigned Doctor selector" }), "9");
    await user.click(within(panel).getByRole("button", { name: "Change doctor" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith("/maternity/assignment/",
                                                           { patient: 3, doctor: 9 }));
    expect(patch.mock.calls[0][1]).not.toHaveProperty("nurse");
    expect(patch.mock.calls[0][1]).not.toHaveProperty("department");
  });

  it("assigns her to the department when she is not in Maternity yet", async () => {
    const user = userEvent.setup();
    mockApi({ assignment: { in_maternity: false, department: null,
                            assigned_nurse: null } });
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    renderWithApp(<Maternity />, { user: RECEPTION });
    await chooseMother(user);

    const panel = await screen.findByRole("group", { name: "Maternity assignment" });
    expect(within(panel).getByText("Not in Maternity")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Assign to Maternity" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/maternity/assignment/",
                                                          { patient: 3 }));
  });
});

describe("the front desk and the clinical record", () => {
  it("reads who she is and whether she is pregnant", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await openPicker(user);
    await user.click(await screen.findByRole("option", { name: /Eze, Ngozi/ }));

    const banner = await screen.findByRole("group", { name: "Maternity summary" });
    expect(within(banner).getByText("NMHS-P000015")).toBeInTheDocument();
  });

  it("is shown no antenatal, labour, delivery or newborn record", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await openPicker(user);
    await user.click(await screen.findByRole("option", { name: /Eze, Ngozi/ }));

    expect(await screen.findByText("The clinical record is the maternity team's"))
      .toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: "Maternity record" }))
      .not.toBeInTheDocument();
  });

  it("does not even ask the server for it", async () => {
    const user = userEvent.setup();
    const calls = mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await openPicker(user);
    await user.click(await screen.findByRole("option", { name: /Eze, Ngozi/ }));
    await screen.findByText("The clinical record is the maternity team's");

    const asked = calls.map(([url]) => url);
    expect(asked).not.toContain("/labour-episodes/");
    expect(asked.some((url) => url.includes("/timeline/"))).toBe(false);
  });

  it("but a midwife gets the whole workspace", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openPicker(user);
    await user.click(await screen.findByRole("option", { name: /Eze, Ngozi/ }));

    expect(await screen.findByRole("button", { name: /^Labour/ })).toBeInTheDocument();
    expect(screen.queryByText("The clinical record is the maternity team's"))
      .not.toBeInTheDocument();
  });
});
