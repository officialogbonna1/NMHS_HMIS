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

/**
 * Open a collapsed group so its findings render.
 *
 * Refraction, Pupils, the anterior and posterior segments start closed — the
 * examination is long and a clinician opens what they examined. A group that
 * already holds a value opens itself, so this is only needed for an empty one.
 */
async function openGroup(user, label) {
  await user.click(await screen.findByRole("button", { name: new RegExp(label, "i") }));
}

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
    const user = userEvent.setup();
    renderForm();
    const onset = await screen.findByLabelText("Onset");
    // "Not recorded" first: a blank is a box nobody filled in, and is dropped
    // rather than stored, so it has to be reachable after a value is picked.
    expect([...onset.options].map((o) => o.textContent))
      .toEqual(["Not recorded", "Sudden", "Gradual"]);
    // A paired row is one finding asked twice, never one box for both eyes —
    // and each choice field carries its own list, not the pressure method's.
    await openGroup(user, "Pupils");
    const pupil = screen.getByLabelText("Pupil — right eye");
    expect([...pupil.options].map((o) => o.textContent))
      .toEqual(["Not recorded", "Normal", "Abnormal"]);
    expect(screen.getByLabelText("Pupil — left eye")).toBeInTheDocument();
  });

  it("keeps refraction as text, so a plus sign survives", async () => {
    const user = userEvent.setup();
    const onChange = renderForm();
    await openGroup(user, "Refraction");
    const sphere = await screen.findByLabelText("Sphere — right eye");
    expect(sphere).toHaveAttribute("maxLength", "60");
    expect(sphere.type).not.toBe("number");
    await user.type(sphere, "+");
    expect(onChange).toHaveBeenCalledWith({ sphere_right: "+" });
  });

  it("nothing is required — an empty examination renders every box and refuses nothing", async () => {
    const user = userEvent.setup();
    renderForm();
    await openGroup(user, "Refraction");
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

// The manual consultation sheet's questions, rendered from the served
// catalogue — the form holds no copy of the field list (rule 43), so these
// tests give it a schema shaped like the server's and check what appears.
describe("the manual sheet's history and measurements", () => {
  const HISTORY_SCHEMA = {
    eyes: [{ key: "right", label: "Right eye" }, { key: "left", label: "Left eye" }],
    sections: [
      {
        key: "eye_history", label: "History", rows: [],
        fields: [
          { key: "family_history_blindness", label: "Family history of blindness",
            kind: "choice",
            choices: [{ value: "yes", label: "Yes" }, { value: "no", label: "No" },
                      { value: "unknown", label: "Unknown" }] },
          { key: "eye_drops", label: "Currently using eye drops?", kind: "choice",
            choices: [{ value: "yes", label: "Yes" }, { value: "no", label: "No" },
                      { value: "unknown", label: "Unknown" }] },
          { key: "eye_drops_details", label: "Eye drops / medication details",
            kind: "short", max_length: 60,
            revealed_by: { field: "eye_drops", values: ["yes"] } },
          { key: "eye_drops_frequency", label: "Frequency", kind: "choice",
            choices: [{ value: "once_daily", label: "Once daily" },
                      { value: "twice_daily", label: "Twice daily" },
                      { value: "other", label: "Other" }],
            revealed_by: { field: "eye_drops", values: ["yes"] } },
        ],
      },
      {
        key: "visual_function", label: "Contrast & colour vision",
        rows: [
          { name: "contrast", label: "Contrast", right: "contrast_right",
            left: "contrast_left", kind: "short", max_length: 60 },
          { name: "colour_vision", label: "Colour", right: "colour_vision_right",
            left: "colour_vision_left", kind: "short", max_length: 60 },
        ],
        fields: [],
      },
    ],
  };

  /** The same form, served a schema shaped like the one these fields add. */
  function renderExam({ schema, value = {} } = {}) {
    vi.spyOn(api, "get").mockResolvedValue({ data: schema });
    return renderForm({ value });
  }

  it("asks the history questions with Yes / No / Unknown", async () => {
    renderExam({ schema: HISTORY_SCHEMA });

    const chooser = await screen.findByLabelText("Family history of blindness");
    expect([...chooser.options].map((o) => o.textContent))
      .toEqual(["Not recorded", "Yes", "No", "Unknown"]);
  });

  it("keeps the eye-drop details hidden until the answer is yes", async () => {
    renderExam({ schema: HISTORY_SCHEMA, value: { eye_drops: "no" } });

    expect(await screen.findByLabelText("Currently using eye drops?")).toBeInTheDocument();
    expect(screen.queryByLabelText("Eye drops / medication details"))
      .not.toBeInTheDocument();
    expect(screen.queryByLabelText("Frequency")).not.toBeInTheDocument();
  });

  it("reveals them once it is", async () => {
    renderExam({ schema: HISTORY_SCHEMA, value: { eye_drops: "yes" } });

    expect(await screen.findByLabelText("Eye drops / medication details"))
      .toBeInTheDocument();
    const frequency = screen.getByLabelText("Frequency");
    expect([...frequency.options].map((o) => o.textContent))
      .toEqual(["Not recorded", "Once daily", "Twice daily", "Other"]);
  });

  it("never hides a box that already has something in it", async () => {
    // The answer was changed to "no" after the detail was typed. Hiding it
    // would lose what somebody recorded — the server keeps it either way.
    renderExam({ schema: HISTORY_SCHEMA,
                 value: { eye_drops: "no", eye_drops_details: "Timolol 0.5%" } });

    expect(await screen.findByDisplayValue("Timolol 0.5%")).toBeInTheDocument();
  });

  it("asks contrast and colour vision of each eye", async () => {
    renderExam({ schema: HISTORY_SCHEMA });

    for (const label of ["Contrast — right eye", "Contrast — left eye",
                         "Colour — right eye", "Colour — left eye"]) {
      expect(await screen.findByLabelText(label)).toBeInTheDocument();
    }
  });
});

// **The layout**, which is all that changed: the same fields, grouped and
// collapsible, with the eyes named once instead of above every input.
//
// Nothing here tests which fields exist — that is the server's to say and
// `test_eye_examination.py` holds it.
describe("the examination reads as a clinical form", () => {
  it("groups the sections under the headings a clinician scans by", async () => {
    renderForm();

    // The fixture carries three of the catalogue's sections; each lands under
    // the group the map puts it in — "Presenting complaint" under History.
    for (const heading of ["History", "Refraction", "Pupils"]) {
      expect(await screen.findByRole("button", { name: new RegExp(heading, "i") }))
        .toBeInTheDocument();
    }
  });

  it("opens History and Visual function, and leaves the examination closed", async () => {
    renderForm();

    const history = await screen.findByRole("button", { name: /History/i });
    expect(history).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /Refraction/i }))
      .toHaveAttribute("aria-expanded", "false");
  });

  it("opens and closes a group without losing what is in it", async () => {
    const user = userEvent.setup();
    renderForm({ value: { sphere_right: "+1.25" } });

    // A group holding a value opens itself — a finding behind a caret is a
    // finding that gets missed.
    const refraction = await screen.findByRole("button", { name: /Refraction/i });
    expect(refraction).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Sphere — right eye")).toHaveValue("+1.25");

    await user.click(refraction);
    expect(refraction).toHaveAttribute("aria-expanded", "false");
    await user.click(refraction);
    // Still there, and still what it was.
    expect(await screen.findByLabelText("Sphere — right eye")).toHaveValue("+1.25");
  });

  it("says how much a group holds, so a closed one is not a blank", async () => {
    renderForm({ value: { sphere_right: "+1.25", sphere_left: "-0.50" } });

    const refraction = await screen.findByRole("button", { name: /Refraction/i });
    // The count is three text nodes in a badge, so read the header's own text.
    expect(refraction.textContent).toMatch(/2 of \d+ recorded/);
  });

  it("names the eyes once per section rather than above every input", async () => {
    const user = userEvent.setup();
    renderForm();
    await openGroup(user, "Pupils");

    // One column header pair for the section…
    const headers = screen.getAllByText("Right eye", { selector: "span" });
    expect(headers.length).toBeLessThanOrEqual(2);  // desktop header + mobile label
    // …and every input still carries its own accessible name.
    expect(screen.getByLabelText("Pupil — right eye")).toBeInTheDocument();
    expect(screen.getByLabelText("Pupil — left eye")).toBeInTheDocument();
  });

  it("keeps right and left side by side on a wide screen and stacked on a phone", async () => {
    const user = userEvent.setup();
    renderForm();
    await openGroup(user, "Refraction");

    const row = (await screen.findByLabelText("Sphere — right eye"))
      .closest("[class*='sm:grid-cols-']");
    expect(row).not.toBeNull();
    // Three columns from `sm` up — the finding, then an eye each — and a
    // single stacked column below it.
    expect(row.className).toMatch(/sm:grid-cols-\[9rem_1fr_1fr\]/);
    expect(row.className).toMatch(/\bgrid\b/);
  });

  it("opens a group the server complained about, whatever its default", async () => {
    renderForm({ errors: { sphere_right: ["Too long."] } });

    const refraction = await screen.findByRole("button", { name: /Refraction/i });
    expect(refraction).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("Too long.")).toBeInTheDocument();
  });

  it("still renders a section the grouping has never heard of", async () => {
    // A catalogue can gain a section without this file changing; it must not
    // make findings disappear from the form.
    vi.spyOn(api, "get").mockResolvedValue({ data: {
      eyes: FIELDS.eyes,
      sections: [...FIELDS.sections, {
        key: "brand_new", label: "Something new", rows: [],
        fields: [{ key: "newly_added", label: "Newly added", kind: "short" }],
      }],
    } });
    renderForm();

    expect(await screen.findByRole("button", { name: /Something new/i }))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Newly added")).toBeInTheDocument();
  });

  it("reads a locked examination as text, group by group", async () => {
    renderForm({ value: { sphere_right: "+1.25" }, disabled: true });

    // The group opens because it holds something, and the value is content
    // rather than a greyed-out box.
    expect(await screen.findByLabelText("Sphere — right eye"))
      .toHaveTextContent("+1.25");
  });
});
