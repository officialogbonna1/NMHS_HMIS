import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import Maternity from "./Maternity.jsx";

// **The amendment controls, where the records are shown.**
//
// Everything here is read off the record as the API returned it: `can_amend`
// is the server's own `may_amend`, `amendable_fields` is what a correction
// may touch, `is_amended` is whether it has been corrected. So the screen can
// never offer a button the API refuses, and the identity facts are not
// rendered as editable boxes because they are not on that list.
//
// The read-only records — newborns, partogram checks, postpartum visits —
// carry no `can_amend` and get no action, which the last block holds.

const CREATOR = { id: 5, role: "maternity_nurse" };
const COLLEAGUE = { id: 6, role: "maternity_nurse" };
const ADMIN = { id: 1, role: "admin" };

const PATIENT = {
  id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000", name: "Williams, Dora",
  first_name: "Dora", last_name: "Williams", patient_number: "NMHS-P000015",
  phone_number: "0803 111 2222", pregnancy_number: 1, pregnancy_status: "active",
  gestation: "12w 0d", assigned_nurse: null, assigned_doctor: null,
};

const AMENDABLE_PREGNANCY = [
  { name: "edd", label: "EDD" }, { name: "gravida", label: "Gravida" },
  { name: "lmp", label: "LMP" }, { name: "notes", label: "Notes" },
  { name: "para", label: "Para" },
];

function pregnancy({ canAmend = true, amended = false } = {}) {
  return {
    id: 7, patient: 3, number: 1, reference: "PRG-000007", is_active: true,
    gestation: "12w 0d", lmp: "2026-07-09", edd: "2027-04-15",
    encounter_count: 1, last_encounter: null,
    can_amend: canAmend,
    amendable_fields: AMENDABLE_PREGNANCY,
    is_amended: amended,
    amendment_count: amended ? 1 : 0,
    last_amended_by_name: amended ? "Grace Nwosu" : null,
  };
}

const AMENDMENTS = [
  { id: 1, created_at: "2026-09-24T10:15:00Z", amended_by_name: "Grace Nwosu",
    reason: "typo", reason_label: "Typing or data-entry error",
    detail: "Card misread at booking.", previous: { lmp: "2026-07-02" },
    changes: [{ field: "lmp", label: "LMP", from: "2026-07-02", to: "2026-07-09" }] },
];

const ENCOUNTER = {
  id: 30, seen_on: "2026-09-01", visit_type_name: "ANC — follow-up",
  gestation: "10w 0d", provider_name: "Grace Nwosu", status: "completed",
  can_amend: true, is_amended: false, amendment_count: 0,
  amendable_fields: [{ name: "summary", label: "Summary" },
                     { name: "status", label: "Status" }],
};

function mockApi({ record = pregnancy(), encounters = [ENCOUNTER],
                   amendments = AMENDMENTS } = {}) {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/maternity/patients/") {
      return Promise.resolve({ data: { results: [PATIENT] } });
    }
    if (url === "/maternity/assignment/") {
      return Promise.resolve({ data: { in_maternity: true, department: "Maternity",
                                       assigned_nurse: null, assigned_doctor: null,
                                       admission: { admitted: false } } });
    }
    if (url === "/maternity/staff/") return Promise.resolve({ data: [] });
    if (url.endsWith("/amendments/")) return Promise.resolve({ data: amendments });
    if (url.endsWith("/amendment-reasons/")) {
      return Promise.resolve({ data: [
        { value: "typo", label: "Typing or data-entry error" }] });
    }
    if (url === "/maternity/lookup/") {
      return Promise.resolve({ data: {
        known: true,
        patient: { id: 3, name: "Williams, Dora", patient_number: "NMHS-P000015" },
        active_pregnancy: record, history: [record],
        can_continue: true, can_start: false, has_history: true, visit_types: [],
      } });
    }
    if (url.startsWith("/pregnancies/")) {
      return Promise.resolve({ data: { ...record, encounters } });
    }
    return Promise.resolve({ data: [] });
  });
}

