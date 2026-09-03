import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";

// The one vitals form, used by the nursing station and the patient chart's
// Vitals tab. Grouped the way a reading is actually taken, with units and
// plausible ranges on each field so a slipped decimal is caught at entry —
// vitals lock on save, so there is no fixing them afterwards.
const GROUPS = [
  {
    title: "Observations",
    fields: [
      { name: "temperature_c", label: "Temperature", unit: "°C", step: "0.1", min: 25, max: 45 },
      { name: "heart_rate", label: "Heart rate", unit: "bpm", min: 20, max: 250 },
      { name: "respiratory_rate", label: "Respiratory rate", unit: "/min", min: 4, max: 80 },
      { name: "sao2", label: "SaO₂", unit: "%", min: 50, max: 100 },
    ],
  },
  {
    title: "Blood pressure",
    fields: [
      { name: "bp_systolic", label: "Systolic", unit: "mmHg", min: 40, max: 300 },
      { name: "bp_diastolic", label: "Diastolic", unit: "mmHg", min: 20, max: 200 },
    ],
    selects: [
      { name: "bp_position", label: "Position", options: ["Sitting", "Standing", "Lying"] },
      { name: "bp_extremity", label: "Extremity", options: ["Left arm", "Right arm", "Left leg", "Right leg"] },
    ],
  },
  {
    title: "Measurements",
    fields: [
      { name: "height_cm", label: "Height", unit: "cm", step: "0.1", min: 30, max: 250 },
      { name: "weight_kg", label: "Weight", unit: "kg", step: "0.1", min: 1, max: 400 },
    ],
  },
  {
    title: "Blood glucose",
    fields: [{ name: "glucose_level", label: "Glucose", unit: "mmol/L", step: "0.1", min: 0, max: 60 }],
    selects: [{ name: "glucose_time_of_day", label: "Taken", options: ["Before meal", "After meal", "Bedtime", "Random"] }],
    toggles: [{ name: "glucose_fasting", label: "Fasting" }],
  },
];

const NUMERIC_FIELDS = GROUPS.flatMap((g) => g.fields);

function blankForm() {
  return {
    visit_time: localDateTimeValue(new Date()),
    ...Object.fromEntries(NUMERIC_FIELDS.map((f) => [f.name, ""])),
    bp_position: "", bp_extremity: "", glucose_time_of_day: "", glucose_fasting: null,
  };
}

export default function VitalsEntryForm({ patientId, onSaved, onCancel }) {
  const [form, setForm] = useState(blankForm);
  const [error, setError] = useState(null);

  const set = (field, value) => setForm((f) => ({ ...f, [field]: value }));

  const outOfRange = NUMERIC_FIELDS.filter((f) => {
    const value = form[f.name];
    if (value === "" || value == null) return false;
    const n = Number(value);
    return Number.isNaN(n) || n < f.min || n > f.max;
  });

  // A reading with nothing in it is not a reading.
  const hasAnyValue = NUMERIC_FIELDS.some((f) => form[f.name] !== "");
  const bpHalfFilled = (form.bp_systolic === "") !== (form.bp_diastolic === "");

  const save = useMutation({
    mutationFn: () => api.post("/vitals/", { patient: patientId, ...emptyToNull(form) }),
    onSuccess: (response) => {
      setForm(blankForm());
      setError(null);
      onSaved?.(response.data);
    },
    onError: (err) => setError(readError(err, "Could not save these vitals.")),
  });

  const blocked = !hasAnyValue || outOfRange.length > 0 || bpHalfFilled;

  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!blocked) save.mutate(); }} className="space-y-5">
      <div className="max-w-xs">
        <label className="block text-sm font-medium text-slate-700 mb-1">Time taken</label>
        <input
          type="datetime-local"
          value={form.visit_time}
          onChange={(e) => set("visit_time", e.target.value)}
          className="w-full rounded-md border border-slate-300 px-3 py-2"
        />
      </div>

      {GROUPS.map((group) => (
        <fieldset key={group.title} className="rounded-lg border p-4">
          <legend className="px-2 text-sm font-semibold uppercase tracking-wide text-slate-700">{group.title}</legend>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {group.fields.map((field) => (
              <NumberField
                key={field.name}
                field={field}
                value={form[field.name]}
                onChange={(v) => set(field.name, v)}
              />
            ))}
            {(group.selects ?? []).map((select) => (
              <div key={select.name}>
                <label className="block text-sm font-medium text-slate-700 mb-1">{select.label}</label>
                <select
                  value={form[select.name]}
                  onChange={(e) => set(select.name, e.target.value)}
                  className="w-full rounded-md border border-slate-300 px-3 py-2"
                >
                  <option value="">—</option>
                  {select.options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </div>
            ))}
            {(group.toggles ?? []).map((toggle) => (
              <div key={toggle.name}>
                <label className="block text-sm font-medium text-slate-700 mb-1">{toggle.label}</label>
                <div className="flex rounded-md border overflow-hidden text-sm">
                  {[["Yes", true], ["No", false]].map(([label, val]) => (
                    <button
                      key={label}
                      type="button"
                      onClick={() => set(toggle.name, form[toggle.name] === val ? null : val)}
                      className={`flex-1 px-3 py-2 ${form[toggle.name] === val ? "bg-brand-600 text-white" : "hover:bg-gray-50"}`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </fieldset>
      ))}

      {outOfRange.length > 0 && (
        <p className="text-sm text-amber-700">
          Check {outOfRange.map((f) => f.label.toLowerCase()).join(", ")} — outside the range this form accepts.
        </p>
      )}
      {bpHalfFilled && <p className="text-sm text-amber-700">Enter both systolic and diastolic, or neither.</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={blocked || save.isPending}
          className="bg-brand-600 text-white px-5 py-2.5 rounded-md hover:bg-brand-700 disabled:opacity-50"
        >
          {save.isPending ? "Saving…" : "Save vitals"}
        </button>
        {onCancel && <button type="button" onClick={onCancel} className="px-4 py-2 rounded-md border">Cancel</button>}
        {!hasAnyValue && <span className="text-sm text-slate-600">Fill in at least one reading.</span>}
      </div>
    </form>
  );
}

function NumberField({ field, value, onChange }) {
  const n = Number(value);
  const bad = value !== "" && (Number.isNaN(n) || n < field.min || n > field.max);
  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1">
        {field.label} <span className="font-normal text-slate-500">({field.unit})</span>
      </label>
      <input
        type="number"
        inputMode="decimal"
        step={field.step ?? "1"}
        min={field.min}
        max={field.max}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full rounded-md border px-3 py-2 ${bad ? "border-amber-500 bg-amber-50" : "border-slate-300"}`}
      />
    </div>
  );
}

// A number field left alone means "not measured", and the backend wants null
// for that — an empty string is a 400. The text fields next to them are the
// opposite: they are blank-but-not-null on the model, so nulling them was
// rejecting every reading where the nurse had not picked a BP position or a
// glucose time, which is most of them.
const TEXT_FIELDS = ["bp_position", "bp_extremity", "glucose_time_of_day"];

function emptyToNull(form) {
  return Object.fromEntries(
    Object.entries(form).map(([k, v]) => [k, v === "" && !TEXT_FIELDS.includes(k) ? null : v])
  );
}

function localDateTimeValue(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
