import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { Badge, Field, Input, Select, Textarea } from "./ui.jsx";
import { Icon } from "./icons.jsx";

// The eye examination on a consultation note — part of the note, not a
// record of its own, so it locks and is amended with it.
//
// The fields are not listed here. They come from the server
// (`clinical/eye_exam.py`, served at /notes/eye-examination-fields/), which is
// also what validates them, so this form can never offer a field the server
// refuses or miss one it accepts. Nothing is required: the eye doctor fills in
// what they examined, and a blank is dropped rather than stored.

export function useEyeExaminationFields() {
  return useQuery({
    queryKey: ["eye-examination-fields"],
    queryFn: () => api.get("/notes/eye-examination-fields/").then((r) => r.data),
    staleTime: Infinity,
  });
}

const filled = (value) =>
  Array.isArray(value)
    ? value.length > 0
    : value !== undefined && value !== null && String(value).trim() !== "";

const messageOf = (error) => (Array.isArray(error) ? error.join(" ") : error);

// A ticked list, read back through the catalogue's own labels so a locked
// note shows what was recorded rather than the stored values.
function labelsFor(spec, value) {
  const chosen = Array.isArray(value) ? value : [];
  return chosen.map((item) => spec.choices?.find((c) => c.value === item)?.label ?? item);
}

/**
 * Multiple findings at once — the presenting complaint, the ocular history.
 * Chips rather than a column of checkboxes: fourteen complaints in a list is
 * a page of scrolling on the phone this is filled in on.
 */
