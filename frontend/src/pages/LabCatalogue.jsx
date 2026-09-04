import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import { useToast } from "../components/Toaster.jsx";

// The laboratory catalogue, edited by the laboratory.
//
// Everything the result form shows is a row on this page: a test, its price,
// its specimen, and the parameters under it with their units, reference
// ranges and result types. Adding "Serum Magnesium" is done here, by the
// people who know what it is measured in — not by a developer, and not in a
// deployment.
//
// A test is never deleted, only retired: results already filed point at it,
// and last year's report has to keep saying what it said. Retiring is
// reversible from the same panel (see the restore endpoint).
//
// Layout: master–detail. On a wide screen the list sits beside the editor and
// stays put while the editor scrolls. On a phone they are two *views* rather
// than a long stack — picking a test swaps to the editor with a way back —
// because the editor used to open below a 60vh list, off the bottom of the
// screen, and nothing said it had happened.

const RESULT_TYPE_HELP = {
  numeric: "A figure, flagged against the reference range below.",
  text: "Free text — a description, a finding, an organism.",
  positive_negative: "Positive / Negative.",
  reactive_nonreactive: "Reactive / Non-reactive.",
  detected_notdetected: "Detected / Not detected.",
  normal_abnormal: "Normal / Abnormal.",
  select: "One of the options you list below.",
};

// A result type is worth telling apart at a glance on a long parameter list.
const RESULT_TYPE_TONE = {
  numeric: "bg-brand-50 text-brand-700 ring-brand-200",
  select: "bg-violet-50 text-violet-700 ring-violet-200",
  text: "bg-slate-100 text-slate-700 ring-slate-300",
};
const typeTone = (value) => RESULT_TYPE_TONE[value] ?? "bg-emerald-50 text-emerald-700 ring-emerald-200";

const blankTest = {
  code: "", name: "", category: "haematology", description: "", specimen_type: "",
  container: "", turnaround_hours: "", price: "0", is_active: true,
};

const naira = (value) => `₦${Number(value || 0).toLocaleString()}`;

// Category names carry a second, longer synonym after a slash — enough to
// wrap the filter strip onto four lines. The first half names it on its own;
// the full label stays on the chip's tooltip.
const shortCategory = (label) => (label ?? "").split(" / ")[0].trim() || label;

