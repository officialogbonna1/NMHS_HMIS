import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import VitalsStation from "./VitalsStation.jsx";

// A vitals hand-off is claimed by accepting it, and a claim is made once. The
// row says so afterwards rather than keeping a button that would be refused —
// which is also what stops two nurses walking to the same patient.

const NURSE = { id: 7, role: "nurse" };

const route = (overrides) => ({
  id: 1, purpose: "vitals", status: "queued", priority: "routine", patient_id: 3,
  patient_uuid: "8b0e5f1e-1111-4000-8000-000000000000", patient_name: "Obi, Ada",
  patient_number: "NMHS-P000012", department_name: "Clinicals (Nursing)",
  created_at: "2026-09-15T08:00:00Z", notes: "", assigned_to: null,
  assigned_to_name: null, can_accept: true, can_work: true, claimed_by_other: false,
  ...overrides,
});

const ACCEPTED = (overrides) => route({
  status: "in_progress", assigned_to: 7, assigned_to_name: "Ada Bello",
  can_accept: false, ...overrides,
});

function mockQueue(rows) {
  const state = { rows };
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/patient-routes/") return Promise.resolve({ data: state.rows });
    if (url === "/vitals/recorded-today/") return Promise.resolve({ data: { rows: [] } });
    return Promise.resolve({ data: [] });
  });
  return state;
}

/** The queue list — the toast says "Patient accepted" when one is taken. */
const queueList = () => within(screen.getByRole("heading", { name: "My queue" }).closest("section"));

afterEach(() => vi.restoreAllMocks());

describe("the Accept control on a vitals hand-off", () => {
  it("offers Accept while the hand-off is unclaimed", async () => {
    mockQueue([route({})]);
    renderWithApp(<VitalsStation />, { user: NURSE });

    expect(await screen.findByRole("button", { name: "Accept" })).toBeInTheDocument();
    expect(screen.queryByText("Accepted")).not.toBeInTheDocument();
  });

  it("reads Accepted once this nurse has accepted it", async () => {
    mockQueue([ACCEPTED({})]);
    renderWithApp(<VitalsStation />, { user: NURSE });

    expect(await screen.findByText("Accepted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("reads Accepted where another nurse took it first", async () => {
    // This is the double-claim: the row can still be on screen from before
    // the refetch, and the server would answer 409.
    mockQueue([ACCEPTED({ assigned_to: 12, assigned_to_name: "Tunde Okafor",
                          can_work: false, claimed_by_other: true })]);
    renderWithApp(<VitalsStation />, { user: NURSE });

    expect(await screen.findByText("Accepted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("still says Accepted when the page is loaded again from scratch", async () => {
    mockQueue([ACCEPTED({})]);
    const first = renderWithApp(<VitalsStation />, { user: NURSE });
    expect(await screen.findByText("Accepted")).toBeInTheDocument();
    first.unmount();

    renderWithApp(<VitalsStation />, { user: NURSE });
    expect(await screen.findByText("Accepted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("accepts the patient exactly as it always did", async () => {
    const user = userEvent.setup();
    const state = mockQueue([route({})]);
    const post = vi.spyOn(api, "post").mockImplementation(() => {
      state.rows = [ACCEPTED({})];
      return Promise.resolve({ data: ACCEPTED({}) });
    });
    renderWithApp(<VitalsStation />, { user: NURSE });

    await user.click(await screen.findByRole("button", { name: "Accept" }));
    expect(post).toHaveBeenCalledWith("/patient-routes/1/accept/");

    await user.click(await screen.findByRole("button", { name: /Back to my queue/i }));
    await waitFor(() => expect(queueList().getByText("Accepted")).toBeInTheDocument());
    expect(queueList().queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });
});
