import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import MedicalNotesTab from "./MedicalNotesTab.jsx";

// The notes tab as a longitudinal record: a list of visits, a read-only view
// of one, amending it with a reason, and the trail that results. The server
// decides who may amend (`may_amend`); `can_amend` is what it says, and this
// page only reflects it.

const SMITH = { id: 4, role: "doctor", username: "smith" };
const JANE = { id: 5, role: "doctor", username: "jane" };
const PATIENT = { id: 3, first_name: "John", last_name: "Doe", patient_number: "NMHS-P000123" };

const note = (overrides = {}) => ({
  id: 1, patient: 3, doctor: SMITH.id, doctor_name: "Dr John Smith",
  doctor_staff_number: "NMHS-S000012", patient_name: "Doe, John",
  patient_number: "NMHS-P000123",
  visit_time: "2026-09-16T10:32:00Z", created_at: "2026-09-16T10:32:00Z",
  reason_for_visit: "Blurred vision", chief_complaint: "Two weeks",
  note_text: "Progressive blurring.", diagnosis: "Refractive error",
  plan: "Refraction and follow-up.", eye_examination: null, is_locked: true,
  is_amended: false, last_amended_at: null, last_amended_by_name: null,
  amendment_count: 0, can_amend: true, amendments: [],
  ...overrides,
});

const AMENDED = note({
  is_amended: true, diagnosis: "Myopia with astigmatism",
  last_amended_at: "2026-09-16T11:15:00Z", last_amended_by_name: "Dr John Smith",
  amendment_count: 1,
  amendments: [{
    id: 11, created_at: "2026-09-16T11:15:00Z", amended_by_name: "Dr John Smith",
    reason: "correction", reason_label: "Correction of error",
    detail: "The diagnosis was entered incorrectly.",
    changes: [{ field: "diagnosis", label: "Diagnosis",
                previous: "Refractive error", current: "Myopia with astigmatism" }],
  }],
});

const VISIT_2 = note({ id: 2, visit_time: "2026-10-02T09:00:00Z", reason_for_visit: "Follow-up",
                       diagnosis: "Continue monitoring", created_at: "2026-10-02T09:00:00Z" });

function mockGets(notes = [note()]) {
  return vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/notes/") return Promise.resolve({ data: notes });
    return Promise.resolve({ data: [] });
  });
}

afterEach(() => vi.restoreAllMocks());

