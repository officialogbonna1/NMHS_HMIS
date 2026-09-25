import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import Maternity from "./Maternity.jsx";

// The maternity desk: find her, see where she stands, continue what she is
// already in. The screen never decides whether she is pregnant — the server
// does, and the buttons follow.

const MIDWIFE = { id: 5, role: "nurse" };
const RECEPTION = { id: 2, role: "reception" };

const PATIENT = {
  id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000", first_name: "Mary",
  last_name: "Jane", patient_number: "NMHS-P000123", phone_number: "0803 111 2222",
};

const CURRENT = {
  id: 7, number: 2, reference: "PRG-000007", is_active: true, gestation: "32w 4d",
  edd: "2026-03-12", gravida: 2, para: 1, encounter_count: 2,
  last_encounter: { id: 30, type: "ANC — follow-up", seen_on: "2026-09-01",
                    status: "completed", gestation: "29w 0d" },
};
const PREVIOUS = {
  id: 3, number: 1, reference: "PRG-000003", is_active: false, gestation: "",
  edd: "2024-02-02", outcome: "delivered", ended_on: "2024-02-05", encounter_count: 5,
  last_encounter: null,
};

const VISIT_TYPES = [
  { id: 1, code: "anc-booking", name: "ANC — first visit (booking)" },
  { id: 2, code: "anc-followup", name: "ANC — follow-up" },
  { id: 4, code: "labour-assessment", name: "Labour assessment" },
];

const ENCOUNTERS = [
  { id: 30, seen_on: "2026-09-01", visit_type_name: "ANC — follow-up",
    gestation: "29w 0d", provider_name: "Ada Bello", status: "completed" },
  { id: 12, seen_on: "2026-06-04", visit_type_name: "ANC — first visit (booking)",
    gestation: "16w 2d", provider_name: "Ada Bello", status: "completed" },
];

const lookup = (overrides) => ({
  known: true,
  patient: { id: 3, uuid: PATIENT.uuid, name: "Jane, Mary",
             patient_number: "NMHS-P000123", phone_number: "0803 111 2222", sex: "F" },
  active_pregnancy: CURRENT, history: [CURRENT, PREVIOUS],
  can_continue: true, can_start: false, has_history: true, visit_types: VISIT_TYPES,
  ...overrides,
});

function mockApi({ desk = lookup({}), encounters = ENCOUNTERS } = {}) {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/maternity/lookup/") return Promise.resolve({ data: desk });
    if (url.startsWith("/pregnancies/")) {
      return Promise.resolve({ data: { ...CURRENT, encounters } });
    }
    // The maternity picker reads the ward's own list, which the server
    // scopes — never the generic patient endpoint.
    if (url === "/maternity/patients/") {
      return Promise.resolve({ data: { results: [{ ...PATIENT, name: "Jane, Mary" }] } });
    }
    return Promise.resolve({ data: [] });
  });
}

async function findMother(user) {
  await user.click(await screen.findByLabelText(/Search maternity patients/i));
  await user.click(await screen.findByRole("option", { name: /Jane, Mary/ }));
}

afterEach(() => vi.restoreAllMocks());

describe("finding a returning mother", () => {
  it("asks nothing until a patient is chosen", async () => {
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });

    await screen.findByText("Find the mother");
    expect(api.get).not.toHaveBeenCalledWith("/maternity/lookup/", expect.anything());
  });

  it("shows who she is and the pregnancy she is already in", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    // Scoped to the banner: the timeline below says "Pregnancy #2" too.
    const banner = await screen.findByRole("group", { name: "Maternity summary" });
    expect(within(banner).getByText("Existing maternity patient")).toBeInTheDocument();
    expect(within(banner).getByText("Jane, Mary")).toBeInTheDocument();
    expect(within(banner).getByText("NMHS-P000123")).toBeInTheDocument();
    expect(within(banner).getByText(/Pregnancy #2/)).toBeInTheDocument();
    expect(within(banner).getByText(/32w 4d/)).toBeInTheDocument();
    expect(within(banner).getByText("G2 P1")).toBeInTheDocument();
  });

  it("offers Continue and never Start while she is pregnant", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    expect(await screen.findByText("Continue current pregnancy")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start new pregnancy" }))
      .not.toBeInTheDocument();
  });

  it("offers Start and never Continue when she has no active pregnancy", async () => {
    const user = userEvent.setup();
    mockApi({ desk: lookup({ active_pregnancy: null, history: [PREVIOUS],
                             can_continue: false, can_start: true }) });
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    expect(await screen.findByText("Existing patient — no active pregnancy"))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start new pregnancy" })).toBeInTheDocument();
    expect(screen.queryByText("Continue current pregnancy")).not.toBeInTheDocument();
  });

  it("says her previous pregnancies are kept as they are", async () => {
    const user = userEvent.setup();
    mockApi({ desk: lookup({ active_pregnancy: null, history: [PREVIOUS],
                             can_continue: false, can_start: true }) });
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    expect(await screen.findByText(/stay exactly as they are/)).toBeInTheDocument();
  });
});

