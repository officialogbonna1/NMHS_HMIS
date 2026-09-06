import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { patientNumber } from "../components/patientIdentity.js";
import { Button, Page, PageHeader, Breadcrumb } from "../components/ui.jsx";
import { readError } from "../api/errors";
import { useToast } from "../components/Toaster.jsx";

// The doctor writes a whole script here, not one drug at a time: add each
// drug with its quantity and directions, then send the lot to the pharmacy
// in one action (POST /prescriptions/bulk/, written inside one transaction).
//
// Nothing here moves stock. The pharmacist dispensing is what deducts the
// batches FEFO, writes the StockMovement rows and raises the charge the
// counter then collects — see pharmacy/services.py.
//
// The picker shows availability, never counts: /items/ returns
// `available: true/false` for doctors (ItemForPrescribingSerializer).

export default function PrescribeDrug() {
  const { id: patientId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [lines, setLines] = useState([]);       // the script being written
  const [error, setError] = useState(null);

  const { data: patient } = useQuery({
    queryKey: ["patient", patientId],
    queryFn: () => api.get(`/patients/${patientId}/`).then((r) => r.data),
  });

  const { data: existing } = useQuery({
    queryKey: ["prescriptions", patientId],
    queryFn: () => api.get("/prescriptions/", { params: { patient: patientId, page_size: 100 } })
      .then((r) => r.data.results ?? r.data),
  });

  const send = useMutation({
    mutationFn: () => api.post("/prescriptions/bulk/", {
      patient: patientId,
      lines: lines.map((l) => ({
        item: l.item.id,
        quantity: l.quantity,
        dosage_instructions: l.instructions,
      })),
    }),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ["prescriptions", patientId] });
      queryClient.invalidateQueries({ queryKey: ["patient-overview", String(patientId)] });
      const count = response.data.length;
      showToast({
        title: "Sent to pharmacy",
        message: `${count} drug${count === 1 ? "" : "s"} queued. The pharmacy dispenses and takes payment.`,
      });
      navigate(`/patients/${patientId}`);
    },
    // The whole script is rejected together, so the message names the drug
    // that stopped it and nothing has been queued.
    onError: (err) => setError(readError(err, "Could not send this prescription.")),
  });

  const pending = (existing ?? []).filter((p) => p.status === "pending");

  function addLine(item) {
    setError(null);
    setLines((current) => current.some((l) => l.item.id === item.id)
      ? current
      : [...current, { item, quantity: 1, instructions: "" }]);
  }

  function updateLine(itemId, patch) {
    setLines((current) => current.map((l) => (l.item.id === itemId ? { ...l, ...patch } : l)));
  }

  function removeLine(itemId) {
    setLines((current) => current.filter((l) => l.item.id !== itemId));
  }

  const incomplete = lines.filter((l) => !l.quantity || l.quantity < 1);
  const canSend = lines.length > 0 && incomplete.length === 0 && !send.isPending;

  return (
    <Page width="narrow" className="space-y-6">
      {/* The chart is where this was started, so the way back names it. */}
      <PageHeader
        className="mb-0"
        icon="pill"
        title="Prescribe medication"
        breadcrumb={
          <Breadcrumb
            items={[
              { label: "Patients", to: "/patients" },
              {
                label: patient ? `${patient.last_name}, ${patient.first_name}` : "Patient",
                to: `/patients/${patientId}`,
              },
              { label: "Prescribe" },
            ]}
          />
        }
        subtitle={
          patient
            ? `For ${patient.last_name}, ${patient.first_name} · ${patientNumber(patient)}`
            : "Loading patient…"
        }
      />

      {pending.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">Already with the pharmacy</p>
          <ul className="mt-1 space-y-0.5">
            {pending.map((p) => (
              <li key={p.id}>{p.item_name} ×{p.quantity} — awaiting dispensing</li>
            ))}
          </ul>
        </div>
      )}

      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-medium text-slate-800">Add a drug</h2>
        <p className="mb-3 text-sm text-slate-700">
          Pick from what the pharmacy has in stock. Add as many as the patient needs — they go over
          as one prescription.
        </p>
        <DrugPicker onPick={addLine} chosenIds={lines.map((l) => l.item.id)} />
      </section>

      <section className="rounded-xl border bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-medium text-slate-800">This prescription</h2>
          <span className="text-sm font-medium text-slate-700">
            {lines.length} drug{lines.length === 1 ? "" : "s"}
          </span>
        </div>

        {lines.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-600">
            Nothing added yet. Search for a drug above, or open the list.
          </p>
        ) : (
          <div className="space-y-3">
            {lines.map((line) => (
              <div key={line.item.id} className="rounded-lg border border-slate-200 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-medium text-slate-800">{line.item.name}</p>
                    <p className="text-sm text-slate-700">
                      {line.item.category || "Drug"}
                      {line.item.unit && ` · per ${line.item.unit}`}
                    </p>
                  </div>
                  <Button variant="linkDanger" size="xs" onClick={() => removeLine(line.item.id)}>
                    Remove
                  </Button>
                </div>

                <div className="mt-3 grid gap-3 sm:grid-cols-[8rem,1fr]">
                  <div>
                    <label className="mb-1 block text-sm font-medium text-slate-700">
                      Quantity ({line.item.unit || "units"}) *
                    </label>
                    <input
                      type="number"
                      min={1}
                      value={line.quantity}
                      onChange={(e) => updateLine(line.item.id, {
                        quantity: e.target.value === "" ? "" : Number(e.target.value),
                      })}
                      className={`w-full rounded-md border px-3 py-2 ${
                        !line.quantity || line.quantity < 1 ? "border-amber-400 bg-amber-50" : "border-slate-300"
                      }`}
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium text-slate-700">Directions</label>
                    <input
                      value={line.instructions}
                      onChange={(e) => updateLine(line.item.id, { instructions: e.target.value })}
                      placeholder="e.g. 1 tablet three times daily after meals"
                      className="w-full rounded-md border border-slate-300 px-3 py-2"
                      list="dosage-suggestions"
                    />
                  </div>
                </div>
              </div>
            ))}
            <datalist id="dosage-suggestions">
              {["Once daily", "Twice daily", "Three times daily", "Four times daily",
                "1 tablet three times daily after meals", "At night", "As needed for pain"]
                .map((o) => <option key={o} value={o} />)}
            </datalist>
          </div>
        )}
      </section>

      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      )}
      {incomplete.length > 0 && (
        <p className="text-sm text-amber-700">
          Set a quantity for {incomplete.map((l) => l.item.name).join(", ")}.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => { setError(null); send.mutate(); }}
          disabled={!canSend}
          className="rounded-md bg-brand-600 px-6 py-2.5 font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {send.isPending
            ? "Sending…"
            : `Send ${lines.length || ""} to pharmacy`.replace("  ", " ")}
        </button>
        <button
          onClick={() => navigate(`/patients/${patientId}`)}
          className="rounded-md border border-slate-300 px-4 py-2.5 text-sm font-medium"
        >
          Cancel
        </button>
        <span className="text-sm text-slate-700">
          The pharmacy deducts stock and takes payment when they hand the drugs over.
        </span>
      </div>
    </Page>
  );
}