describe("the patient's clinical notes", () => {
  it("lists every visit, newest first, without collapsing them into one", async () => {
    mockGets([VISIT_2, note()]);
    renderWithApp(<MedicalNotesTab patientId={3} patient={PATIENT} />, { user: SMITH });
    expect(await screen.findByText("Follow-up")).toBeInTheDocument();
    expect(screen.getByText("Blurred vision")).toBeInTheDocument();
  });

  it("marks an amended visit as amended, and leaves an untouched one alone", async () => {
    mockGets([VISIT_2, AMENDED]);
    renderWithApp(<MedicalNotesTab patientId={3} patient={PATIENT} />, { user: SMITH });
    await screen.findByText("Blurred vision");
    expect(screen.getAllByText("Amended")).not.toHaveLength(0);
    // Visit 2 was never corrected, so nothing on its row says it was.
    const row = screen.getByText("Follow-up").closest("div").parentElement;
    expect(within(row).queryByText("Amended")).not.toBeInTheDocument();
  });

  it("opens a saved note read-only — no form until Amend is pressed", async () => {
    const user = userEvent.setup();
    mockGets();
    renderWithApp(<MedicalNotesTab patientId={3} patient={PATIENT} />, { user: SMITH });
    await user.click(await screen.findByRole("button", { name: /Blurred vision/ }));

    expect(await screen.findByText("Refractive error")).toBeInTheDocument();
    // Nothing typeable: viewing a record cannot change it by accident.
    expect(screen.queryByLabelText(/Reason for visit/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Amend note" })).toBeInTheDocument();
  });

  it("shows who documented it and that it has not been amended", async () => {
    const user = userEvent.setup();
    mockGets();
    renderWithApp(<MedicalNotesTab patientId={3} patient={PATIENT} />, { user: SMITH });
    await user.click(await screen.findByRole("button", { name: /Blurred vision/ }));
    expect(await screen.findByText("NMHS-S000012")).toBeInTheDocument();
    expect(screen.getByText("Original")).toBeInTheDocument();
  });
});

describe("amending a note", () => {
  const open = async (user, who = SMITH, rows) => {
    mockGets(rows);
    renderWithApp(<MedicalNotesTab patientId={3} patient={PATIENT} />, { user: who });
    await user.click(await screen.findByRole("button", { name: /Blurred vision/ }));
  };

  it("offers Amend only to whoever the server says may amend", async () => {
    const user = userEvent.setup();
    await open(user, JANE, [note({ can_amend: false })]);
    expect(await screen.findByText("Refractive error")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Amend note" })).not.toBeInTheDocument();
  });

  it("will not send an amendment until a reason is chosen", async () => {
    const user = userEvent.setup();
    await open(user);
    await user.click(await screen.findByRole("button", { name: "Amend note" }));

    const save = screen.getByRole("button", { name: "Save amendment" });
    expect(save).toBeDisabled();
    expect(screen.getByText("Choose a reason for the amendment to save it.")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/Reason for amendment/), "correction");
    expect(save).toBeEnabled();
  });

  it("sends the correction with its reason, and PATCHes the same note", async () => {
    const user = userEvent.setup();
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ data: AMENDED });
    const post = vi.spyOn(api, "post");
    await open(user);
    await user.click(await screen.findByRole("button", { name: "Amend note" }));

    await user.selectOptions(screen.getByLabelText(/Reason for amendment/), "correction");
    const diagnosis = screen.getByLabelText("Diagnosis");
    await user.clear(diagnosis);
    await user.type(diagnosis, "Myopia with astigmatism");
    await user.type(screen.getByLabelText(/Additional explanation/), "Entered incorrectly.");
    await user.click(screen.getByRole("button", { name: "Save amendment" }));

    await waitFor(() => expect(patch).toHaveBeenCalled());
    const [url, body] = patch.mock.calls[0];
    expect(url).toBe("/notes/1/");
    expect(body).toMatchObject({
      diagnosis: "Myopia with astigmatism",
      amendment_reason: "correction",
      amendment_detail: "Entered incorrectly.",
    });
    // An amendment corrects a note; it never creates a second one.
    expect(post).not.toHaveBeenCalled();
  });

  it("surfaces the server's refusal when the reason is missing", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "patch").mockRejectedValue({
      response: { data: { code: "amendment_reason_required",
                          amendment_reason: "Say why this note is being amended.",
                          choices: [{ value: "correction", label: "Correction of error" }] } },
    });
    await open(user);
    await user.click(await screen.findByRole("button", { name: "Amend note" }));
    await user.selectOptions(screen.getByLabelText(/Reason for amendment/), "correction");
    await user.click(screen.getByRole("button", { name: "Save amendment" }));

    expect(await screen.findByText("Say why this note is being amended.")).toBeInTheDocument();
  });

  it("reads the trail back: what changed, by whom, and why", async () => {
    const user = userEvent.setup();
    await open(user, SMITH, [AMENDED]);
    await user.click(await screen.findByRole("button", { name: /amendment history/i }));

    const history = await screen.findByRole("region", { name: "Amendment history" });
    expect(within(history).getByText("Refractive error")).toBeInTheDocument();
    expect(within(history).getByText("Myopia with astigmatism")).toBeInTheDocument();
    expect(within(history).getByText(/Correction of error/)).toBeInTheDocument();
    expect(within(history).getByText(/The diagnosis was entered incorrectly/)).toBeInTheDocument();
    // The chain reads back to who first wrote it.
    expect(within(history).getByText(/Original/)).toBeInTheDocument();
    expect(within(history).getByText(/Created by Dr John Smith/)).toBeInTheDocument();
  });
});

describe("printing a note", () => {
  it("prints from the row without opening the note", async () => {
    mockGets([note()]);
    renderWithApp(<MedicalNotesTab patientId={3} patient={PATIENT} />, { user: SMITH });
    expect(await screen.findByRole("button", { name: /Medical note/ })).toBeInTheDocument();
  });

  it("prints a saved note from the note itself, amended or not", async () => {
    const user = userEvent.setup();
    mockGets([AMENDED]);
    renderWithApp(<MedicalNotesTab patientId={3} patient={PATIENT} />, { user: SMITH });
    await user.click(await screen.findByRole("button", { name: /Blurred vision/ }));
    expect(await screen.findAllByRole("button", { name: /Medical note/ })).not.toHaveLength(0);
  });

  it("offers no print button to a role that may not print a clinical note", async () => {
    mockGets([note()]);
    renderWithApp(<MedicalNotesTab patientId={3} patient={PATIENT} />,
                  { user: { id: 8, role: "reception" } });
    await screen.findByText("Blurred vision");
    expect(screen.queryByRole("button", { name: /Medical note/ })).not.toBeInTheDocument();
  });
});
