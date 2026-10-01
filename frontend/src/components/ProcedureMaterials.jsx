import { Button, Field, Input } from "./ui.jsx";
import { emptyRow, rowProblem } from "./procedureMaterials.js";

// The materials used during a procedure, as rows the staff add, edit and
// remove. Clinical documentation only — nothing here touches stock. The column
// list comes from the server's own definition (`schema.materials`, served by
// `/patient-routes/report-fields/`), so the form holds no copy of it.
//
// Each row says, under it, what does not add up; the server is what refuses.

export default function ProcedureMaterials({ schema, rows, onChange, readOnly = false, errors = {} }) {
  const textFields = schema?.text_fields ?? [];
  const quantities = schema?.quantities ?? [];
  const set = (index, key, value) => onChange(rows.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  const remove = (index) => onChange(rows.filter((_, i) => i !== index));
  const add = () => onChange([...rows, emptyRow()]);
  const byKey = (key) => textFields.find((f) => f.key === key);

  return (
    <section aria-label="Materials used" className="min-w-0 space-y-3">
      <div>
        <h3 className="font-medium text-slate-900">Materials used</h3>
        <p className="text-sm text-slate-600">
          What went into this procedure. {schema?.rule ?? "Received = Used + Remaining + Wastage"} when
          a received quantity is given. Documentation only — stock is not changed.
        </p>
      </div>

      {rows.length === 0 && (
        <p className="text-sm text-slate-700">No materials recorded.</p>
      )}

      {rows.map((row, index) => {
        const problem = rowProblem(row);
        const serverProblems = errors[String(index)];
        const label = `Material ${index + 1}`;
        return (
          <fieldset key={index} aria-label={label}
                    className="min-w-0 space-y-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
            <legend className="sr-only">{label}</legend>
            <div className="grid min-w-0 gap-3 sm:grid-cols-3">
              {["name", "category", "unit"].map((key) => (
                <Field key={key} label={byKey(key)?.label ?? key} required={key === "name"}>
                  <Input value={row[key] ?? ""} disabled={readOnly}
                         maxLength={byKey(key)?.max_length}
                         onChange={(e) => set(index, key, e.target.value)} />
                </Field>
              ))}
            </div>
            <div className="grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-4">
              {quantities.map((q) => (
                <Field key={q.key} label={q.label}>
                  <Input type="number" inputMode="decimal" min="0" step="0.01" disabled={readOnly}
                         value={row[q.key] ?? ""}
                         onChange={(e) => set(index, q.key, e.target.value)} />
                </Field>
              ))}
            </div>
            <Field label={byKey("notes")?.label ?? "Notes"}>
              <Input value={row.notes ?? ""} disabled={readOnly} maxLength={byKey("notes")?.max_length}
                     onChange={(e) => set(index, "notes", e.target.value)} />
            </Field>
            {(problem || serverProblems) && (
              <p role="alert" className="text-sm text-red-700">
                {problem ?? Object.values(serverProblems).flat().join(" ")}
              </p>
            )}
            {!readOnly && (
              <Button type="button" variant="linkDanger" size="xs" onClick={() => remove(index)}>
                Remove {row.name?.trim() || label.toLowerCase()}
              </Button>
            )}
          </fieldset>
        );
      })}

      {!readOnly && rows.length < (schema?.max_rows ?? 50) && (
        <Button type="button" variant="secondary" size="sm" onClick={add}>+ Add material</Button>
      )}
    </section>
  );
}
