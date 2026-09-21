import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import DepartmentStation from "./DepartmentStation.jsx";

// The /eye station is the eye doctor's way in: a referral that is theirs opens
// the chart and a new consultation. An unclaimed one offers no chart — the
// server would refuse it — and the optometrist keeps the findings form only.

const UUID = "8b0e5f1e-1111-4000-8000-000000000000";
const route = (overrides) => ({
  id: 1, purpose: "eye", status: "in_progress", priority: "routine", patient_id: 3, patient_uuid: UUID,
  patient_name: "Obi, Ada", patient_number: "NMHS-P000012", routed_by_name: "Reception",
  created_at: "2026-09-15T08:00:00Z", notes: "", assigned_to: 9, assigned_to_name: "Dr Eye", result: "",
  status_label: "In progress", patient_age: "34 years", patient_sex: "Female",
  patient_phone: "0803 111 2222", visit_reason: "Blurred vision", visit_type: "Outpatient",
  department_name: "Eye Clinic", routed_by_name: "Reception",
  ...overrides,
});

const QUEUE = [
  route({}),
  route({ id: 2, patient_uuid: "9c1f6a2f-2222-4000-8000-000000000000", patient_name: "Eze, Bo",
          status: "queued", status_label: "Queued", assigned_to: null, assigned_to_name: null }),
];
const FINISHED = [route({ id: 3, patient_name: "Nwosu, Chi", status: "completed",
                          status_label: "Completed" })];

beforeEach(() => {
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url === "/patient-routes/") {
      // Finished work is a request of its own — the live queue never carries it.
      return Promise.resolve({ data: config?.params?.status === "completed" ? FINISHED : QUEUE });
    }
    return Promise.resolve({ data: [] });
  });
});
afterEach(() => vi.restoreAllMocks());

describe("the eye doctor at the eye clinic station", () => {
  it("opens the chart only for the referral that is theirs", async () => {
    renderWithApp(<DepartmentStation station="eye" />, { user: { id: 9, role: "ophthalmologist" } });
    const links = await screen.findAllByRole("link", { name: "Open chart" });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", `/patients/${UUID}`);
    expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
  });

  it("starts an eye consultation on the existing notes page", async () => {
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="eye" />, { user: { id: 9, role: "ophthalmologist" } });
    await user.click(await screen.findByRole("button", { name: "Open →" }));
    expect(await screen.findByRole("link", { name: "Eye consultation" }))
      .toHaveAttribute("href", `/patients/${UUID}/notes?new=1`);
    expect(screen.getByRole("link", { name: "Open chart" })).toHaveAttribute("href", `/patients/${UUID}`);
  });
});

