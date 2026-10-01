import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import DepartmentStation from "./DepartmentStation.jsx";

// The Procedure Department (rule 59) on the station every unit works from:
// a board the server scopes to the doctors and nurses posted to Theatre /
// Procedures, a record written in sections with the materials used, and a
// completed record that locks for its staff and is corrected by an admin.

const UUID = "8b0e5f1e-1111-4000-8000-000000000000";
// Procedure is its own department (rule 59) — never Theatre.
const PROCEDURE = [{ id: 12, code: "procedure", name: "Procedure" }];
const THEATRE_ONLY = [{ id: 9, code: "theatre", name: "Theatre" }];
const NURSE = { id: 21, role: "nurse", authorized_departments: PROCEDURE };
const DOCTOR = { id: 22, role: "doctor", authorized_departments: PROCEDURE };
const UNPOSTED_DOCTOR = { id: 23, role: "doctor", authorized_departments: [] };
const THEATRE_NURSE = { id: 24, role: "nurse", authorized_departments: THEATRE_ONLY };
const ADMIN = { id: 1, role: "hospital_admin", authorized_departments: [] };

const route = (overrides) => ({
  id: 7, purpose: "procedure", purpose_label: "Procedure", status: "in_progress",
  priority: "routine", patient_id: 3, patient_uuid: UUID, patient_name: "John, Michael",
  patient_number: "NMHS-P000014", routed_by_name: "Chidi Nwosu",
  created_at: "2026-10-01T08:00:00Z", notes: "Abscess left forearm — I&D please",
  assigned_to: 21, assigned_to_name: "Bola Ade", result: "", result_data: null,
  department_name: "Procedure", services: [], can_work: true,
  ...overrides,
});

const SCHEMA = {
  purpose: "procedure",
  sections: [
    { key: "procedure_performed", label: "Procedure performed", kind: "short", hint: "", max_length: 255 },
    { key: "clinical_notes", label: "Procedure notes", kind: "text", hint: "", max_length: 4000 },
    { key: "findings", label: "Findings", kind: "text", hint: "", max_length: 4000 },
    { key: "outcome", label: "Outcome", kind: "text", hint: "", max_length: 4000 },
    { key: "follow_up", label: "Follow-up", kind: "text", hint: "", max_length: 4000 },
  ],
  materials: {
    max_rows: 50,
    text_fields: [
      { key: "name", label: "Item / material", max_length: 120 },
      { key: "category", label: "Category", max_length: 60 },
      { key: "unit", label: "Unit", max_length: 30 },
      { key: "notes", label: "Notes", max_length: 255 },
    ],
    quantities: [
      { key: "quantity_received", label: "Received" }, { key: "quantity_used", label: "Used" },
      { key: "quantity_remaining", label: "Remaining" }, { key: "wastage", label: "Wastage" },
    ],
    rule: "Received = Used + Remaining + Wastage",
  },
};

function mockQueue(rows) {
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url === "/patient-routes/") {
      return Promise.resolve({ data: config?.params?.status === "completed"
        ? rows.filter((r) => r.status === "completed") : rows.filter((r) => r.status !== "completed") });
    }
    if (url === "/patient-routes/report-fields/") return Promise.resolve({ data: { schema: SCHEMA } });
    return Promise.resolve({ data: [] });
  });
}

async function openFirst(user) {
  await user.click(await screen.findByRole("button", { name: "Open →" }));
  await screen.findByLabelText(/^Procedure performed/);
}

afterEach(() => vi.restoreAllMocks());