describe("recording today's visit", () => {
  it("submits the pregnancy she is in and the clinic — never a patient", async () => {
    const user = userEvent.setup();
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: { id: 41, visit_type_name: "ANC — follow-up" } });
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    await user.selectOptions(await screen.findByLabelText("Visit type"), "2");
    await user.click(screen.getByRole("button", { name: "Record visit" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/maternity-encounters/", {
      pregnancy: 7, visit_type: 2,
    }));
    // Nothing on this screen can create a patient or a pregnancy.
    expect(post.mock.calls.some(([url]) => url === "/patients/")).toBe(false);
    expect(post.mock.calls.some(([url]) => url === "/pregnancies/")).toBe(false);
  });

  it("offers labour as its own visit type, not an ANC follow-up", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    const types = await screen.findByLabelText("Visit type");
    expect(within(types).getByRole("option", { name: "Labour assessment" }))
      .toBeInTheDocument();
  });

  it("will not record until a visit type is chosen", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    expect(await screen.findByRole("button", { name: "Record visit" })).toBeDisabled();
  });

  it("surfaces the server's refusal rather than its own guess", async () => {
    const user = userEvent.setup();
    mockApi();
    vi.spyOn(api, "post").mockRejectedValue({
      response: { status: 400, data: { code: "pregnancy_already_active",
                                       detail: "She already has an active pregnancy." } },
    });
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);
    await user.selectOptions(await screen.findByLabelText("Visit type"), "2");
    await user.click(screen.getByRole("button", { name: "Record visit" }));

    expect(await screen.findByText("She is already in a pregnancy")).toBeInTheDocument();
  });

  it("tells reception who records the visit, and offers it no control", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await findMother(user);

    // It still sees *which* action applies — that is the point of the desk —
    // and is told who performs it.
    expect(await screen.findByText("Continue current pregnancy")).toBeInTheDocument();
    expect(screen.getByText(/A midwife or doctor records today's visit/))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record visit" })).not.toBeInTheDocument();
  });
});

