import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import DepartmentStation from "./DepartmentStation.jsx";

// The radiology worklist is the station every unit already works from — what
// is new is that a request now says *what was asked for* and where the money
// stands, read off the route's own `services` (workflow.RouteService).
//
// Payment is shown and never enforced: rule 24's boundary, which the
// laboratory already works to — a patient on the couch is scanned and the
// cash desk chases the balance.

const UUID = "8b0e5f1e-1111-4000-8000-000000000000";

const service = (overrides) => ({
  id: 1, name: "Abdominal Ultrasound", unit_price: "8000.00",
  billing: { billed: true, status: "unpaid", amount: "8000.00", paid: "0.00" },
  ...overrides,
});

const route = (overrides) => ({
  id: 1, purpose: "ultrasound", status: "in_progress", status_label: "In progress",
  priority: "routine", patient_id: 3, patient_uuid: UUID, patient_name: "Obi, Ada",
  patient_number: "NMHS-P000012", routed_by_name: "Dr Chidi Nwosu",
  created_at: "2026-09-15T08:00:00Z", notes: "RUQ pain, rule out gallstones",
  assigned_to: 9, assigned_to_name: "Bisi Ade", result: "", patient_age: "34 years",
  patient_sex: "Female", patient_phone: "0803 111 2222", visit_reason: "Abdominal pain",
  visit_type: "Outpatient", department_name: "Radiology / Ultrasound",
  services: [service({})],
  ...overrides,
});

// The sections the server defines for an imaging report
// (`workflow/report_fields.py`). The station holds no copy of this list — it
// renders whatever the endpoint answers, which is what these mocks stand in
// for.
const REPORT_SCHEMA = {
  purpose: "ultrasound",
  sections: [
    { key: "technique", label: "Technique", kind: "short", hint: "How it was performed.",
      max_length: 255 },
    { key: "findings", label: "Findings", kind: "text", hint: "What was seen.", max_length: 4000 },
    { key: "measurements", label: "Measurements", kind: "text", hint: "The figures taken.",
      max_length: 4000 },
    { key: "impression", label: "Impression", kind: "text", hint: "The conclusion.",
      max_length: 4000 },
  ],
};

function mockQueue(rows, { schema = REPORT_SCHEMA } = {}) {
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url === "/patient-routes/") {
      return Promise.resolve({ data: config?.params?.status === "completed" ? [] : rows });
    }
    if (url === "/patient-routes/report-fields/") return Promise.resolve({ data: { schema } });
    return Promise.resolve({ data: [] });
  });
}

const RADIOGRAPHER = { id: 9, role: "radiology" };

afterEach(() => vi.restoreAllMocks());

