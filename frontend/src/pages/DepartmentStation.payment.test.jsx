import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import DepartmentStation from "./DepartmentStation.jsx";

// Where the money stands, on the unit's own worklist — and what happens when
// somebody is about to close a request the patient has not paid for.
//
// The rule underneath all of it: a department *reads* the billing status and
// is never gated by it (rules 24 and 51). A sample already drawn is run, a
// patient already on the couch is scanned, and the cash desk chases the
// balance. What these tests hold is that it cannot happen by *accident*.

const UUID = "8b0e5f1e-1111-4000-8000-000000000000";

const service = (over = {}) => ({
  id: 1, name: "Abdominal Ultrasound", unit_price: "8000.00",
  billing: {
    billed: true, status: "unpaid", label: "UNPAID", requires_payment: true,
    amount: "8000.00", paid: "0.00", outstanding: "8000.00", deferred: false,
  },
  ...over,
});

const unpaidRoute = (over = {}) => ({
  id: 1, purpose: "ultrasound", status: "in_progress", status_label: "In progress",
  priority: "routine", patient_id: 3, patient_uuid: UUID, patient_name: "Obi, Ada",
  patient_number: "NMHS-P000012", routed_by_name: "Dr Chidi Nwosu",
  created_at: "2026-09-15T08:00:00Z", notes: "RUQ pain",
  assigned_to: 9, assigned_to_name: "Bisi Ade", result: "",
  department_name: "Radiology / Ultrasound", visit_type: "Outpatient",
  services: [service()],
  billing: {
    billed: true, status: "unpaid", label: "UNPAID", requires_payment: true,
    total: "8000.00", paid: "0.00", outstanding: "8000.00", settled: false, deferred: false,
  },
  ...over,
});

const SCHEMA = { purpose: "ultrasound", sections: [
  { key: "findings", label: "Findings", kind: "text", max_length: 4000 }] };

function mockQueue(rows) {
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url === "/patient-routes/") {
      return Promise.resolve({ data: config?.params?.status === "completed" ? [] : rows });
    }
    if (url === "/patient-routes/report-fields/") return Promise.resolve({ data: { schema: SCHEMA } });
    return Promise.resolve({ data: [] });
  });
}

const RADIOGRAPHER = { id: 9, role: "radiology" };

/** Open the request and write a finding, which is what "done" needs. */
async function openAndWrite(user) {
  await user.click(await screen.findByRole("button", { name: "Open →" }));
  await user.type(await screen.findByLabelText(/Findings/), "Normal study.");
}

afterEach(() => vi.restoreAllMocks());

describe("the worklist says where the money stands", () => {
  it("carries the status on the queue row, without opening anything", async () => {
    mockQueue([unpaidRoute()]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    // The bench used to have to open the order to find this out, which meant
    // it mostly did not.
    expect(await screen.findAllByText("UNPAID · ₦8,000")).not.toHaveLength(0);
  });

  it("reads PAID off the same block once the counter has taken the money", async () => {
    mockQueue([unpaidRoute({
      billing: { billed: true, status: "paid", label: "PAID", requires_payment: false,
                 total: "8000.00", paid: "8000.00", outstanding: "0.00", settled: true },
      services: [service({ billing: { billed: true, status: "paid", requires_payment: false,
                                      amount: "8000.00", paid: "8000.00", outstanding: "0.00" } })],
    })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findAllByText("PAID · ₦8,000")).not.toHaveLength(0);
    expect(screen.queryByText(/^UNPAID/)).not.toBeInTheDocument();
  });

  it("shows PAYMENT REQUIRED on the request itself, and never hides the work", async () => {
    const user = userEvent.setup();
    mockQueue([unpaidRoute()]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    await openAndWrite(user);

    expect(await screen.findByText("PAYMENT REQUIRED")).toBeInTheDocument();
    // The report form is right there and the button is live: the scan is
    // still done. The only thing that gates it is having written a finding,
    // which is what it has always been.
    expect(screen.getByRole("button", { name: /Save & mark done/ })).toBeEnabled();
  });

  it("says NO PAYMENT REQUIRED for a waived service rather than UNPAID", async () => {
    const user = userEvent.setup();
    mockQueue([unpaidRoute({
      billing: { billed: true, status: "waived", label: "NO PAYMENT REQUIRED",
                 requires_payment: false, total: "8000.00", paid: "0.00",
                 outstanding: "0.00", settled: true },
    })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    await user.click(await screen.findByRole("button", { name: "Open →" }));
    expect(await screen.findByText("NO PAYMENT REQUIRED")).toBeInTheDocument();
    expect(screen.queryByText("PAYMENT REQUIRED")).not.toBeInTheDocument();
  });
});

describe("an unpaid request is still work to do", () => {
  it("closes in one call, with nothing standing in the way", async () => {
    // Rules 24 and 51, and the contract `test_radiology_workflow.py`'s
    // `test_an_unpaid_examination_is_shown_and_never_blocks_the_work` holds
    // on the server: the unit reads the money and is never gated by it. The
    // scan is done and the cash desk chases the balance.
    const user = userEvent.setup();
    mockQueue([unpaidRoute()]);
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });

    await openAndWrite(user);
    await user.click(screen.getByRole("button", { name: /Save & mark done/ }));

    await vi.waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/patient-routes/1/complete/");
    expect(body.get("report.findings")).toBe("Normal study.");
    // No dialog, no second round trip, no acknowledgement field.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
