import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { TextLink } from "./ui.jsx";
import { readError } from "../api/errors";

// One modal implementation driven by tileConfig.js — same panel shape as
// the manual's Allergies screen, generic enough to cover all nine tiles.
//
// Pass `item` to edit an existing row (PATCH); omit it to add a new one
// (POST). All fields are visible from the start, required fields block Save
// with a reason, and a failed request is shown rather than swallowed.
export default function GenericTileModal({ patientId, config, item, onClose }) {
  const isEdit = Boolean(item);
  const queryClient = useQueryClient();
  const [error, setError] = useState(null);
  const [touched, setTouched] = useState(false);

  const [name, setName] = useState(() => item?.[config.nameField] ?? "");
  const [values, setValues] = useState(() => initialValues(config, item));

  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);

  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const missing = [
    !name.trim() && (config.nameLabel ?? config.title),
    ...config.fields.filter((f) => f.required && isEmpty(values[f.name])).map((f) => f.label),
  ].filter(Boolean);

  // Some tiles need a rule no single field can express — a test result must
  // carry a document or a typed finding, either one. The server enforces the
  // same rule; this just says so before the round trip.
  const issue = missing.length === 0 ? config.validate?.(values) : null;

  const save = useMutation({
    mutationFn: () => {
      const payload = { patient: patientId, [config.nameField]: name.trim(), ...cleaned(config, values) };
      if (config.key === "allergies") {
        const dangerous = config.fields.find((f) => f.name === "reactions")?.dangerousOptions ?? [];
        payload.is_dangerous = (values.reactions ?? []).some((r) => dangerous.includes(r));
      }
      // A tile carrying a document has to go up as multipart; JSON cannot
      // hold a File. Everything else keeps the plain JSON path.
      const body = hasFileField(config) ? asFormData(config, payload) : payload;
      return isEdit
        ? api.patch(`/${config.endpoint}/${item.id}/`, body)
        : api.post(`/${config.endpoint}/`, body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [config.endpoint, patientId] });
      queryClient.invalidateQueries({ queryKey: ["patient-overview", String(patientId)] });
      onClose();
    },
    onError: (err) => setError(readError(err)),
  });

  function submit(e) {
    e.preventDefault();
    setTouched(true);
    if (missing.length === 0 && !issue) save.mutate();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-0 sm:p-4" onMouseDown={onClose}>
      <form
        onSubmit={submit}
        onMouseDown={(e) => e.stopPropagation()}
        className="flex max-h-[92vh] w-full max-w-2xl flex-col rounded-t-2xl bg-white sm:rounded-2xl shadow-xl"
      >
        <header className="flex items-center justify-between rounded-t-2xl bg-gradient-to-r from-brand-700 to-brand-500 px-5 py-4 text-white">
          <div>
            <h2 className="font-semibold">{isEdit ? `Edit ${config.title}` : `Add ${config.title}`}</h2>
            <p className="text-xs text-white/70 mt-0.5">Fields marked * are required.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-full px-2 py-1 text-lg hover:bg-white/15">✕</button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto p-5">
          <div>
            <Label required>{config.nameLabel ?? `${config.title} name`}</Label>
            {config.nameOptions ? (
              <select
                className={inputClass(touched && !name.trim())}
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              >
                <option value="">Select one…</option>
                {config.nameOptions.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : (
              <>
                <input
                  className={inputClass(touched && !name.trim())}
                  placeholder={config.namePlaceholder ?? "Type here"}
                  value={name}
                  list={config.nameSuggestions ? `${config.key}-suggestions` : undefined}
                  onChange={(e) => setName(e.target.value)}
                  autoFocus
                />
                {config.nameSuggestions && (
                  <datalist id={`${config.key}-suggestions`}>
                    {config.nameSuggestions.map((o) => <option key={o} value={o} />)}
                  </datalist>
                )}
              </>
            )}
          </div>

          <div className="grid grid-cols-1 gap-x-4 gap-y-5 sm:grid-cols-2">
            {config.fields.map((field) => (
              <div key={field.name} className={field.width === "half" ? "sm:col-span-1" : "sm:col-span-2"}>
                <FieldInput
                  field={field.type === "file"
                    ? { ...field, currentUrl: item?.file_url, currentName: item?.file_name }
                    : field}
                  today={today}
                  value={values[field.name]}
                  invalid={touched && field.required && isEmpty(values[field.name])}
                  onChange={(v) => setValues((prev) => ({ ...prev, [field.name]: v }))}
                />
              </div>
            ))}
          </div>
        </div>

        <footer className="space-y-2 border-t p-4">
          {error && <p className="text-sm text-red-600">{error}</p>}
          {touched && missing.length > 0 && (
            <p className="text-sm text-amber-700">Still needed: {missing.join(", ")}.</p>
          )}
          {touched && issue && <p className="text-sm text-amber-700">{issue}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-full border px-4 py-2">Cancel</button>
            <button
              type="submit"
              disabled={save.isPending}
              className="rounded-full bg-brand-600 px-5 py-2 text-white hover:bg-brand-700 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : isEdit ? "Save changes" : "Save & Exit"}
            </button>
          </div>
        </footer>
      </form>
    </div>
  );
}

function FieldInput({ field, value, onChange, invalid, today }) {
  const label = <Label required={field.required}>{field.label}</Label>;
  const help = field.help && <p className="mt-1 text-xs text-slate-600">{field.help}</p>;

  if (field.type === "chips") {
    const selected = value ?? [];
    return (
      <div>
        {label}
        <div className="flex flex-wrap gap-2">
          {field.options.map((opt) => {
            const on = selected.includes(opt);
            const danger = on && (field.dangerousOptions ?? []).includes(opt);
            return (
              <button
                key={opt}
                type="button"
                onClick={() => onChange(on ? selected.filter((o) => o !== opt) : [...selected, opt])}
                className={`rounded-full border px-3 py-1.5 text-sm transition ${
                  danger ? "border-red-400 bg-red-50 text-red-700"
                    : on ? "border-brand-500 bg-brand-50 text-brand-700"
                    : "border-gray-300 text-slate-700 hover:border-brand-300 hover:bg-brand-50/50"
                }`}
              >
                {on && "✓ "}{opt}
              </button>
            );
          })}
        </div>
        {help}
      </div>
    );
  }

  if (field.type === "toggle") {
    return (
      <div>
        {label}
        <div className="flex w-full overflow-hidden rounded-md border text-sm">
          {[["Yes", true], ["No", false]].map(([text, val]) => (
            <button
              key={text}
              type="button"
              onClick={() => onChange(val)}
              className={`flex-1 px-3 py-2 ${value === val ? "bg-brand-600 text-white" : "hover:bg-gray-50"}`}
            >
              {text}
            </button>
          ))}
        </div>
        {help}
      </div>
    );
  }

  if (field.type === "select") {
    // Options are plain strings where the stored value is the label itself,
    // or {value, label} where the column holds a code (a test's "xray"
    // against the "X-ray" a person picks).
    const options = field.options.map((o) => (typeof o === "string" ? { value: o, label: o } : o));
    return (
      <div>
        {label}
        <select className={inputClass(invalid)} value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {help}
      </div>
    );
  }

  if (field.type === "file") {
    // `value` is a freshly picked File, or the string URL of one already on
    // the record. Editing without touching the picker must leave the stored
    // document alone, which is why an untouched field sends nothing at all.
    const picked = value instanceof File ? value : null;
    return (
      <div>
        {label}
        <input
          type="file"
          accept={field.accept}
          onChange={(e) => onChange(e.target.files?.[0] ?? "")}
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm file:mr-3 file:rounded-full file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
        />
        {picked && <p className="mt-1 text-sm text-slate-600">Selected: {picked.name}</p>}
        {!picked && field.currentUrl && (
          <p className="mt-1 text-sm text-slate-600">
            On file:{" "}
            <TextLink href={field.currentUrl} target="_blank" rel="noreferrer">
              {field.currentName || "open document"}
            </TextLink>
            {" — choose a file to replace it."}
          </p>
        )}
        {help}
      </div>
    );
  }

  if (field.type === "textarea") {
    return (
      <div>
        {label}
        <textarea
          rows={3}
          className={inputClass(invalid)}
          placeholder={field.placeholder}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
        {help}
      </div>
    );
  }

  return (
    <div>
      {label}
      <input
        type={field.type}
        className={inputClass(invalid)}
        placeholder={field.placeholder}
        min={field.min}
        max={field.max === "today" ? today : field.max}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
      />
      {help}
    </div>
  );
}

function Label({ children, required }) {
  return (
    <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
      {children}{required && <span className="text-red-500"> *</span>}
    </label>
  );
}

function inputClass(invalid) {
  return `w-full rounded-md border px-3 py-2 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100 ${
    invalid ? "border-red-400 bg-red-50" : "border-gray-300"
  }`;
}

function initialValues(config, item) {
  return Object.fromEntries(
    config.fields.map((f) => {
      const existing = item?.[f.name];
      if (existing !== undefined && existing !== null) return [f.name, existing];
      return [f.name, f.type === "chips" ? [] : f.type === "toggle" ? false : ""];
    })
  );
}

// Blank optional fields are sent as null (dates/numbers reject "") except
// text columns, which the backend declares as blank-not-null. A file field
// left alone is dropped entirely rather than sent empty, so editing the
// findings on a result does not wipe the document attached to it.
function cleaned(config, values) {
  const out = {};
  for (const field of config.fields) {
    const v = values[field.name];
    if (field.type === "file") {
      if (v instanceof File) out[field.name] = v;
      continue;
    }
    if (v === "" && ["date", "number"].includes(field.type)) out[field.name] = null;
    else out[field.name] = v;
  }
  return out;
}

function hasFileField(config) {
  return config.fields.some((f) => f.type === "file");
}

// DRF reads multipart, but every value arrives as a string — so null has to
// be left out rather than sent as the text "null", and a list has to repeat
// its key.
function asFormData(config, payload) {
  const form = new FormData();
  for (const [key, value] of Object.entries(payload)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) value.forEach((v) => form.append(key, v));
    else form.append(key, value);
  }
  return form;
}

function isEmpty(v) {
  return v === "" || v === null || v === undefined || (Array.isArray(v) && v.length === 0);
}

