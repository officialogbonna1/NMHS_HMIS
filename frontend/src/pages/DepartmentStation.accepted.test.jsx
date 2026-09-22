import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import DepartmentStation from "./DepartmentStation.jsx";

// Accepting a referral is a one-way step, so the control it was made with
// must not survive it: the row reads "✓ Accepted" afterwards rather than
// keeping a button that would only come back 409. The state is the route's
// own status — nothing on the screen remembers the press.

const UUID = "8b0e5f1e-1111-4000-8000-000000000000";
const RADIOGRAPHER = { id: 9, role: "radiology" };

const route = (overrides) => ({
  id: 1, purpose: "ultrasound", status: "queued", status_label: "Queued",
  priority: "routine", patient_id: 3, patient_uuid: UUID, patient_name: "Obi, Ada",
  patient_number: "NMHS-P000012", routed_by_name: "Dr Chidi Nwosu",
  created_at: "2026-09-15T08:00:00Z", notes: "", assigned_to: null,
  assigned_to_name: null, result: "", department_name: "Radiology / Ultrasound",
  services: [], can_accept: true, can_work: true, claimed_by_other: false,
  ...overrides,
});

const ACCEPTED = (overrides) => route({
  status: "in_progress", status_label: "In progress", assigned_to: 9,
  assigned_to_name: "Bisi Ade", can_accept: false, ...overrides,
});

/** The queue list itself — the toast also says "Accepted" when one is taken. */
const queueList = () => within(screen.getByRole("heading", { name: "Requests" }).closest("section"));

/** The queue as the server would send it, and a handle to change it. */
function mockQueue(rows) {
  const state = { rows };
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url === "/patient-routes/") {
      return Promise.resolve({ data: config?.params?.status === "completed" ? [] : state.rows });
    }
    if (url === "/patient-routes/report-fields/") return Promise.resolve({ data: { schema: null } });
    return Promise.resolve({ data: [] });
  });
  return state;
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => vi.restoreAllMocks());

describe("the Accept control on a referral", () => {
  it("offers Accept while the work is unclaimed", async () => {
    mockQueue([route({})]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });

    expect(await screen.findByRole("button", { name: "Accept" })).toBeInTheDocument();
    expect(screen.queryByText("Accepted")).not.toBeInTheDocument();
  });

  it("reads Accepted instead, once the referral has been accepted", async () => {
    mockQueue([ACCEPTED({})]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });

    expect(await screen.findByText("Accepted")).toBeInTheDocument();
  });

  it("offers no Accept on a referral that has been accepted", async () => {
    mockQueue([ACCEPTED({})]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });

    await screen.findByText("Accepted");
    // Not a disabled button either: a spent control invites a second press.
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accepting…" })).not.toBeInTheDocument();
  });

  it("still says Accepted when the page is loaded again from scratch", async () => {
    // Nothing is remembered between renders: the second mount reads the same
    // route off the server and reaches the same answer.
    mockQueue([ACCEPTED({})]);
    const first = renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findByText("Accepted")).toBeInTheDocument();
    first.unmount();

    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findByText("Accepted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("says Accepted where a colleague took it, and never offers the button", async () => {
    mockQueue([ACCEPTED({ assigned_to: 12, assigned_to_name: "Tunde Okafor",
                          can_work: false, claimed_by_other: true })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });

    expect(await screen.findByText("Accepted")).toBeInTheDocument();
    expect(screen.getByText("With Tunde Okafor")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("accepts the referral exactly as it always did", async () => {
    const user = userEvent.setup();
    const state = mockQueue([route({})]);
    const post = vi.spyOn(api, "post").mockImplementation(() => {
      // The server's answer is the new state of the row, which is what the
      // refetched queue then carries.
      state.rows = [ACCEPTED({})];
      return Promise.resolve({ data: ACCEPTED({}) });
    });
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });

    await user.click(await screen.findByRole("button", { name: "Accept" }));
    expect(post).toHaveBeenCalledWith("/patient-routes/1/accept/");

    // And the control is spent: back on the queue, the row reads Accepted.
    await user.click(await screen.findByRole("button", { name: /Back to the queue/i }));
    await waitFor(() => expect(queueList().getByText("Accepted")).toBeInTheDocument());
    expect(queueList().queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });
});
