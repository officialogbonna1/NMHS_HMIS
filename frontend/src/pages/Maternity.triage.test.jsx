import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import Maternity from "./Maternity.jsx";

// **Maternity triage is the hospital's triage**, reached from the ward.
//
// The Triage tab renders `VitalsTab` — the very component the chart's Vitals
// tab renders — so a reading taken here is an ordinary `clinical.Vitals` row
// posted to `/vitals/`. These tests hold that it is the same component and the
// same endpoint, and that the ward's context (who is responsible, where she is
// lying) is read rather than stored.

const MIDWIFE = { id: 5, role: "maternity_nurse" };
const RECEPTION = { id: 2, role: "reception" };

const PATIENT = {
  id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000", name: "Ward, Dora",
  first_name: "Dora", last_name: "Ward", patient_number: "NMHS-P000015",
  phone_number: "0803 111 2222", pregnancy_number: 1, pregnancy_status: "active",
  gestation: "12w 0d", assigned_nurse: "Grace Nwosu", assigned_nurse_id: 5,
  assigned_doctor: "Dara Ibe", assigned_doctor_id: 8,
};

const PREGNANCY = {
  id: 7, patient: 3, number: 1, reference: "PRG-000007", is_active: true,
  gestation: "12w 0d", edd: "2027-03-12", encounter_count: 1, last_encounter: null,
};

const VITALS = [
  { id: 91, created_at: "2026-09-24T09:10:00Z", visit_time: "2026-09-24T09:10:00Z",
    bp_systolic: 120, bp_diastolic: 80, heart_rate: 82, temperature_c: "36.7",
    respiratory_rate: 18, sao2: 98, recorded_by_name: "Grace Nwosu" },
  { id: 74, created_at: "2026-09-10T08:00:00Z", visit_time: "2026-09-10T08:00:00Z",
    bp_systolic: 118, bp_diastolic: 76, heart_rate: 78, recorded_by_name: "Ada Okafor" },
];

const ADMITTED = {
  in_maternity: true, department: "Maternity", department_code: "maternity",
  assigned_nurse: { id: 5, name: "Grace Nwosu" },
  assigned_doctor: { id: 8, name: "Dara Ibe" },
  admission: { admitted: true, ward: "Maternity Ward", bed: "M-07",
               admitted_at: "2026-09-20T06:00:00Z", reference: "ADM-000012" },
};

function mockApi({ assignment = ADMITTED, vitals = VITALS } = {}) {
  const calls = [];
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    calls.push([url, config?.params]);
    if (url === "/maternity/patients/") {
      return Promise.resolve({ data: { results: [PATIENT] } });
    }
    if (url === "/maternity/assignment/") return Promise.resolve({ data: assignment });
    if (url === "/maternity/staff/") return Promise.resolve({ data: [] });
    if (url === "/maternity/lookup/") {
      return Promise.resolve({ data: {
        known: true,
        patient: { id: 3, name: "Ward, Dora", patient_number: "NMHS-P000015" },
        active_pregnancy: PREGNANCY, history: [PREGNANCY],
        can_continue: true, can_start: false, has_history: true, visit_types: [],
        admission: assignment.admission,
      } });
    }
    if (url === "/vitals/") return Promise.resolve({ data: vitals });
    if (url === "/nursing-notes/") return Promise.resolve({ data: [] });
    if (url.startsWith("/pregnancies/")) {
      return Promise.resolve({ data: { ...PREGNANCY, encounters: [] } });
    }
    return Promise.resolve({ data: [] });
  });
  return calls;
}

async function openMother(user) {
  await user.click(await screen.findByRole("option", { name: /Ward, Dora/ }));
}

const openTab = (user, label) =>
  user.click(screen.getByRole("button", { name: new RegExp(`^${label}`) }));

afterEach(() => vi.restoreAllMocks());

describe("the Triage tab", () => {
  it("is offered in the maternity workspace", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);

    expect(await screen.findByRole("button", { name: /^Triage/ })).toBeInTheDocument();
  });

  it("reads the hospital's own vitals endpoint, not a maternity one", async () => {
    const user = userEvent.setup();
    const calls = mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);
    await openTab(user, "Triage");

    await waitFor(() => expect(
      calls.some(([url, params]) => url === "/vitals/" && params?.patient === 3)).toBe(true));
    expect(calls.some(([url]) => url.includes("maternity-vitals"))).toBe(false);
    expect(calls.some(([url]) => url.includes("maternity/vitals"))).toBe(false);
  });

  it("shows today's reading and the earlier ones together", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);
    await openTab(user, "Triage");

    // Both readings, newest first — history is not replaced by today.
    expect(await screen.findByText("120/80")).toBeInTheDocument();
    expect(screen.getByText("118/76")).toBeInTheDocument();
  });

  it("names who recorded each one", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);
    await openTab(user, "Triage");

    expect(await screen.findByText(/Grace Nwosu/)).toBeInTheDocument();
    expect(screen.getByText(/Ada Okafor/)).toBeInTheDocument();
  });

  it("lets the midwife record a new reading", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);
    await openTab(user, "Triage");

    expect(await screen.findByRole("button", { name: /Record vitals|Add.*[Vv]itals/ }))
      .toBeInTheDocument();
  });

  it("says nothing recorded yet rather than showing an empty table", async () => {
    const user = userEvent.setup();
    mockApi({ vitals: [] });
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);
    await openTab(user, "Triage");

    expect(await screen.findByText(/No vitals/i)).toBeInTheDocument();
  });
});

describe("the overview brings the ward's context together", () => {
  it("names who is responsible, both of them", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Assigned nurse")).toBeInTheDocument();
    expect(within(table).getByText("Grace Nwosu")).toBeInTheDocument();
    expect(within(table).getByText("Assigned doctor")).toBeInTheDocument();
    expect(within(table).getByText("Dara Ibe")).toBeInTheDocument();
  });

  it("shows the ward and the bed she is actually in", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);

    const table = await screen.findByRole("table");
    expect(within(table).getByText(/Admitted · Maternity Ward · Bed M-07/))
      .toBeInTheDocument();
  });

  it("says 'Not admitted' when she is not, rather than leaving it blank", async () => {
    const user = userEvent.setup();
    mockApi({ assignment: {
      ...ADMITTED,
      admission: { admitted: false, ward: null, bed: null, admitted_at: null },
    } });
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Not admitted")).toBeInTheDocument();
  });

  it("says 'Not assigned' for a responsibility nobody holds", async () => {
    const user = userEvent.setup();
    mockApi({ assignment: { ...ADMITTED, assigned_doctor: null } });
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openMother(user);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Not assigned")).toBeInTheDocument();
  });

  it("reads the bed from the existing admission record, never a maternity copy",
     async () => {
       const user = userEvent.setup();
       const calls = mockApi();
       renderWithApp(<Maternity />, { user: MIDWIFE });
       await openMother(user);
       await screen.findByRole("table");

       expect(calls.some(([url]) => url === "/maternity/assignment/")).toBe(true);
       expect(calls.some(([url]) => url.includes("maternity-admission"))).toBe(false);
       expect(calls.some(([url]) => url.includes("maternity-beds"))).toBe(false);
     });
});

describe("the front desk", () => {
  it("gets no Triage tab, because it gets no workspace", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await openMother(user);

    expect(await screen.findByText("The clinical record is the maternity team's"))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Triage/ })).not.toBeInTheDocument();
  });
});
