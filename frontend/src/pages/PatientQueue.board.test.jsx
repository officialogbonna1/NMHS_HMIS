/**
 * The shared queue page, when the row is somebody else's.
 *
 * A station's board carries work a colleague has already accepted
 * (`work_routes_for(..., include_unit=True)`), so the buttons on a row have to
 * follow the server's own answer — `can_work`, out of `workflow/access.py`,
 * which is the rule `_own_route` refuses with. Otherwise a sonographer is
 * shown Start on a scan another sonographer is doing, and finds out by being
 * refused.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import PatientQueue from "./PatientQueue.jsx";

const RADIOGRAPHER = { id: 9, role: "radiology", first_name: "Bisi" };

const row = (overrides) => ({
  id: 1, visit: 5, patient_name: "Obi, Ada", patient_number: "NMHS-P000012",
  patient_id: 3, purpose: "ultrasound", purpose_label: "Ultrasound / Imaging",
  status: "queued", priority: "routine", department_name: "Radiology / Ultrasound",
  created_at: "2026-09-20T08:00:00Z", notes: "RUQ pain", assigned_to: null,
  assigned_to_name: null, can_work: true, can_accept: true, claimed_by_other: false,
  ...overrides,
});

function mockRoutes(rows) {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/patient-routes/") return Promise.resolve({ data: { results: rows } });
    return Promise.resolve({ data: { results: [] } });
  });
}

beforeEach(() => vi.restoreAllMocks());

describe("a radiologist reading the shared queue", () => {
  it("can start work that is theirs to do", async () => {
    mockRoutes([row({})]);
    renderWithApp(<PatientQueue />, { user: RADIOGRAPHER });
    expect(await screen.findByRole("button", { name: "Start" })).toBeInTheDocument();
  });

  it("can mark their own in-progress work done", async () => {
    mockRoutes([row({ status: "in_progress", assigned_to: 9, assigned_to_name: "Bisi Ade" })]);
    renderWithApp(<PatientQueue />, { user: RADIOGRAPHER });
    expect(await screen.findByRole("button", { name: "Mark done" })).toBeInTheDocument();
  });

  it("is offered no Start on a colleague's accepted request", async () => {
    mockRoutes([row({ status: "queued", assigned_to: 12, assigned_to_name: "Tunde Okafor",
                      can_work: false, can_accept: false, claimed_by_other: true })]);
    renderWithApp(<PatientQueue />, { user: RADIOGRAPHER });
    expect(await screen.findByText(/Obi, Ada/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
    expect(screen.getByText(/Accepted · Tunde Okafor/)).toBeInTheDocument();
  });

  it("is offered no Mark done on a colleague's in-progress work", async () => {
    mockRoutes([row({ status: "in_progress", assigned_to: 12, assigned_to_name: "Tunde Okafor",
                      can_work: false, claimed_by_other: true })]);
    renderWithApp(<PatientQueue />, { user: RADIOGRAPHER });
    await screen.findByText(/Obi, Ada/);
    expect(screen.queryByRole("button", { name: "Mark done" })).not.toBeInTheDocument();
  });

  it("keeps working for a payload that carries no verdict at all", async () => {
    const { can_work: _w, can_accept: _a, claimed_by_other: _c, ...legacy } = row({});
    mockRoutes([legacy]);
    renderWithApp(<PatientQueue />, { user: RADIOGRAPHER });
    expect(await screen.findByRole("button", { name: "Start" })).toBeInTheDocument();
  });
});