describe("the maternity workspace", () => {
  // Six views of one pregnancy — Overview opens, and each tab reads the Phase
  // 2 records rather than a second store.

  it("opens on Overview and says where the pregnancy stands", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    const workspace = (await screen.findByText("Pregnancy #2")).closest("section");
    expect(within(workspace).getByRole("cell", { name: "Pregnancy" })).toBeInTheDocument();
    expect(within(workspace).getByRole("cell", { name: /Not in labour|In progress|Delivered/ }))
      .toBeInTheDocument();
  });

  it("offers every tab of the record", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    const tabs = await screen.findByRole("navigation", { name: "Maternity record" });
    for (const label of ["Overview", "ANC", "Labour", "Deliveries", "Postpartum", "Newborns"]) {
      expect(within(tabs).getByRole("button", { name: new RegExp(`^${label}`) }))
        .toBeInTheDocument();
    }
  });

  it("lists every ANC visit of this pregnancy, oldest history intact", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    await user.click(await screen.findByRole("button", { name: /^ANC/ }));
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(screen.getByRole("cell", { name: "ANC — first visit (booking)" }))
      .toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "16w 2d" })).toBeInTheDocument();
  });

  it("counts the visits on the tab itself", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    expect(await screen.findByRole("button", { name: /^ANC\s*\(2\)/ })).toBeInTheDocument();
  });

  it("says so plainly when no ANC visit has been recorded yet", async () => {
    const user = userEvent.setup();
    mockApi({ encounters: [] });
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    await user.click(await screen.findByRole("button", { name: /^ANC/ }));
    expect(await screen.findByText("No ANC visit recorded yet")).toBeInTheDocument();
  });

  it("shows her earlier pregnancies on the overview, read-only", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await findMother(user);

    const section = (await screen.findByText("Previous pregnancies")).closest("section");
    expect(within(section).getByText("PRG-000003")).toBeInTheDocument();
    expect(within(section).getByText("delivered")).toBeInTheDocument();
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("the Reception desk", () => {
  // Checks 1-4 from the screen reception actually works at, not only the API.
  // Reception is the desk that meets her at the door: it must be able to see
  // which of the three situations this is without being able to write.

  it("finds a returning mother and says she is an existing maternity patient", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await findMother(user);

    const banner = await screen.findByRole("group", { name: "Maternity summary" });
    expect(within(banner).getByText("Existing maternity patient")).toBeInTheDocument();
    expect(within(banner).getByText("Jane, Mary")).toBeInTheDocument();
    expect(within(banner).getByText("NMHS-P000123")).toBeInTheDocument();
    // No second registration is offered to somebody the hospital already knows.
    expect(screen.queryByRole("button", { name: /Register/i })).not.toBeInTheDocument();
  });

  it("reads the active pregnancy summary off the record", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await findMother(user);

    const banner = await screen.findByRole("group", { name: "Maternity summary" });
    expect(within(banner).getByText(/Pregnancy #2/)).toBeInTheDocument();
    expect(within(banner).getByText(/32w 4d/)).toBeInTheDocument();
    expect(within(banner).getByText(/EDD/)).toBeInTheDocument();
    expect(within(banner).getByText("G2 P1")).toBeInTheDocument();
    expect(within(banner).getByText(/ANC — follow-up/)).toBeInTheDocument();
  });

  it("shows Continue and never Start for an active pregnancy", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await findMother(user);

    expect(await screen.findByText("Continue current pregnancy")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start new pregnancy" }))
      .not.toBeInTheDocument();
  });

  it("shows Start and never Continue once the pregnancy is completed", async () => {
    const user = userEvent.setup();
    // Her only pregnancy has been delivered and closed.
    mockApi({ desk: lookup({ active_pregnancy: null, history: [PREVIOUS],
                             can_continue: false, can_start: true }) });
    renderWithApp(<Maternity />, { user: RECEPTION });
    await findMother(user);

    const banner = await screen.findByRole("group", { name: "Maternity summary" });
    expect(within(banner).getByText("Existing patient — no active pregnancy"))
      .toBeInTheDocument();
    expect(screen.queryByText("Continue current pregnancy")).not.toBeInTheDocument();
  });

  it("shows her previous pregnancy history either way", async () => {
    const user = userEvent.setup();
    mockApi({ desk: lookup({ active_pregnancy: null, history: [PREVIOUS],
                             can_continue: false, can_start: true }) });
    renderWithApp(<Maternity />, { user: RECEPTION });
    await findMother(user);

    const section = (await screen.findByText("Previous pregnancies")).closest("section");
    expect(within(section).getByText("#1")).toBeInTheDocument();
    expect(within(section).getByText("PRG-000003")).toBeInTheDocument();
    expect(within(section).getByText("delivered")).toBeInTheDocument();
    expect(within(section).getByText("5")).toBeInTheDocument();   // visits on it
  });

  it("never offers reception a way to write", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: RECEPTION });
    await findMother(user);

    await screen.findByRole("group", { name: "Maternity summary" });
    expect(screen.queryByRole("button", { name: "Record visit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start new pregnancy" }))
      .not.toBeInTheDocument();
    expect(screen.queryByLabelText("Visit type")).not.toBeInTheDocument();
  });
});