describe("the rest of the eye clinic", () => {
  it("gives the optometrist no chart actions", async () => {
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="eye" />, { user: { id: 9, role: "optometrist" } });
    await user.click(await screen.findByRole("button", { name: "Open →" }));
    expect(screen.queryByRole("link", { name: "Open chart" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Eye consultation" })).not.toBeInTheDocument();
  });
});

describe("the ophthalmology queue", () => {
  it("separates the whole clinic, my patients and what nobody has claimed", async () => {
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="eye" />, { user: { id: 9, role: "ophthalmologist" } });
    expect(await screen.findByRole("button", { name: "All (2)" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Mine (1)" }));
    expect(await screen.findByText("Obi, Ada")).toBeInTheDocument();
    expect(screen.queryByText("Eze, Bo")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Unassigned (1)" }));
    expect(await screen.findByText("Eze, Bo")).toBeInTheDocument();
    expect(screen.queryByText("Obi, Ada")).not.toBeInTheDocument();
  });

  it("reads the clinic that is already closed only when asked", async () => {
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="eye" />, { user: { id: 9, role: "ophthalmologist" } });
    await user.click(await screen.findByRole("button", { name: "In consultation (1)" }));
    expect(screen.queryByText("Nwosu, Chi")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Completed" }));
    expect(await screen.findByText("Nwosu, Chi")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith("/patient-routes/",
      expect.objectContaining({ params: expect.objectContaining({ status: "completed" }) }));
  });

  it("offers no way to finish work that is already finished", async () => {
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="eye" />, { user: { id: 9, role: "ophthalmologist" } });
    await user.click(await screen.findByRole("button", { name: "Completed" }));
    await user.click(await screen.findByRole("button", { name: "Open →" }));
    // The badge says so (the Completed tab button carries the word too).
    expect(await screen.findAllByText("Completed")).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Save & mark done" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
  });

  it("shows the eye doctor who is in front of them before they open the chart", async () => {
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="eye" />, { user: { id: 9, role: "ophthalmologist" } });
    await user.click(await screen.findByRole("button", { name: "Open →" }));
    // Who they are reads on one line under the name, not as a grid of rows.
    expect(await screen.findByText("NMHS-P000012 · 34 years · Female · 0803 111 2222"))
      .toBeInTheDocument();
    // Where they came from and what was asked for sit together below it.
    // `department_name` is *this* clinic, so it is never labelled "From".
    for (const [label, value] of [["Referred by", "Reception"], ["Clinic", "Eye Clinic"],
                                  ["Visit", "Outpatient"], ["Reason for visit", "Blurred vision"],
                                  ["Working on it", "Dr Eye"]]) {
      const row = screen.getByText(label).closest("div");
      expect(within(row).getByText(value)).toBeInTheDocument();
    }
    expect(screen.queryByText("From")).not.toBeInTheDocument();
  });

  it("sends the eye doctor onward through the hospital's existing pages", async () => {
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="eye" />, { user: { id: 9, role: "ophthalmologist" } });
    await user.click(await screen.findByRole("button", { name: "Open →" }));
    expect(await screen.findByRole("link", { name: "Prescribe" }))
      .toHaveAttribute("href", `/patients/${UUID}/prescribe`);
    expect(screen.getByRole("link", { name: "Refer / request" })).toHaveAttribute("href", "/refer");
  });
});

describe("the eye clinician's workspace", () => {
  const open = async (user, role = "ophthalmologist") => {
    renderWithApp(<DepartmentStation station="eye" />, { user: { id: 9, role } });
    await user.click(await screen.findByRole("button", { name: "Open →" }));
  };

  it("points the eye doctor at the consultation note for the examination itself", async () => {
    const user = userEvent.setup();
    await open(user);
    // The prose box here is the answer to the referrer; the structured
    // examination lives on the note, so the page says so rather than leaving
    // the eye doctor to type an examination into a summary field.
    expect(await screen.findByText("Findings to send back")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the eye consultation" }))
      .toHaveAttribute("href", `/patients/${UUID}/notes?new=1`);
    expect(screen.queryByText("Conduct the test")).not.toBeInTheDocument();
  });

  it("leaves the optometrist the working form they already had", async () => {
    const user = userEvent.setup();
    await open(user, "optometrist");
    expect(await screen.findByText("Conduct the test")).toBeInTheDocument();
    expect(screen.queryByText("Findings to send back")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open the eye consultation" })).not.toBeInTheDocument();
  });

  it("keeps the two ways of saving at the foot of the box they save", async () => {
    const user = userEvent.setup();
    await open(user);
    const findings = await screen.findByLabelText("Findings");
    const card = findings.closest("section");
    expect(within(card).getByRole("button", { name: "Save result" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Save & mark done" })).toBeInTheDocument();
  });

  it("will not save an empty finding, and says why", async () => {
    const user = userEvent.setup();
    await open(user);
    expect(await screen.findByRole("button", { name: "Save result" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save & mark done" })).toBeDisabled();
    expect(screen.getByText(/Type the findings, or attach the report/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Findings"), "VA 6/6 both eyes.");
    expect(screen.getByRole("button", { name: "Save result" })).toBeEnabled();
  });
});