function MultiChoice({ spec, label, value, onChange, disabled }) {
  const chosen = Array.isArray(value) ? value : [];
  if (disabled) {
    return (
      <p className="text-slate-800">
        {chosen.length ? labelsFor(spec, chosen).join(" · ") : "—"}
      </p>
    );
  }
  return (
    <div role="group" aria-label={label} className="flex min-w-0 flex-wrap gap-2">
      {(spec.choices ?? []).map((choice) => {
        const picked = chosen.includes(choice.value);
        return (
          <button
            key={choice.value}
            type="button"
            aria-pressed={picked}
            onClick={() =>
              onChange(picked
                ? chosen.filter((item) => item !== choice.value)
                : [...chosen, choice.value])}
            className={`min-h-[36px] rounded-full border px-3 py-1.5 text-sm font-medium transition ${
              picked
                ? "border-brand-500 bg-brand-600 text-white"
                : "border-slate-300 bg-white text-slate-700 hover:border-brand-400 hover:bg-brand-50"}`}
          >
            {picked ? "✓ " : ""}{choice.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * **How the server's sections are grouped on screen.**
 *
 * Presentation only. The fields, their kinds, their options and which section
 * each belongs to are all the server's (`clinical/eye_exam.py`, rule 43);
 * this says which of those sections sit under one heading, and nothing else.
 * There is no field list here.
 *
 * A server section this map does not claim still renders — as its own group,
 * at the end — so adding a section to the catalogue can never make findings
 * disappear from the form. `test_an_unknown_section_still_renders` holds it.
 */
const GROUPS = [
  { key: "history", label: "History",
    hint: "What they came in with, and what they have been treated for.",
    sections: ["complaint", "ocular_history", "eye_history"], open: true },
  { key: "visual_function", label: "Visual function",
    hint: "Acuity, contrast and colour, each eye.",
    sections: ["vision", "visual_function"], open: true },
  { key: "refraction", label: "Refraction", sections: ["refraction"], open: false },
  { key: "pressure", label: "Eye pressure", sections: ["pressure"], open: false },
  { key: "pupils", label: "Pupils", sections: ["pupils"], open: false },
  { key: "anterior", label: "Anterior segment", sections: ["anterior"], open: false },
  { key: "posterior", label: "Posterior segment", sections: ["posterior"], open: false },
  { key: "functional", label: "Functional examination",
    hint: "Movements and fields.", sections: ["other"], open: true },
];


/** Every key a group holds a value for, so a collapsed card can still count. */
function groupKeys(group, schema) {
  const keys = [];
  for (const section of schema.sections) {
    if (!group.sections.includes(section.key)) continue;
    for (const row of section.rows) {
      for (const eye of schema.eyes) keys.push(row[eye.key]);
    }
    for (const field of section.fields) keys.push(field.key);
  }
  return keys;
}


function Control({ spec, label, value, onChange, disabled }) {
  const common = {
    value: value ?? "",
    disabled,
    "aria-label": label,
    onChange: (event) => onChange(event.target.value),
  };
  if (spec.kind === "multi") {
    return <MultiChoice spec={spec} label={label} value={value} onChange={onChange} disabled={disabled} />;
  }
  // **A saved finding is read as text, never as a greyed-out box.** A page of
  // disabled inputs reads as a form somebody is meant to fill in — it invites
  // clicking to find out why it will not type, and on a record that has been
  // signed off that is exactly the wrong invitation. The value is the content
  // here, so it is rendered as content.
  if (disabled) {
    const labels = new Map((spec.choices ?? []).map((c) => [c.value, c.label]));
    const shown = labels.get(value) ?? value;
    return (
      <p aria-label={label} className="whitespace-pre-wrap py-1 text-slate-800">
        {shown === undefined || shown === null || String(shown).trim() === ""
          ? <span className="text-slate-500">Not recorded</span>
          : `${shown}${spec.unit ? ` ${spec.unit}` : ""}`}
      </p>
    );
  }
  if (spec.kind === "choice") {
    return (
      <Select {...common}>
        <option value="">Not recorded</option>
        {spec.choices.map((choice) => (
          <option key={choice.value} value={choice.value}>{choice.label}</option>
        ))}
      </Select>
    );
  }
  if (spec.kind === "number") {
    return <Input type="number" inputMode="decimal" step="0.1" min={spec.min} max={spec.max} {...common} />;
  }
  if (spec.kind === "text") return <Textarea rows={2} maxLength={spec.max_length} {...common} />;
  return <Input maxLength={spec.max_length} {...common} />;
}

/**
 * `value` is the examination object; `errors` is what the server said about
 * it — per field, or one message for the whole examination.
 */
export default function EyeExaminationFields({ value, onChange, disabled = false, errors }) {
  const { data: schema, isLoading, isError } = useEyeExaminationFields();
  const exam = value ?? {};
  const set = (key, next) => onChange({ ...exam, [key]: next });
  const fieldErrors = errors && !Array.isArray(errors) && typeof errors === "object" ? errors : {};
  const overall = errors && (Array.isArray(errors) || typeof errors === "string") ? messageOf(errors) : null;

  if (isLoading) return <p className="text-sm text-slate-600">Loading the eye examination…</p>;
  if (isError || !schema) {
    return <p className="text-sm text-red-700">Could not load the eye examination fields.</p>;
  }

  // A locked note shows what was examined, not a page of empty boxes.
  function revealed(field, values) {
    const rule = field.revealed_by;
    if (!rule) return true;
    // Already answered? Then it stays on screen whatever the answer says —
    // hiding a box with something in it loses what somebody recorded.
    if (filled(values[field.key])) return true;
    return (rule.values ?? []).includes(values[rule.field]);
  }

  const sections = schema.sections
    .map((section) => ({
      ...section,
      rows: disabled
        ? section.rows.filter((row) => schema.eyes.some((eye) => filled(exam[row[eye.key]])))
        : section.rows,
      fields: (disabled
        ? section.fields.filter((field) => filled(exam[field.key]))
        // A field the catalogue says is revealed by another — "how often?"
        // is only a question once "are you using drops?" has been answered
        // yes. The rule is **served on the field** (`revealed_by`), so this
        // renderer holds no copy of which field reveals which, the same way
        // it holds no copy of the field list (rule 43).
        //
        // It is a display rule and not a validation one: the server stores
        // whatever it is given, so a detail typed before the answer changed
        // is kept rather than thrown away.
        : section.fields.filter((field) => revealed(field, exam))),
    }))
    .filter((section) => section.rows.length || section.fields.length);

  // The server's sections, arranged under the groups a clinician scans by.
  // Anything the map does not claim becomes its own group rather than
  // vanishing — a catalogue can gain a section without this file changing.
  const byKey = new Map(sections.map((section) => [section.key, section]));
  const claimed = new Set(GROUPS.flatMap((group) => group.sections));
  const groups = [
    ...GROUPS.map((group) => ({
      ...group,
      parts: group.sections.map((key) => byKey.get(key)).filter(Boolean),
    })),
    ...sections.filter((section) => !claimed.has(section.key)).map((section) => ({
      key: section.key, label: section.label, sections: [section.key],
      open: true, parts: [section],
    })),
  ].filter((group) => group.parts.length);

  return (
    <section aria-label="Eye examination" className="min-w-0 space-y-3 border-t pt-4">
      <div>
        <h3 className="font-semibold text-slate-800">Eye examination</h3>
        {!disabled && (
          <p className="text-sm text-slate-600">
            Fill in what you examined — nothing here is required. History, diagnosis and plan
            go in the note above.
          </p>
        )}
      </div>
      {overall && <p className="text-sm text-red-700">{overall}</p>}

      {groups.map((group) => (
        <ExaminationGroup
          key={group.key}
          group={group}
          schema={schema}
          exam={exam}
          fieldErrors={fieldErrors}
          disabled={disabled}
          set={set}
        />
      ))}
    </section>
  );
}

/**
 * One group of findings, as a card that opens and closes.
 *
 * **Collapsed never means hidden.** The header carries how much of the group
 * has been filled in, so a closed card still says there is something in it —
 * and a group with a value in it opens itself, because a finding somebody
 * has to go looking for is a finding that gets missed.
 */
function ExaminationGroup({ group, schema, exam, fieldErrors, disabled, set }) {
  const keys = groupKeys(group, schema);
  const recorded = keys.filter((key) => filled(exam[key])).length;
  const hasError = keys.some((key) => fieldErrors[key]);
  // A group that holds something, or has an error in it, opens regardless of
  // its default: a complaint the server rejected must not be behind a caret.
  const [open, setOpen] = useState(group.open || recorded > 0 || hasError);
  const bodyId = `eye-group-${group.key}`;

  return (
    <div className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((was) => !was)}
        className="flex min-h-[44px] w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-slate-50 sm:min-h-[38px]"
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold uppercase tracking-wide text-slate-700">
            {group.label}
          </span>
          {group.hint && !disabled && (
            <span className="mt-0.5 block text-xs text-slate-600">{group.hint}</span>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {recorded > 0 && (
            <Badge tone={hasError ? "danger" : "brand"}>
              {recorded} of {keys.length} recorded
            </Badge>
          )}
          <Icon name="chevronDown" aria-hidden="true"
                className={`h-4 w-4 text-slate-600 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>

      {open && (
        <div id={bodyId} className="space-y-4 border-t border-slate-100 px-4 py-4">
          {group.parts.map((section) => (
            <ExaminationSection
              key={section.key}
              section={section}
              showHeading={group.parts.length > 1}
              schema={schema}
              exam={exam}
              fieldErrors={fieldErrors}
              disabled={disabled}
              set={set}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** One of the server's sections inside a group. */
function ExaminationSection({ section, showHeading, schema, exam, fieldErrors,
                              disabled, set }) {
  return (
    <fieldset className="min-w-0 space-y-2">
      {showHeading && (
        <legend className="text-xs font-medium uppercase tracking-wide text-slate-500">
          {section.label}
        </legend>
      )}

      {section.rows.length > 0 && (
        <div className="min-w-0">
          {/* **Named once, not once per row.** The eye labels were repeated
              above every single input, which is what made the examination
              read as an endless run of "Right eye / Left eye" blocks. Each
              input keeps its own accessible name from `Control`'s
              `aria-label`, so nothing was lost to a screen reader. */}
          <div className="hidden gap-2 border-b border-slate-100 pb-1 sm:grid sm:grid-cols-[9rem_1fr_1fr]">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Examination
            </span>
            {schema.eyes.map((eye) => (
              <span key={eye.key}
                    className="text-xs font-medium uppercase tracking-wide text-slate-500">
                {eye.label}
              </span>
            ))}
          </div>

          <div className="divide-y divide-slate-100">
            {section.rows.map((row) => (
              <div key={row.name}
                   className="grid min-w-0 gap-2 py-2 sm:grid-cols-[9rem_1fr_1fr] sm:items-start">
                <p className="text-sm font-medium text-slate-800 sm:pt-2">
                  {row.label}
                  {row.unit && !row.label.includes(row.unit) ? ` (${row.unit})` : ""}
                </p>
                {schema.eyes.map((eye) => (
                  <div key={eye.key} className="min-w-0">
                    {/* The column header is hidden on a phone, where the
                        grid stacks — so each input names its own eye there
                        rather than leaving two boxes with nothing to tell
                        them apart. */}
                    <span className="mb-1 block text-xs font-medium text-slate-600 sm:hidden">
                      {eye.label}
                    </span>
                    <Control
                      spec={row}
                      label={`${row.label} — ${eye.label.toLowerCase()}`}
                      value={exam[row[eye.key]]}
                      disabled={disabled}
                      onChange={(next) => set(row[eye.key], next)}
                    />
                    {messageOf(fieldErrors[row[eye.key]]) && (
                      <p className="mt-1 text-sm text-red-700">
                        {messageOf(fieldErrors[row[eye.key]])}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {section.fields.length > 0 && (
        // Two columns on a wide screen, one on a phone. A tick-list or a
        // paragraph takes the full width: squeezing either into half a column
        // is how a compact form becomes a cramped one.
        <div className="grid min-w-0 gap-3 sm:grid-cols-2">
          {section.fields.map((field) => (
            <Field
              key={field.key}
              label={field.label}
              error={messageOf(fieldErrors[field.key])}
              className={field.kind === "multi" || field.kind === "text"
                ? "sm:col-span-2" : ""}
            >
              <Control
                spec={field}
                label={field.label}
                value={exam[field.key]}
                disabled={disabled}
                onChange={(next) => set(field.key, next)}
              />
            </Field>
          ))}
        </div>
      )}
    </fieldset>
  );
}