describe("the radiology worklist", () => {
  it("says which examination was requested, and what it costs", async () => {
    mockQueue([route({})]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findByText("Abdominal Ultrasound")).toBeInTheDocument();
    // The status and the figure are one badge — the word a unit reads and the
    // amount it would be quoting to the patient, in `components/billingStatus.js`.
    expect(screen.getByText("UNPAID · ₦8,000")).toBeInTheDocument();
  });

  it("shows the payment state the desk has reached, and never hides the work", async () => {
    mockQueue([
      route({}),
      route({ id: 2, patient_name: "Eze, Bo", patient_uuid: "9c1f6a2f-2222-4000-8000-000000000000",
              services: [service({ id: 2, name: "Obstetric Ultrasound (Dating)",
                                   billing: { billed: true, status: "paid", amount: "8000.00" } })] }),
    ]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findByText(/^UNPAID/)).toBeInTheDocument();
    expect(screen.getByText(/^PAID/)).toBeInTheDocument();
    // Both patients are on the list — an unpaid scan is still work to do.
    expect(screen.getByText(/Obi, Ada/)).toBeInTheDocument();
    expect(screen.getByText(/Eze, Bo/)).toBeInTheDocument();
  });

  it("carries the context the unit needs before calling the patient in", async () => {
    mockQueue([route({})]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findByText(/Dr Chidi Nwosu/)).toBeInTheDocument();
    expect(screen.getByText(/RUQ pain, rule out gallstones/)).toBeInTheDocument();
  });

  it("lists the examinations again on the request itself, beside the report form", async () => {
    const user = userEvent.setup();
    mockQueue([route({ services: [service({}),
                                  service({ id: 2, name: "Doppler Study (Obstetric)",
                                            billing: { billed: true, status: "part_paid",
                                                       amount: "15000.00" } })] })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    await user.click(await screen.findByRole("button", { name: "Open →" }));

    expect(await screen.findByText("Examinations requested")).toBeInTheDocument();
    expect(screen.getAllByText("Doppler Study (Obstetric)").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^PARTIALLY PAID/).length).toBeGreaterThan(0);
    // The report is written on the same panel it always was — now in the
    // sections the server defines rather than one box of prose.
    expect(await screen.findByLabelText(/Findings/)).toBeInTheDocument();
  });

  it("shows nothing extra for a referral that named no examination", async () => {
    mockQueue([route({ services: [] })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    await screen.findByText(/Obi, Ada/);
    expect(screen.queryByText("Examinations requested")).not.toBeInTheDocument();
    expect(screen.queryByText(/^UNPAID/)).not.toBeInTheDocument();
  });
});


describe("the imaging report the sonographer writes", () => {
  it("asks for the sections the server defines, not one box of prose", async () => {
    const user = userEvent.setup();
    mockQueue([route({})]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    await user.click(await screen.findByRole("button", { name: "Open →" }));

    for (const section of REPORT_SCHEMA.sections) {
      expect(await screen.findByLabelText(new RegExp(section.label))).toBeInTheDocument();
    }
    expect(screen.queryByLabelText("Report")).not.toBeInTheDocument();
  });

  it("sends each section under its own key and never composes the text itself", async () => {
    const user = userEvent.setup();
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    mockQueue([route({})]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    await user.click(await screen.findByRole("button", { name: "Open →" }));

    await user.type(await screen.findByLabelText(/Technique/), "Transabdominal, full bladder");
    await user.type(screen.getByLabelText(/Findings/), "Normal liver echotexture.");
    await user.type(screen.getByLabelText(/Impression/), "No gallstones seen.");
    await user.click(screen.getByRole("button", { name: /Save result/i }));

    await waitFor(() => expect(post).toHaveBeenCalled());
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/patient-routes/1/record-result/");
    expect(body.get("report.technique")).toBe("Transabdominal, full bladder");
    expect(body.get("report.findings")).toBe("Normal liver echotexture.");
    expect(body.get("report.impression")).toBe("No gallstones seen.");
    // The rendered text is the server's to write.
    expect(body.get("result")).toBeNull();
  });

  it("re-opens a saved report in its own sections", async () => {
    const user = userEvent.setup();
    mockQueue([route({ result_data: { findings: "Bulky uterus.", impression: "Fibroid uterus." },
                       result: "Findings:\nBulky uterus.\n\nImpression:\nFibroid uterus." })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    await user.click(await screen.findByRole("button", { name: "Open →" }));

    expect(await screen.findByLabelText(/Findings/)).toHaveValue("Bulky uterus.");
    expect(screen.getByLabelText(/Impression/)).toHaveValue("Fibroid uterus.");
  });

  it("keeps the single findings box for a unit whose report has no sections", async () => {
    const user = userEvent.setup();
    mockQueue([route({})], { schema: null });
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    await user.click(await screen.findByRole("button", { name: "Open →" }));
    expect(await screen.findByLabelText("Report")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Impression/)).not.toBeInTheDocument();
  });
});


describe("who has what, on the unit's board", () => {
  it("offers Accept while nobody has taken it", async () => {
    mockQueue([route({ status: "queued", status_label: "Queued", assigned_to: null,
                       assigned_to_name: null, can_accept: true, can_work: true,
                       claimed_by_other: false })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findByRole("button", { name: "Accept" })).toBeInTheDocument();
    expect(screen.getByText("Unclaimed")).toBeInTheDocument();
  });

  it("marks it accepted and names who has it, for everyone else", async () => {
    mockQueue([route({ assigned_to: 12, assigned_to_name: "Tunde Okafor",
                       can_accept: false, can_work: false, claimed_by_other: true })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });

    expect(await screen.findByText(/Accepted · Tunde Okafor/)).toBeInTheDocument();
    expect(screen.getByText("With Tunde Okafor")).toBeInTheDocument();
    // No control that the server would only refuse.
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open →" })).not.toBeInTheDocument();
  });

  it("still shows the patient rather than dropping them off the list", async () => {
    mockQueue([route({ assigned_to: 12, assigned_to_name: "Tunde Okafor",
                       claimed_by_other: true, can_work: false })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findByText(/Obi, Ada/)).toBeInTheDocument();
    expect(screen.getByText("Abdominal Ultrasound")).toBeInTheDocument();
  });

  it("gives the person holding it the working panel", async () => {
    mockQueue([route({ assigned_to: 9, assigned_to_name: "Bisi Ade",
                       can_work: true, claimed_by_other: false })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findByText("Yours")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open →" })).toBeInTheDocument();
  });

  it("shows a radiologist the request named to them as theirs", async () => {
    mockQueue([route({ id: 4, status: "queued", status_label: "Queued", assigned_to: 9,
                       assigned_to_name: "Bisi Ade", can_work: true, can_accept: false,
                       claimed_by_other: false })]);
    renderWithApp(<DepartmentStation station="ultrasound" />, { user: RADIOGRAPHER });
    expect(await screen.findByText("Yours")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open →" })).toBeInTheDocument();
  });
});
