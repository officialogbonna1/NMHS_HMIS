import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import MedicalNotesTab from "./MedicalNotesTab.jsx";

// The eye doctor writes the ordinary consultation note with an eye examination
// under it; the general doctor's note is sent exactly as before. The fields are
// the server's (`/notes/eye-examination-fields/`), so the fixture is that shape.

const EYE_DOCTOR = { id: 9, role: "ophthalmologist", username: "eye" };
const DOCTOR = { id: 4, role: "doctor", username: "doc" };

const FIELDS = {
  eyes: [{ key: "right", label: "Right eye" }, { key: "left", label: "Left eye" }],
  sections: [
    { key: "vision", label: "Vision", fields: [],
      rows: [{ name: "distance", label: "Distance", right: "distance_right", left: "distance_left",
               kind: "short", max_length: 60 }] },
    { key: "pressure", label: "Eye pressure",
      rows: [{ name: "iop", label: "IOP (mmHg)", right: "iop_right", left: "iop_left", kind: "number",
               min: 0, max: 80, unit: "mmHg" }],
      fields: [{ key: "iop_method", label: "Measurement method", kind: "choice",
                 choices: [{ value: "applanation", label: "Applanation (Goldmann)" },
                           { value: "rebound", label: "Rebound (iCare)" }] }] },
  ],
};

function mockGets(notes = []) {
  return vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/notes/eye-examination-fields/") return Promise.resolve({ data: FIELDS });
    if (url === "/notes/") return Promise.resolve({ data: notes });
    return Promise.resolve({ data: [] });
  });
}

const reasonBox = () => screen.getByLabelText(/Reason for visit/);

afterEach(() => vi.restoreAllMocks());

describe("the eye doctor's consultation note", () => {
  it("records only what was examined, on the same note", async () => {
    const user = userEvent.setup();
    mockGets();
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { id: 1 } });
    renderWithApp(<MedicalNotesTab patientId={3} />, { user: EYE_DOCTOR });

    await user.click(await screen.findByRole("button", { name: "New note" }));
    const exam = await screen.findByRole("region", { name: "Eye examination" });
    await user.type(reasonBox(), "Blurred vision");
    await user.type(within(exam).getByLabelText("IOP (mmHg) — right eye"), "28");
    await user.selectOptions(within(exam).getByLabelText("Measurement method"), "applanation");
    await user.click(screen.getByRole("button", { name: "Save medical note" }));

    await waitFor(() => expect(post).toHaveBeenCalled());
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/notes/");
    expect(body).toMatchObject({ patient: 3, reason_for_visit: "Blurred vision" });
    // Untouched fields are not sent at all; the server drops blanks anyway.
    expect(body.eye_examination).toEqual({ iop_right: "28", iop_method: "applanation" });
  });

  it("shows the server's refusal against the field it is about", async () => {
    const user = userEvent.setup();
    mockGets();
    vi.spyOn(api, "post").mockRejectedValue({
      response: { status: 400, data: { eye_examination: { iop_right: ["Enter a pressure between 0 and 80 mmHg."] } } },
    });
    renderWithApp(<MedicalNotesTab patientId={3} />, { user: EYE_DOCTOR });

    await user.click(await screen.findByRole("button", { name: "New note" }));
    await screen.findByRole("region", { name: "Eye examination" });
    await user.type(reasonBox(), "Pain");
    await user.type(screen.getByLabelText("IOP (mmHg) — right eye"), "95");
    await user.click(screen.getByRole("button", { name: "Save medical note" }));

    expect(await screen.findByText("Enter a pressure between 0 and 80 mmHg.")).toBeInTheDocument();
    expect(screen.getByText("Check the eye examination below.")).toBeInTheDocument();
  });

  it("shows a locked note's examination read-only, without the empty rows", async () => {
    const user = userEvent.setup();
    mockGets([{ id: 7, doctor: EYE_DOCTOR.id, visit_time: "2026-09-15T09:00:00Z", reason_for_visit: "Glaucoma review",
                note_text: "", is_locked: true, eye_examination: { iop_right: 28 }, amendments: [] }]);
    renderWithApp(<MedicalNotesTab patientId={3} />, { user: DOCTOR });

    expect(await screen.findByText("Eye examination")).toBeInTheDocument();   // the list badge
    await user.click(screen.getByRole("button", { name: /Glaucoma review/ }));
    // Opening a saved note shows the record, never an editable form.
    const exam = await screen.findByRole("region", { name: "Eye examination" });
    // Read as a record: the value as text, with its unit, and no input to type in.
    expect(within(exam).getByLabelText("IOP (mmHg) — right eye")).toHaveTextContent("28 mmHg");
    expect(within(exam).queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(within(exam).queryByLabelText("Distance — right eye")).not.toBeInTheDocument();
  });
});

describe("the general doctor's consultation note", () => {
  it("has no eye examination and sends the note exactly as before", async () => {
    const user = userEvent.setup();
    const get = mockGets();
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { id: 2 } });
    renderWithApp(<MedicalNotesTab patientId={3} />, { user: DOCTOR });

    await user.click(await screen.findByRole("button", { name: "New note" }));
    await user.type(reasonBox(), "Headache");
    expect(screen.queryByRole("region", { name: "Eye examination" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save medical note" }));

    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).not.toHaveProperty("eye_examination");
    expect(get.mock.calls.map(([url]) => url)).not.toContain("/notes/eye-examination-fields/");
  });
});