export default function LabCatalogue() {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [showRetired, setShowRetired] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [creating, setCreating] = useState(false);

  const { data: meta } = useQuery({
    queryKey: ["lab-meta"],
    queryFn: () => api.get("/lab-tests/categories/").then((r) => r.data),
    staleTime: 300000,
  });

  const { data: tests, isLoading } = useQuery({
    queryKey: ["lab-catalogue"],
    // `all=1` brings back retired tests too, so one can be brought back
    // rather than re-created under a second code.
    queryFn: () => api.get("/lab-tests/", { params: { all: 1, page_size: 400 } })
      .then((r) => r.data.results ?? r.data),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["lab-catalogue"] });
    queryClient.invalidateQueries({ queryKey: ["lab-tests"] });
    queryClient.invalidateQueries({ queryKey: ["lab-meta"] });
  };

  const all = tests ?? [];
  const retiredCount = all.filter((t) => !t.is_active).length;

  // Counted here rather than read off `/categories/`, which counts active
  // rows only: with "show retired" on, a server count would say 13 beside a
  // list of 14.
  const counts = useMemo(() => {
    const pool = all.filter((t) => t.is_active || showRetired);
    const byCategory = {};
    for (const test of pool) byCategory[test.category] = (byCategory[test.category] ?? 0) + 1;
    return { total: pool.length, byCategory };
  }, [all, showRetired]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return all
      .filter((t) => t.is_active || showRetired)
      .filter((t) => category === "all" || t.category === category)
      .filter((t) => !term || `${t.name} ${t.code} ${t.specimen_type}`.toLowerCase().includes(term));
  }, [all, category, search, showRetired]);

  // Grouped under category headings, but only when the list is actually
  // mixed — inside one category the heading would repeat on every row.
  const groups = useMemo(() => {
    if (category !== "all") return [{ key: category, label: null, rows: visible }];
    const order = (meta?.categories ?? []).map((c) => c.value);
    const byCategory = new Map();
    for (const test of visible) {
      if (!byCategory.has(test.category)) byCategory.set(test.category, []);
      byCategory.get(test.category).push(test);
    }
    return [...byCategory.entries()]
      .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
      .map(([key, rows]) => ({ key, label: rows[0]?.category_label ?? key, rows }));
  }, [visible, category, meta]);

  const selected = all.find((t) => t.id === selectedId) ?? null;
  const showingDetail = creating || Boolean(selected);

  const openTest = (id) => { setSelectedId(id); setCreating(false); };
  const backToList = () => { setSelectedId(null); setCreating(false); };

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
            Laboratory Catalogue
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-700">
            Every test the lab offers and the parameters under it. Change a unit or a reference
            range here and the bench's result form follows immediately.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <p className="hidden text-sm text-slate-600 sm:block">
            <span className="font-semibold text-slate-900">{all.filter((t) => t.is_active).length}</span> in use
            {retiredCount > 0 && <> · {retiredCount} retired</>}
          </p>
          <button
            type="button"
            onClick={() => { setCreating(true); setSelectedId(null); }}
            className="rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700 sm:px-5"
          >
            + New test
          </button>
        </div>
      </header>

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,23rem)_minmax(0,1fr)]">
        {/* On a phone the list steps aside once something is open. */}
        <section
          className={`${showingDetail ? "hidden lg:block" : "block"} lg:sticky lg:top-[88px]`}
        >
          <CatalogueList
            groups={groups}
            meta={meta}
            loading={isLoading}
            category={category}
            onCategory={setCategory}
            search={search}
            onSearch={setSearch}
            showRetired={showRetired}
            onShowRetired={setShowRetired}
            counts={counts}
            retiredCount={retiredCount}
            selectedId={selectedId}
            onOpen={openTest}
            total={visible.length}
          />
        </section>

        <div className={`${showingDetail ? "block" : "hidden lg:block"} min-w-0`}>
          {showingDetail && (
            <button
              type="button"
              onClick={backToList}
              className="mb-3 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-medium text-brand-700 transition hover:bg-brand-50 lg:hidden"
            >
              <span aria-hidden="true">←</span> All tests
            </button>
          )}

          {creating && (
            <TestEditor
              key="new"
              test={blankTest}
              meta={meta}
              onSaved={(saved) => { refresh(); setCreating(false); setSelectedId(saved.id); }}
              onCancel={backToList}
            />
          )}
          {!creating && selected && (
            <div className="space-y-5">
              <TestEditor key={selected.id} test={selected} meta={meta} onSaved={refresh} />
              <ParameterEditor test={selected} meta={meta} onChanged={refresh} />
            </div>
          )}
          {!showingDetail && (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center">
              <p className="text-3xl" aria-hidden="true">🧫</p>
              <p className="mt-2 font-medium text-slate-800">Pick a test to edit it</p>
              <p className="mt-1 text-sm text-slate-600">
                Or add a new one — the bench sees it on the result form straight away.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- the list */

function CatalogueList({
  groups, meta, loading, category, onCategory, search, onSearch,
  showRetired, onShowRetired, retiredCount, counts, selectedId, onOpen, total,
}) {
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="space-y-3 border-b border-slate-100 p-4">
        <div className="relative">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden="true">⌕</span>
          <input
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search by name, code or specimen…"
            aria-label="Search the catalogue"
            className="w-full rounded-lg border border-slate-300 bg-white py-2.5 pl-8 pr-9 text-base text-slate-900 outline-none transition placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-100 sm:py-2 sm:text-sm"
          />
          {search && (
            <button
              type="button" onClick={() => onSearch("")} aria-label="Clear search"
              className="absolute right-2 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
            >
              ×
            </button>
          )}
        </div>

        {/* One scrolling row on a phone rather than seven wrapped lines. */}
        <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 sm:flex-wrap sm:overflow-visible sm:pb-0">
          <CategoryChip
            label="All" count={counts.total}
            active={category === "all"} onClick={() => onCategory("all")}
          />
          {(meta?.categories ?? [])
            .filter((c) => counts.byCategory[c.value] > 0)
            .map((c) => (
              <CategoryChip
                key={c.value} label={shortCategory(c.label)} title={c.label}
                count={counts.byCategory[c.value]}
                active={category === c.value} onClick={() => onCategory(c.value)}
              />
            ))}
        </div>

        {retiredCount > 0 && (
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox" checked={showRetired}
              onChange={(e) => onShowRetired(e.target.checked)}
              className="h-4 w-4 rounded border-slate-400 text-brand-600 focus:ring-brand-500"
            />
            Show {retiredCount} retired {retiredCount === 1 ? "test" : "tests"}
          </label>
        )}
      </div>

      <div className="max-h-[26rem] overflow-y-auto lg:max-h-[calc(100vh-19rem)]">
        {loading && (
          <div className="space-y-3 p-4" aria-label="Loading the catalogue">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="animate-pulse space-y-2">
                <div className="h-4 w-2/3 rounded bg-slate-200" />
                <div className="h-3 w-1/3 rounded bg-slate-100" />
              </div>
            ))}
          </div>
        )}

        {!loading && groups.map((group) => (
          <div key={group.key}>
            {group.label && (
              <p className="sticky top-0 z-10 border-y border-slate-100 bg-slate-50/95 px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-600 backdrop-blur">
                {group.label}
              </p>
            )}
            {group.rows.map((test) => (
              <TestRow
                key={test.id} test={test}
                selected={selectedId === test.id}
                onClick={() => onOpen(test.id)}
              />
            ))}
          </div>
        ))}

        {!loading && total === 0 && (
          <div className="p-8 text-center">
            <p className="text-sm font-medium text-slate-800">Nothing matches that.</p>
            <p className="mt-1 text-sm text-slate-600">
              {search ? "Try a shorter search, or another category." : "Nothing in this category yet."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function TestRow({ test, selected, onClick }) {
  return (
    <button
      onClick={onClick}
      aria-current={selected ? "true" : undefined}
      className={`flex w-full items-center gap-3 border-b border-slate-100 px-4 py-3 text-left transition ${
        selected ? "bg-brand-50 ring-1 ring-inset ring-brand-200" : "hover:bg-slate-50"}`}
    >
      <div className="min-w-0 flex-1">
        <p className={`truncate font-medium ${
          test.is_active ? "text-slate-800" : "text-slate-500 line-through"}`}>
          {test.name}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-slate-600">
          <span>
            {test.parameter_count} parameter{test.parameter_count === 1 ? "" : "s"}
          </span>
          {test.specimen_type && (
            <>
              <span aria-hidden="true" className="text-slate-400">·</span>
              <span className="truncate">{test.specimen_type}</span>
            </>
          )}
        </p>
      </div>
      <div className="shrink-0 text-right">
        {Number(test.price) > 0 && (
          <p className="font-medium tabular-nums text-slate-800">{naira(test.price)}</p>
        )}
        {!test.is_active && (
          <span className="mt-0.5 inline-block rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
            Retired
          </span>
        )}
      </div>
    </button>
  );
}

function CategoryChip({ label, count, active, onClick, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`shrink-0 whitespace-nowrap rounded-full px-3 py-1.5 text-sm font-medium transition sm:py-1 ${
        active
          ? "bg-brand-600 text-white shadow-sm"
          : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
    >
      {label} <span className={active ? "text-brand-100" : "text-slate-600"}>{count}</span>
    </button>
  );
}

/* --------------------------------------------------------- the test editor */

function TestEditor({ test, meta, onSaved, onCancel }) {
  const { showToast } = useToast();
  const [form, setForm] = useState(test);
  const [error, setError] = useState(null);
  const [confirmingRetire, setConfirmingRetire] = useState(false);
  useEffect(() => { setForm(test); setConfirmingRetire(false); }, [test]);

  const isNew = !test.id;

  // What "Save changes" would actually send. Nothing to send is worth saying,
  // so the button can stop offering to do nothing.
  const dirty = useMemo(() => {
    if (isNew) return true;
    return ["name", "category", "code", "specimen_type", "container",
            "turnaround_hours", "price", "description"]
      .some((key) => String(form[key] ?? "") !== String(test[key] ?? ""));
  }, [form, test, isNew]);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        ...form,
        turnaround_hours: form.turnaround_hours === "" ? null : Number(form.turnaround_hours),
        // Slugified here so a name typed straight in still gets a usable code.
        code: (form.code || form.name).toLowerCase().replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "").slice(0, 40),
      };
      return isNew
        ? api.post("/lab-tests/", body).then((r) => r.data)
        : api.patch(`/lab-tests/${test.id}/`, body).then((r) => r.data);
    },
    onSuccess: (saved) => {
      setError(null);
      showToast({ title: isNew ? "Test added" : "Test saved" });
      onSaved?.(saved);
    },
    onError: (err) => setError(readError(err, "Could not save this test.")),
  });

  const retire = useMutation({
    mutationFn: () => api.delete(`/lab-tests/${test.id}/`),
    onSuccess: () => {
      setConfirmingRetire(false);
      showToast({ title: "Test retired", message: "Results already filed against it still read." });
      onSaved?.();
    },
    onError: (err) => setError(readError(err, "Could not retire this test.")),
  });

  // Its own endpoint, not a PATCH of the form: bringing a test back is one
  // decision, and sending the whole form for it both raced the state update
  // (`is_active` was still false in `form` when save fired) and risked
  // writing back whatever else was half-typed on screen.
  const restore = useMutation({
    mutationFn: () => api.post(`/lab-tests/${test.id}/restore/`).then((r) => r.data),
    onSuccess: (saved) => {
      setError(null);
      showToast({ title: "Test back in use", message: "It can be ordered again." });
      onSaved?.(saved);
    },
    onError: (err) => setError(readError(err, "Could not bring this test back.")),
  });

  const set = (patch) => setForm((c) => ({ ...c, ...patch }));
  const categoryLabel = (meta?.categories ?? []).find((c) => c.value === form.category)?.label;

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-slate-100 p-4 sm:px-6">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold text-slate-900">
            {isNew ? "New test" : form.name || "Untitled test"}
          </h2>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-slate-600">
            {categoryLabel && <span>{categoryLabel}</span>}
            {!isNew && (
              <>
                <span aria-hidden="true" className="text-slate-400">·</span>
                <span className="font-mono text-slate-700">{form.code}</span>
              </>
            )}
          </p>
        </div>
        {Number(form.price) > 0 && (
          <p className="text-lg font-semibold tabular-nums text-slate-900">{naira(form.price)}</p>
        )}
      </header>

      {!isNew && !form.is_active && (
        <p className="border-b border-amber-200 bg-amber-50 px-4 py-3 text-sm text-slate-800 sm:px-6">
          <span className="font-semibold">This test is retired.</span> It is off the doctors'
          picker and cannot go on a new order. Results already filed against it still read.
        </p>
      )}

      <div className="space-y-6 p-4 sm:p-6">
        <Section title="Identity" note="What it is called, and where it sits in the list.">
          <Field label="Test name" className="sm:col-span-2">
            <input value={form.name} onChange={(e) => set({ name: e.target.value })}
                   className={input} placeholder="e.g. Serum Magnesium" />
          </Field>
          <Field label="Category">
            <select value={form.category} onChange={(e) => set({ category: e.target.value })}
                    className={input}>
              {(meta?.categories ?? []).map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </Field>
          <Field label="Code" note="Used by panels and imports. Left alone, it follows the name.">
            <input value={form.code} onChange={(e) => set({ code: e.target.value })}
                   className={`${input} font-mono`} placeholder="serum-magnesium" />
          </Field>
        </Section>

        <Section title="Specimen & turnaround" note="What the bench needs, and how long it takes.">
          <Field label="Specimen type">
            <input value={form.specimen_type} onChange={(e) => set({ specimen_type: e.target.value })}
                   className={input} placeholder="e.g. Serum" />
          </Field>
          <Field label="Container / bottle">
            <input value={form.container} onChange={(e) => set({ container: e.target.value })}
                   className={input} placeholder="e.g. Plain (red top)" />
          </Field>
          <Field label="Turnaround (hours)">
            <input type="number" min="0" value={form.turnaround_hours ?? ""}
                   onChange={(e) => set({ turnaround_hours: e.target.value })}
                   className={input} placeholder="e.g. 6" />
          </Field>
        </Section>

        <Section title="Billing & preparation">
          <Field label="Price (₦)" note="What the counter bills. Money is still taken at the desk.">
            <input type="number" min="0" step="0.01" value={form.price}
                   onChange={(e) => set({ price: e.target.value })} className={input} />
          </Field>
          <Field label="Description / preparation" className="sm:col-span-2"
                 note="Shown to whoever prepares the patient.">
            <textarea rows={2} value={form.description}
                      onChange={(e) => set({ description: e.target.value })}
                      className={input} placeholder="e.g. Patient fasting 8–12 hours." />
          </Field>
        </Section>
      </div>

      {error && (
        <p className="border-t border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700 sm:px-6">
          {error}
        </p>
      )}

      <footer className="flex flex-col gap-3 border-t border-slate-100 bg-slate-50 p-4 sm:flex-row sm:flex-wrap sm:items-center sm:px-6">
        <button
          type="button" onClick={() => save.mutate()}
          disabled={save.isPending || !form.name.trim() || !dirty}
          className={`w-full rounded-lg px-5 py-2.5 text-sm font-semibold transition disabled:cursor-default sm:w-auto ${
            dirty
              ? "bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50"
              : "border border-slate-300 bg-white text-slate-600"}`}
        >
          {save.isPending ? "Saving…" : isNew ? "Add test" : dirty ? "Save changes" : "✓ Saved"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel}
                  className="w-full rounded-lg border border-slate-300 bg-white px-5 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 sm:w-auto">
            Cancel
          </button>
        )}
        {dirty && !isNew && (
          <p className="order-last text-sm text-amber-700 sm:order-none">Unsaved changes</p>
        )}

        {!isNew && form.is_active && (
          <div className="sm:ml-auto">
            {confirmingRetire ? (
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <p className="text-sm text-slate-700">Take it off the picker?</p>
                <button
                  type="button" onClick={() => retire.mutate()} disabled={retire.isPending}
                  className="w-full rounded-lg bg-red-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-red-700 disabled:opacity-50 sm:w-auto"
                >
                  {retire.isPending ? "Retiring…" : "Yes, retire"}
                </button>
                <button
                  type="button" onClick={() => setConfirmingRetire(false)}
                  className="w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 sm:w-auto"
                >
                  Keep it
                </button>
              </div>
            ) : (
              <button
                type="button" onClick={() => setConfirmingRetire(true)}
                className="w-full rounded-lg border border-red-300 bg-white px-5 py-2.5 text-sm font-medium text-red-700 transition hover:bg-red-50 sm:w-auto"
              >
                Retire this test
              </button>
            )}
          </div>
        )}
        {!isNew && !form.is_active && (
          <button
            type="button" onClick={() => restore.mutate()} disabled={restore.isPending}
            className="w-full rounded-lg border border-emerald-300 bg-white px-5 py-2.5 text-sm font-medium text-emerald-700 transition hover:bg-emerald-50 disabled:opacity-50 sm:ml-auto sm:w-auto"
          >
            {restore.isPending ? "Bringing back…" : "Bring back into use"}
          </button>
        )}
      </footer>
    </section>
  );
}

/* ----------------------------------------------------------- the parameters */

const emptyParameter = {
  code: "", name: "", group: "", result_type: "numeric", unit: "",
  reference_range: "", ref_low: "", ref_high: "", normal_value: "", options: [],
  is_required: false, is_active: true,
};

function ParameterEditor({ test, meta, onChanged }) {
  const { showToast } = useToast();
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState(null);

  const rows = useMemo(
    () => [...(test.parameters ?? [])].sort((a, b) => a.display_order - b.display_order),
    [test.parameters],
  );

  // The catalogue groups urinalysis into Physical / Chemical / Microscopy and
  // the bench reads it that way; showing the heading once beats repeating it
  // as a subtitle under every line.
  const grouped = useMemo(() => {
    if (!rows.some((r) => r.group)) return [{ key: "", label: null, rows }];
    const out = [];
    for (const row of rows) {
      const key = row.group || "Other";
      const last = out[out.length - 1];
      if (last && last.key === key) last.rows.push(row);
      else out.push({ key, label: key, rows: [row] });
    }
    return out;
  }, [rows]);

  const save = useMutation({
    mutationFn: (row) => {
      const body = {
        ...row,
        test: test.id,
        ref_low: row.ref_low === "" ? null : row.ref_low,
        ref_high: row.ref_high === "" ? null : row.ref_high,
        options: typeof row.options === "string"
          ? row.options.split(",").map((o) => o.trim()).filter(Boolean)
          : row.options,
        code: (row.code || row.name).toLowerCase().replace(/[^a-z0-9]+/g, "_")
          .replace(/^_|_$/g, "").slice(0, 40),
      };
      return row.id
        ? api.patch(`/lab-parameters/${row.id}/`, body)
        : api.post("/lab-parameters/", body);
    },
    onSuccess: () => {
      setError(null);
      setEditing(null);
      onChanged();
      showToast({ title: "Parameter saved" });
    },
    onError: (err) => setError(readError(err, "Could not save this parameter.")),
  });

  const retire = useMutation({
    mutationFn: (row) => api.delete(`/lab-parameters/${row.id}/`),
    onSuccess: () => { onChanged(); showToast({ title: "Parameter removed from the form" }); },
    onError: (err) => setError(readError(err, "Could not remove this parameter.")),
  });

  const reorder = useMutation({
    mutationFn: (ids) => api.post("/lab-parameters/reorder/", { order: ids }),
    onSuccess: onChanged,
  });

  function move(index, by) {
    const next = [...rows];
    const target = index + by;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    reorder.mutate(next.map((r) => r.id));
  }

  const indexOf = (row) => rows.findIndex((r) => r.id === row.id);

  const rowProps = (row) => ({
    row,
    meta,
    index: indexOf(row),
    last: rows.length - 1,
    onMove: move,
    onEdit: () => setEditing({ ...row, options: (row.options ?? []).join(", ") }),
    onRemove: () => retire.mutate(row),
  });

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 p-4 sm:px-6">
        <div className="min-w-0">
          <h2 className="font-semibold text-slate-900">
            Parameters <span className="font-normal text-slate-600">({rows.length})</span>
          </h2>
          <p className="mt-0.5 max-w-xl text-sm text-slate-600">
            The lines on the bench's result form, in this order. None of them is compulsory —
            the scientist fills in what they measured.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setEditing({ ...emptyParameter })}
          className="shrink-0 rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
        >
          + Add parameter
        </button>
      </header>

      {editing && (
        <div className="border-b border-slate-100 p-4 sm:p-6">
          <ParameterForm
            row={editing}
            meta={meta}
            error={error}
            saving={save.isPending}
            onChange={setEditing}
            onSave={() => save.mutate(editing)}
            onCancel={() => { setEditing(null); setError(null); }}
          />
        </div>
      )}

      {error && !editing && (
        <p className="border-b border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700 sm:px-6">
          {error}
        </p>
      )}

      {rows.length === 0 && (
        <p className="px-4 py-8 text-center text-sm text-slate-600 sm:px-6">
          No parameters yet. A test with none is still usable — the bench writes a comment.
        </p>
      )}

      {/* A table on a wide screen; the same rows as cards on a phone, rather
          than a 40rem table dragged sideways under a finger. */}
      {rows.length > 0 && (
        <>
          <div className="hidden md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
                  <th scope="col" className="w-[4.75rem] py-2.5 pl-4 pr-2 font-semibold sm:pl-6">Order</th>
                  <th scope="col" className="py-2.5 pr-3 font-semibold">Parameter</th>
                  <th scope="col" className="py-2.5 pr-3 font-semibold">Type</th>
                  <th scope="col" className="py-2.5 pr-3 font-semibold">Unit</th>
                  <th scope="col" className="py-2.5 pr-3 font-semibold">Reference range</th>
                  <th scope="col" className="py-2.5 pr-4 text-right font-semibold sm:pr-6">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              {grouped.map((group) => (
                <tbody key={group.key || "ungrouped"}>
                  {group.label && (
                    <tr>
                      <th
                        colSpan={6} scope="colgroup"
                        className="border-b border-slate-100 bg-slate-50/70 px-4 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 sm:px-6"
                      >
                        {group.label}
                      </th>
                    </tr>
                  )}
                  {group.rows.map((row) => (
                    <ParameterTableRow key={row.id} {...rowProps(row)} />
                  ))}
                </tbody>
              ))}
            </table>
          </div>

          <div className="divide-y divide-slate-100 md:hidden">
            {grouped.map((group) => (
              <div key={group.key || "ungrouped"}>
                {group.label && (
                  <p className="bg-slate-50 px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-600">
                    {group.label}
                  </p>
                )}
                {group.rows.map((row) => (
                  <ParameterCard key={row.id} {...rowProps(row)} />
                ))}
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function typeLabel(meta, row) {
  return meta?.result_types?.find((t) => t.value === row.result_type)?.label ?? row.result_type;
}

function rangeText(row) {
  return row.reference_range || row.normal_value || "—";
}

function ParameterTableRow({ row, meta, index, last, onMove, onEdit, onRemove }) {
  return (
    <tr className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60">
      <td className="py-2.5 pl-4 pr-2 align-top sm:pl-6">
        <div className="flex gap-1">
          <ArrowButton label={`Move ${row.name} up`} onClick={() => onMove(index, -1)}
                       disabled={index === 0}>↑</ArrowButton>
          <ArrowButton label={`Move ${row.name} down`} onClick={() => onMove(index, 1)}
                       disabled={index === last}>↓</ArrowButton>
        </div>
      </td>
      <td className="py-2.5 pr-3 align-top">
        <p className="font-medium text-slate-800">{row.name}</p>
        {row.is_required && (
          <span className="mt-0.5 inline-block rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200">
            Required
          </span>
        )}
      </td>
      <td className="py-2.5 pr-3 align-top">
        <span className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${typeTone(row.result_type)}`}>
          {typeLabel(meta, row)}
        </span>
      </td>
      <td className="whitespace-nowrap py-2.5 pr-3 align-top text-slate-700">{row.unit || "—"}</td>
      <td className="py-2.5 pr-3 align-top text-slate-700">{rangeText(row)}</td>
      <td className="py-2.5 pr-4 text-right align-top sm:pr-6">
        <div className="inline-flex gap-2">
          <RowAction onClick={onEdit}>Edit</RowAction>
          <RowAction onClick={onRemove} tone="danger">Remove</RowAction>
        </div>
      </td>
    </tr>
  );
}

function ParameterCard({ row, meta, index, last, onMove, onEdit, onRemove }) {
  return (
    <div className="flex gap-3 p-4">
      <div className="flex shrink-0 flex-col gap-1.5">
        <ArrowButton label={`Move ${row.name} up`} onClick={() => onMove(index, -1)}
                     disabled={index === 0}>↑</ArrowButton>
        <ArrowButton label={`Move ${row.name} down`} onClick={() => onMove(index, 1)}
                     disabled={index === last}>↓</ArrowButton>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium text-slate-800">{row.name}</p>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${typeTone(row.result_type)}`}>
            {typeLabel(meta, row)}
          </span>
          {row.is_required && (
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200">
              Required
            </span>
          )}
        </div>
        <dl className="mt-1.5 space-y-0.5 text-sm">
          <div className="flex gap-2">
            <dt className="w-16 shrink-0 text-slate-600">Unit</dt>
            <dd className="min-w-0 text-slate-800">{row.unit || "—"}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-16 shrink-0 text-slate-600">Range</dt>
            <dd className="min-w-0 text-slate-800">{rangeText(row)}</dd>
          </div>
        </dl>
        <div className="mt-3 flex gap-2">
          <RowAction onClick={onEdit}>Edit</RowAction>
          <RowAction onClick={onRemove} tone="danger">Remove</RowAction>
        </div>
      </div>
    </div>
  );
}

function RowAction({ children, onClick, tone }) {
  const tones = tone === "danger"
    ? "border-red-300 text-red-700 hover:bg-red-50"
    : "border-slate-300 text-slate-700 hover:bg-slate-50";
  return (
    <button
      type="button" onClick={onClick}
      className={`rounded-md border bg-white px-3 py-1.5 text-sm font-medium transition ${tones}`}
    >
      {children}
    </button>
  );
}

function ParameterForm({ row, meta, error, saving, onChange, onSave, onCancel }) {
  const set = (patch) => onChange({ ...row, ...patch });
  const needsOptions = row.result_type === "select";
  const numeric = row.result_type === "numeric";

  return (
    <div className="rounded-xl border border-brand-200 bg-brand-50/50 p-4">
      <h3 className="mb-4 font-semibold text-slate-900">
        {row.id ? `Edit ${row.name}` : "New parameter"}
      </h3>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Parameter name" className="sm:col-span-2 lg:col-span-1">
          <input value={row.name} onChange={(e) => set({ name: e.target.value })}
                 className={input} placeholder="e.g. Haemoglobin (Hb)" autoFocus />
        </Field>
        <Field label="Result type" note={RESULT_TYPE_HELP[row.result_type]}>
          <select value={row.result_type} onChange={(e) => set({ result_type: e.target.value })}
                  className={input}>
            {(meta?.result_types ?? []).map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </Field>
        <Field label="Group / heading" note="Optional — e.g. Physical, Chemical, Microscopy.">
          <input value={row.group} onChange={(e) => set({ group: e.target.value })}
                 className={input} />
        </Field>
        <Field label="Unit">
          <input value={row.unit} onChange={(e) => set({ unit: e.target.value })}
                 className={input} placeholder="g/dL" />
        </Field>
        <Field label="Reference range (printed)">
          <input value={row.reference_range}
                 onChange={(e) => set({ reference_range: e.target.value })}
                 className={input} placeholder="12 – 16, or M 13 – 17, F 12 – 15" />
        </Field>
        {numeric ? (
          <>
            <Field label="Flag below" note="Leave empty and nothing is flagged low.">
              <input type="number" step="any" value={row.ref_low ?? ""}
                     onChange={(e) => set({ ref_low: e.target.value })} className={input} />
            </Field>
            <Field label="Flag above" note="Leave empty and nothing is flagged high.">
              <input type="number" step="any" value={row.ref_high ?? ""}
                     onChange={(e) => set({ ref_high: e.target.value })} className={input} />
            </Field>
          </>
        ) : (
          <Field label="Expected / normal value">
            <input value={row.normal_value} onChange={(e) => set({ normal_value: e.target.value })}
                   className={input} placeholder="e.g. Negative" />
          </Field>
        )}
        {needsOptions && (
          <Field label="Options" className="sm:col-span-2 lg:col-span-3"
                 note="Comma separated — these are what the bench picks from.">
            <input value={row.options ?? ""} onChange={(e) => set({ options: e.target.value })}
                   className={input} placeholder="Nil, Trace, +, ++, +++" />
          </Field>
        )}
      </div>

      <label className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-700">
        <input type="checkbox" checked={row.is_required}
               onChange={(e) => set({ is_required: e.target.checked })}
               className="h-4 w-4 rounded border-slate-400 text-brand-600 focus:ring-brand-500" />
        Insist on a value for this parameter
        <span className="text-slate-600">
          — off by default, and off on everything the catalogue ships with.
        </span>
      </label>

      {error && <p className="mt-3 text-sm text-red-700">{error}</p>}

      <div className="mt-4 flex flex-col gap-3 sm:flex-row">
        <button type="button" onClick={onSave} disabled={saving || !row.name.trim()}
                className="w-full rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50 sm:w-auto">
          {saving ? "Saving…" : "Save parameter"}
        </button>
        <button type="button" onClick={onCancel}
                className="w-full rounded-lg border border-slate-300 bg-white px-5 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 sm:w-auto">
          Cancel
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- primitives */

function ArrowButton({ children, label, onClick, disabled }) {
  return (
    <button
      type="button" onClick={onClick} disabled={disabled} aria-label={label}
      className="grid h-8 w-8 place-items-center rounded-md border border-slate-300 bg-white text-sm text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 disabled:opacity-30 disabled:hover:border-slate-300 disabled:hover:bg-white"
    >
      {children}
    </button>
  );
}

function Section({ title, note, children }) {
  return (
    <div>
      <div className="mb-3 border-b border-slate-100 pb-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">{title}</h3>
        {note && <p className="mt-0.5 text-sm text-slate-600">{note}</p>}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{children}</div>
    </div>
  );
}

function Field({ label, note, className = "", children }) {
  return (
    <div className={`min-w-0 ${className}`}>
      <label className="mb-1 block text-sm font-medium text-slate-700">{label}</label>
      {children}
      {note && <p className="mt-1 text-sm text-slate-600">{note}</p>}
    </div>
  );
}

const input = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-base text-slate-900 outline-none transition placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-100 sm:py-2 sm:text-sm";