async function openChart(user) {
  await user.click(await screen.findByRole("option", { name: /Williams, Dora/ }));
}

const openTab = (user, label) =>
  user.click(screen.getByRole("button", { name: new RegExp(`^${label}`) }));

afterEach(() => vi.restoreAllMocks());

describe("who is offered the amendment action", () => {
  it("the person who entered the record", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);

    expect(await screen.findByRole("button", { name: /Amend pregnancy/ }))
      .toBeInTheDocument();
  });

  it("an administrator", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: ADMIN });
    await openChart(user);

    expect(await screen.findByRole("button", { name: /Amend pregnancy/ }))
      .toBeInTheDocument();
  });

  it("not a colleague who did not enter it", async () => {
    const user = userEvent.setup();
    // The server says no — `can_amend: false` is `may_amend`'s own answer.
    mockApi({ record: pregnancy({ canAmend: false }) });
    renderWithApp(<Maternity />, { user: COLLEAGUE });
    await openChart(user);

    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /Amend pregnancy/ }))
      .not.toBeInTheDocument();
  });

  it("and the decision is the server's, not the role's", async () => {
    const user = userEvent.setup();
    // Same midwife role as the creator; the record still says no.
    mockApi({ record: pregnancy({ canAmend: false }) });
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);

    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /Amend/ })).not.toBeInTheDocument();
  });
});

describe("the amendment form", () => {
  it("offers only the fields the server says may be corrected", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);
    await user.click(await screen.findByRole("button", { name: /Amend pregnancy/ }));

    const form = await screen.findByRole("form", { name: "Amend record" });
    for (const label of ["LMP", "EDD", "Gravida", "Para"]) {
      expect(within(form).getByLabelText(label)).toBeInTheDocument();
    }
  });

  it("does not present the immutable facts as editable boxes", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);
    await user.click(await screen.findByRole("button", { name: /Amend pregnancy/ }));

    const form = await screen.findByRole("form", { name: "Amend record" });
    for (const immutable of [/^Patient$/i, /^Number$/i, /^Opened by$/i]) {
      expect(within(form).queryByLabelText(immutable)).not.toBeInTheDocument();
    }
  });

  it("says that they cannot be changed rather than leaving it unexplained", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);
    await user.click(await screen.findByRole("button", { name: /Amend pregnancy/ }));

    expect(await screen.findByText(/cannot be changed/i)).toBeInTheDocument();
  });

  it("requires a reason before it will save", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);
    await user.click(await screen.findByRole("button", { name: /Amend pregnancy/ }));

    expect(await screen.findByRole("button", { name: "Save correction" })).toBeDisabled();
  });

  it("submits through the record's own PATCH endpoint", async () => {
    const user = userEvent.setup();
    mockApi();
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ data: pregnancy() });
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);
    await user.click(await screen.findByRole("button", { name: /Amend pregnancy/ }));

    const chooser = await screen.findByLabelText(/Why is this being corrected/i);
    await waitFor(() => expect(chooser.options.length).toBeGreaterThan(1));
    await user.selectOptions(chooser, "typo");
    await user.click(screen.getByRole("button", { name: "Save correction" }));

    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls[0][0]).toBe("/pregnancies/7/");
    expect(patch.mock.calls[0][1].amendment_reason).toBe("typo");
  });

  it("surfaces the server's refusal instead of pretending it saved", async () => {
    const user = userEvent.setup();
    mockApi();
    vi.spyOn(api, "patch").mockRejectedValue({
      response: { status: 400, data: {
        detail: "Say why this record is being corrected.",
        code: "amendment_reason_required" } } });
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);
    await user.click(await screen.findByRole("button", { name: /Amend pregnancy/ }));

    const chooser = await screen.findByLabelText(/Why is this being corrected/i);
    await waitFor(() => expect(chooser.options.length).toBeGreaterThan(1));
    await user.selectOptions(chooser, "typo");
    await user.click(screen.getByRole("button", { name: "Save correction" }));

    expect(await screen.findByText(/Say why this record is being corrected/))
      .toBeInTheDocument();
  });

  it("reloads the record once the correction lands", async () => {
    const user = userEvent.setup();
    mockApi();
    vi.spyOn(api, "patch").mockResolvedValue({ data: pregnancy({ amended: true }) });
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);
    await user.click(await screen.findByRole("button", { name: /Amend pregnancy/ }));

    const chooser = await screen.findByLabelText(/Why is this being corrected/i);
    await waitFor(() => expect(chooser.options.length).toBeGreaterThan(1));
    await user.selectOptions(chooser, "typo");
    await user.click(screen.getByRole("button", { name: "Save correction" }));

    // The form closes and the history opens — the correction is discoverable
    // rather than a value that silently changed.
    expect(await screen.findByRole("region", { name: "Amendment history" }))
      .toBeInTheDocument();
  });
});

