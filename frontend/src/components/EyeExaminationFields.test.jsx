import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import EyeExaminationFields from "./EyeExaminationFields.jsx";

// The form draws itself from the server's own definition
// (`clinical/eye_exam.py`, served at /notes/eye-examination-fields/), so the
// fixture is that shape and this is a test of how each *kind* of field
// behaves — not of which fields exist, which is the server's to say.

const FIELDS = {
  eyes: [{ key: "right", label: "Right eye" }, { key: "left", label: "Left eye" }],
  sections: [
    {
      key: "complaint", label: "Presenting complaint", rows: [],
      fields: [
        { key: "complaints", label: "What brought them in", kind: "multi",
          choices: [{ value: "blurred_vision", label: "Blurred vision" },
                    { value: "redness", label: "Redness" },
                    { value: "floaters", label: "Floaters" }] },
        { key: "complaint_onset", label: "Onset", kind: "choice",
          choices: [{ value: "sudden", label: "Sudden" }, { value: "gradual", label: "Gradual" }] },
      ],
    },
    {
      key: "pupils", label: "Pupils",
      rows: [{ name: "pupil", label: "Pupil", right: "pupil_right", left: "pupil_left",
               kind: "choice", choices: [{ value: "normal", label: "Normal" },
                                         { value: "abnormal", label: "Abnormal" }] }],
      fields: [],
    },
    {
      key: "refraction", label: "Refraction",
      rows: [{ name: "sphere", label: "Sphere", right: "sphere_right", left: "sphere_left",
               kind: "short", max_length: 60 }],
      fields: [],
    },
  ],
};

beforeEach(() => {
  vi.spyOn(api, "get").mockResolvedValue({ data: FIELDS });
});
afterEach(() => vi.restoreAllMocks());

function renderForm({ value = {}, disabled = false, errors } = {}) {
  const onChange = vi.fn();
  renderWithApp(
    <EyeExaminationFields value={value} onChange={onChange} disabled={disabled} errors={errors} />,
    { user: { id: 9, role: "ophthalmologist" } },
  );
  return onChange;
}

describe("the eye examination form", () => {
  it("ticks and unticks a presenting complaint without touching the others", async () => {
    const user = userEvent.setup();
    const onChange = renderForm({ value: { complaints: ["redness"] } });

    const redness = await screen.findByRole("button", { name: /Redness/ });
    expect(redness).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Floaters" })).toHaveAttribute("aria-pressed", "false");

    await user.click(screen.getByRole("button", { name: "Floaters" }));
    expect(onChange).toHaveBeenCalledWith({ complaints: ["redness", "floaters"] });

    await user.click(redness);
    expect(onChange).toHaveBeenLastCalledWith({ complaints: [] });
  });

  it("offers each choice field its own options, per eye where the finding is per eye", async () => {
    renderForm();
    const onset = await screen.findByLabelText("Onset");
    // "Not recorded" first: a blank is a box nobody filled in, and is dropped
    // rather than stored, so it has to be reachable after a value is picked.
    expect([...onset.options].map((o) => o.textContent))
      .toEqual(["Not recorded", "Sudden", "Gradual"]);
    // A paired row is one finding asked twice, never one box for both eyes —
    // and each choice field carries its own list, not the pressure method's.
    const pupil = screen.getByLabelText("Pupil — right eye");
    expect([...pupil.options].map((o) => o.textContent))
      .toEqual(["Not recorded", "Normal", "Abnormal"]);
    expect(screen.getByLabelText("Pupil — left eye")).toBeInTheDocument();
  });

  it("keeps refraction as text, so a plus sign survives", async () => {
    const user = userEvent.setup();
    const onChange = renderForm();
    const sphere = await screen.findByLabelText("Sphere — right eye");
    expect(sphere).toHaveAttribute("maxLength", "60");
    expect(sphere.type).not.toBe("number");
    await user.type(sphere, "+");
    expect(onChange).toHaveBeenCalledWith({ sphere_right: "+" });
  });

  it("nothing is required — an empty examination renders every box and refuses nothing", async () => {
    renderForm();
    expect(await screen.findByLabelText("Sphere — right eye")).toHaveValue("");
    expect(screen.queryByText("*")).not.toBeInTheDocument();
  });

  it("shows a saved note what was examined, as text rather than a page of boxes", async () => {
    renderForm({ value: { complaints: ["blurred_vision"], sphere_right: "+1.25",
                          pupil_right: "abnormal" }, disabled: true });
    // The ticked complaints read back through the catalogue's labels.
    expect(await screen.findByText("Blurred vision")).toBeInTheDocument();

    // A recorded finding is content, not a greyed-out input inviting a click.
    const sphere = screen.getByLabelText("Sphere — right eye");
    expect(sphere.tagName).toBe("P");
    expect(sphere).toHaveTextContent("+1.25");
    expect(screen.queryByRole("textbox", { name: "Sphere — right eye" })).not.toBeInTheDocument();

    // A choice reads by its label, never by its stored value.
    expect(screen.getByLabelText("Pupil — right eye")).toHaveTextContent("Abnormal");
    expect(screen.queryByRole("combobox", { name: "Pupil — right eye" })).not.toBeInTheDocument();
  });

  it("leaves out a section nothing was recorded in", async () => {
    renderForm({ value: { sphere_right: "+1.25" }, disabled: true });
    expect(await screen.findByLabelText("Sphere — right eye")).toBeInTheDocument();
    // Pupils were never examined. A printed or displayed heading with nothing
    // under it reads as "examined, normal", which is the one thing a clinical
    // record must never imply.
    expect(screen.queryByText("Pupils")).not.toBeInTheDocument();
    expect(screen.queryByText("Presenting complaint")).not.toBeInTheDocument();
  });

  it("puts the server's complaint about a field beside that field", async () => {
    renderForm({ errors: { sphere_right: ["Keep this to 60 characters."] } });
    const row = (await screen.findByLabelText("Sphere — right eye")).closest("div").parentElement;
    expect(within(row).getByText("Keep this to 60 characters.")).toBeInTheDocument();
  });
});