describe("the Procedures board", () => {
  it("shows posted staff the referrals, with who sent them and what for", async () => {
    mockQueue([route({})]);
    renderWithApp(<DepartmentStation station="procedure" />, { user: NURSE });
    expect(await screen.findByText("John, Michael")).toBeInTheDocument();
    expect(screen.queryByText(/not on the Procedure Department's staff/)).not.toBeInTheDocument();
  });

  it("tells a doctor who is not posted there why the board is empty", async () => {
    mockQueue([]);
    renderWithApp(<DepartmentStation station="procedure" />, { user: UNPOSTED_DOCTOR });
    expect(await screen.findByText(/not on the Procedure Department's staff/)).toBeInTheDocument();
  });

  it("treats Theatre staff as outside the Procedure department", async () => {
    mockQueue([]);
    renderWithApp(<DepartmentStation station="procedure" />, { user: THEATRE_NURSE });
    expect(await screen.findByText(/not on the Procedure Department's staff/)).toBeInTheDocument();
  });

  it("says when there is nothing waiting", async () => {
    mockQueue([]);
    renderWithApp(<DepartmentStation station="procedure" />, { user: NURSE });
    await waitFor(() => expect(screen.queryByText("John, Michael")).not.toBeInTheDocument());
  });

  it("offers a held procedure's doctor the patient's chart", async () => {
    mockQueue([route({ assigned_to: 22 })]);
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="procedure" />, { user: DOCTOR });
    await openFirst(user);
    expect(screen.getByRole("link", { name: "Open chart" }))
      .toHaveAttribute("href", `/patients/${UUID}`);
  });
});

describe("documenting a procedure", () => {
  it("sends the sections and the materials, as the server reads them", async () => {
    mockQueue([route({})]);
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="procedure" />, { user: NURSE });
    await openFirst(user);

    await user.type(screen.getByLabelText(/^Procedure performed/), "Incision and drainage");
    await user.type(screen.getByLabelText(/^Follow-up/), "Review in 48 hours");
    await user.click(screen.getByRole("button", { name: "+ Add material" }));
    const row = screen.getByRole("group", { name: "Material 1" });
    await user.type(within(row).getByLabelText(/^Item \/ material/), "Gauze swab");
    await user.type(within(row).getByLabelText(/^Received/), "10");
    await user.type(within(row).getByLabelText(/^Used/), "6");
    await user.type(within(row).getByLabelText(/^Remaining/), "3");
    await user.type(within(row).getByLabelText(/^Wastage/), "1");
    await user.type(within(row).getByLabelText(/^Unit/), "piece");
    await user.click(screen.getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(post).toHaveBeenCalled());
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/patient-routes/7/record-result/");
    expect(body.get("report.procedure_performed")).toBe("Incision and drainage");
    expect(body.get("report.follow_up")).toBe("Review in 48 hours");
    expect(JSON.parse(body.get("report.materials"))).toEqual([{
      name: "Gauze swab", unit: "piece", quantity_received: "10", quantity_used: "6",
      quantity_remaining: "3", wastage: "1",
    }]);
    expect(body.get("result")).toBeNull();          // the server renders the text
  });

  it("will not save materials that do not add up, and says why", async () => {
    mockQueue([route({})]);
    const post = vi.spyOn(api, "post");
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="procedure" />, { user: NURSE });
    await openFirst(user);
    await user.type(screen.getByLabelText(/^Procedure performed/), "Dressing");
    await user.click(screen.getByRole("button", { name: "+ Add material" }));
    const row = screen.getByRole("group", { name: "Material 1" });
    await user.type(within(row).getByLabelText(/^Item \/ material/), "Gauze");
    await user.type(within(row).getByLabelText(/^Received/), "10");
    await user.type(within(row).getByLabelText(/^Used/), "6");
    expect(within(row).getByRole("alert")).toHaveTextContent(
      "Received (10) must equal used + remaining + wastage (6).");
    expect(screen.getByRole("button", { name: "Save result" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Save & mark done/ })).toBeDisabled();
    expect(post).not.toHaveBeenCalled();
  });

  it("re-opens saved rows, and removing one removes it", async () => {
    mockQueue([route({ result: "x", result_data: {
      procedure_performed: "Dressing",
      materials: [{ name: "Gauze", quantity_used: "2.00" }, { name: "Crepe bandage", quantity_used: "1.00" }],
    } })]);
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="procedure" />, { user: NURSE });
    await openFirst(user);
    expect(screen.getByLabelText(/^Procedure performed/)).toHaveValue("Dressing");
    await user.click(screen.getByRole("button", { name: "Remove Gauze" }));
    await user.click(screen.getByRole("button", { name: "Save result" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(JSON.parse(post.mock.calls[0][1].get("report.materials")).map((r) => r.name))
      .toEqual(["Crepe bandage"]);
  });

  it("shows the server's refusal under the row it was about", async () => {
    mockQueue([route({})]);
    vi.spyOn(api, "post").mockRejectedValue({ response: { status: 400, data: {
      report: { materials: { 0: { name: ["Keep this to 120 characters."] } } }, code: "invalid_report",
    } } });
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="procedure" />, { user: NURSE });
    await openFirst(user);
    await user.type(screen.getByLabelText(/^Procedure performed/), "Dressing");
    await user.click(screen.getByRole("button", { name: "+ Add material" }));
    const row = screen.getByRole("group", { name: "Material 1" });
    await user.type(within(row).getByLabelText(/^Item \/ material/), "Gauze");
    await user.type(within(row).getByLabelText(/^Used/), "1");
    await user.click(screen.getByRole("button", { name: "Save result" }));
    expect(await within(row).findByText("Keep this to 120 characters.")).toBeInTheDocument();
  });

  it("completes it through the same call, with the record", async () => {
    mockQueue([route({})]);
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="procedure" />, { user: NURSE });
    await openFirst(user);
    await user.type(screen.getByLabelText(/^Procedure performed/), "Dressing");
    await user.click(screen.getByRole("button", { name: /Save & mark done/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][0]).toBe("/patient-routes/7/complete/");
    expect(post.mock.calls[0][1].get("report.procedure_performed")).toBe("Dressing");
  });
});

describe("a completed procedure", () => {
  const completed = route({ status: "completed", result: "Procedure performed:\nDressing",
                            result_data: { procedure_performed: "Dressing",
                                           materials: [{ name: "Gauze", quantity_used: "2.00" }] } });

  async function openCompleted(user) {
    await user.click(await screen.findByRole("button", { name: "Completed" }));
    await openFirst(user);
  }

  it("is read-only for its staff", async () => {
    mockQueue([completed]);
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="procedure" />, { user: NURSE });
    await openCompleted(user);
    expect(screen.getByText(/completed and its record is locked/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Procedure performed/)).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Add material" })).not.toBeInTheDocument();
  });

  it("is corrected by an administrator", async () => {
    mockQueue([completed]);
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="procedure" />, { user: ADMIN });
    await openCompleted(user);
    const field = screen.getByLabelText(/^Procedure performed/);
    expect(field).toBeEnabled();
    await user.clear(field);
    await user.type(field, "Dressing, left leg");
    await user.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][1].get("report.procedure_performed")).toBe("Dressing, left leg");
  });

  it("can be printed as the procedure record", async () => {
    mockQueue([completed]);
    const user = userEvent.setup();
    renderWithApp(<DepartmentStation station="procedure" />, { user: NURSE });
    await openCompleted(user);
    // The Print button is named by the document it prints first: the record.
    expect(screen.getByRole("button", { name: /^Report/ })).toBeInTheDocument();
  });
});
