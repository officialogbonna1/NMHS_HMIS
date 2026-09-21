import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { Field, Input, Select, Textarea } from "./ui.jsx";

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
  const sections = schema.sections
    .map((section) => ({
      ...section,
      rows: disabled
        ? section.rows.filter((row) => schema.eyes.some((eye) => filled(exam[row[eye.key]])))
        : section.rows,
      fields: disabled ? section.fields.filter((field) => filled(exam[field.key])) : section.fields,
    }))
    .filter((section) => section.rows.length || section.fields.length);

  return (
    <section aria-label="Eye examination" className="min-w-0 space-y-4 border-t pt-4">
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

      {sections.map((section) => (
        // A card per section: the eye examination is nine groups of findings
        // and a flat run of boxes gives the clinician nothing to navigate by.
        <fieldset
          key={section.key}
          className="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
        >
          <legend className="px-1 text-sm font-semibold uppercase tracking-wide text-slate-700">
            {section.label}
          </legend>

          {section.rows.map((row) => (
            <div key={row.name} className="grid min-w-0 gap-2 sm:grid-cols-[9rem_1fr_1fr] sm:items-start">
              <p className="text-sm font-medium text-slate-800 sm:pt-2">
                {row.label}
                {row.unit && !row.label.includes(row.unit) ? ` (${row.unit})` : ""}
              </p>
              {schema.eyes.map((eye) => (
                <Field key={eye.key} label={eye.label} error={messageOf(fieldErrors[row[eye.key]])}>
                  <Control
                    spec={row}
                    label={`${row.label} — ${eye.label.toLowerCase()}`}
                    value={exam[row[eye.key]]}
                    disabled={disabled}
                    onChange={(next) => set(row[eye.key], next)}
                  />
                </Field>
              ))}
            </div>
          ))}

          {section.fields.map((field) => (
            <Field key={field.key} label={field.label} error={messageOf(fieldErrors[field.key])}>
              <Control
                spec={field}
                label={field.label}
                value={exam[field.key]}
                disabled={disabled}
                onChange={(next) => set(field.key, next)}
              />
            </Field>
          ))}
        </fieldset>
      ))}
    </section>
  );
}