// Same combobox behaviour as the patient picker: click to browse the whole
// catalogue, type to filter it. Searching happens server-side, so a drug
// past the first page of the catalogue is still findable — the old page
// filtered one already-fetched page and reported "No drugs match".
function DrugPicker({ onPick, chosenIds }) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [highlight, setHighlight] = useState(0);
  const boxRef = useRef(null);
  const inputRef = useRef(null);

  const query = term.trim();
  const { data, isFetching } = useQuery({
    queryKey: ["items-for-prescribing", query],
    queryFn: () => api
      .get("/items/", { params: { ...(query ? { search: query } : {}), page_size: 100 } })
      .then((r) => r.data.results ?? r.data),
    enabled: open,
    staleTime: 30000,
  });

  const options = useMemo(() => data ?? [], [data]);
  useEffect(() => setHighlight(0), [query, open]);

  useEffect(() => {
    if (!open) return undefined;
    function onDocClick(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  function choose(item) {
    if (!item.available) return;
    onPick(item);
    setTerm("");
    inputRef.current?.focus();
  }

  function onKeyDown(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      const step = e.key === "ArrowDown" ? 1 : -1;
      setHighlight((h) => Math.min(Math.max(h + step, 0), Math.max(options.length - 1, 0)));
      return;
    }
    if (e.key === "Enter" && open && options[highlight]) {
      e.preventDefault();
      choose(options[highlight]);
      return;
    }
    if (e.key === "Escape" && open) { e.preventDefault(); setOpen(false); }
  }

  return (
    <div ref={boxRef} className="relative">
      <div className="flex">
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
          value={term}
          placeholder="Search drugs, or open the list…"
          onChange={(e) => { setTerm(e.target.value); setOpen(true); }}
          // Opened by clicking or typing, never by focus alone: an
          // autoFocused field fires focus on mount, which had the list
          // hanging open before anyone had touched the page.
          onClick={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className="w-full rounded-md border border-slate-300 px-3 py-2 pr-9 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
        />
        <button
          type="button"
          tabIndex={-1}
          aria-label={open ? "Close drug list" : "Open drug list"}
          onClick={() => { setOpen((o) => !o); inputRef.current?.focus(); }}
          className="-ml-8 self-stretch px-2 text-slate-600 hover:text-slate-900"
        >
          ▾
        </button>
      </div>

      {open && (
        <div className="absolute z-30 mt-1 max-h-80 w-full overflow-auto rounded-md border border-slate-200 bg-white shadow-lg">
          {isFetching && options.length === 0 && (
            <p className="px-3 py-2 text-sm text-slate-700">Loading the catalogue…</p>
          )}
          {!isFetching && options.length === 0 && (
            <p className="px-3 py-2 text-sm text-slate-700">
              {query ? "No drug by that name." : "The drug catalogue is empty — the pharmacy adds items in Inventory."}
            </p>
          )}
          {options.map((item, index) => {
            const already = chosenIds.includes(item.id);
            return (
              <button
                type="button"
                key={item.id}
                disabled={!item.available || already}
                onMouseEnter={() => setHighlight(index)}
                onClick={() => choose(item)}
                className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm disabled:cursor-not-allowed ${
                  index === highlight && item.available && !already ? "bg-brand-50" : "hover:bg-slate-50"
                } ${!item.available || already ? "opacity-60" : ""}`}
              >
                <span>
                  <span className="font-medium text-slate-800">{item.name}</span>
                  {item.category && <span className="ml-2 text-slate-700">{item.category}</span>}
                </span>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                  already ? "bg-brand-100 text-brand-700"
                    : item.available ? "bg-emerald-100 text-emerald-700"
                    : "bg-red-100 text-red-700"
                }`}>
                  {already ? "On this script" : item.available ? "In stock" : "Out of stock"}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
