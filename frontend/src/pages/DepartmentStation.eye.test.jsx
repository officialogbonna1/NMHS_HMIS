import { screen } from "@testing-library/react";
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
  ...overrides,
});

beforeEach(() => {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/patient-routes/") {
      return Promise.resolve({ data: [
        route({}),
        route({ id: 2, patient_uuid: "9c1f6a2f-2222-4000-8000-000000000000", patient_name: "Eze, Bo",
                status: "queued", assigned_to: null, assigned_to_name: null }),
      ] });
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