describe("a record that has been corrected", () => {
  it("says so on the record itself", async () => {
    const user = userEvent.setup();
    mockApi({ record: pregnancy({ amended: true }) });
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);

    expect(await screen.findByText("Amended")).toBeInTheDocument();
    expect(screen.getByText(/Corrected once · last by Grace Nwosu/))
      .toBeInTheDocument();
  });

  it("offers its history, showing what it used to say", async () => {
    const user = userEvent.setup();
    mockApi({ record: pregnancy({ amended: true }) });
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);
    await user.click(await screen.findByRole("button", { name: /View amendment history/ }));

    const history = await screen.findByRole("region", { name: "Amendment history" });
    expect(within(history).getByText("2026-07-02")).toBeInTheDocument();
    expect(within(history).getByText("Typing or data-entry error")).toBeInTheDocument();
  });

  it("offers no history link on a record nobody has corrected", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);

    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /View amendment history/ }))
      .not.toBeInTheDocument();
  });
});

describe("each attendance carries its own controls", () => {
  it("on the ANC tab, per visit", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);
    await openTab(user, "ANC");

    expect(await screen.findByRole("button", { name: /Amend visit/ }))
      .toBeInTheDocument();
  });

  it("and not where the server says the reader may not", async () => {
    const user = userEvent.setup();
    mockApi({ encounters: [{ ...ENCOUNTER, can_amend: false }] });
    renderWithApp(<Maternity />, { user: COLLEAGUE });
    await openChart(user);
    await openTab(user, "ANC");

    await screen.findByText("ANC — follow-up");
    expect(screen.queryByRole("button", { name: /Amend visit/ })).not.toBeInTheDocument();
  });
});

describe("the read-only records stay read-only", () => {
  it("a newborn, a partogram check and a postpartum visit get no action", async () => {
    const user = userEvent.setup();
    // None of them carries `can_amend`, because none of them has an update
    // path on the server — `NewbornViewSet` is read-only and the other two
    // are written through their parent's action.
    mockApi();
    renderWithApp(<Maternity />, { user: ADMIN });
    await openChart(user);

    for (const tab of ["Newborns", "Postpartum"]) {
      await openTab(user, tab);
      expect(screen.queryByRole("button", { name: /Amend newborn/ }))
        .not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Amend baby/ }))
        .not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Amend postpartum/ }))
        .not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Amend observation/ }))
        .not.toBeInTheDocument();
    }
  });
});

describe("the workspace is otherwise unchanged", () => {
  it("keeps every tab it had", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);

    for (const tab of ["Overview", "Triage", "ANC", "Labour", "Deliveries",
                       "Postpartum", "Newborns"]) {
      expect(await screen.findByRole("button", { name: new RegExp(`^${tab}`) }))
        .toBeInTheDocument();
    }
  });

  it("still shows the mother and the pregnancy she is in", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Maternity />, { user: CREATOR });
    await openChart(user);

    const banner = await screen.findByRole("group", { name: "Maternity summary" });
    expect(within(banner).getByText("NMHS-P000015")).toBeInTheDocument();
  });
});
