import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";

// One patient picker for every screen that has to find a patient.
//
// It is a combobox, not a search box: clicking it opens the list you are
// allowed to see, so somebody who does not know how the name is spelled can
// scroll to it, and typing filters that same list by name or file number.
// A plain search input showed nothing until you had already guessed right.
//
// The list is whatever `/patients/` returns for the signed-in role — the
// backend scopes it (patients.access.patient_queryset_for), so reception
// browses everyone while a nurse browses only the patients routed to them.

export function patientLabel(p) {
  if (!p) return "";
  return p.display ?? [p.last_name, p.first_name].filter(Boolean).join(", ");
}

export default function PatientPicker({
  value,                    // the selected patient object, or null
  onChange,                 // (patient | null) => void
  placeholder = "Search or pick a patient…",
  autoFocus = false,
  disabled = false,
  invalid = false,
  emptyMessage = "No patients match that.",
}) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [highlight, setHighlight] = useState(0);
  const boxRef = useRef(null);
  const inputRef = useRef(null);

  // With no term this is the browsable list; with one it is the search. Both
  // go through the same endpoint, so what you can scroll to and what you can
  // find are always the same set.
  const query = term.trim();
  const { data, isFetching } = useQuery({
    queryKey: ["patient-picker", query],
    queryFn: () => api
      .get("/patients/", { params: { ...(query ? { search: query } : {}), page_size: 50 } })
      .then((r) => r.data.results ?? r.data),
    enabled: open,
    staleTime: 30000,
  });

  const options = useMemo(() => data ?? [], [data]);

  useEffect(() => setHighlight(0), [query, open]);

  // Clicking away closes the list without choosing anything.
  useEffect(() => {
    if (!open) return undefined;
    function onDocClick(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  function choose(patient) {
    onChange(patient);
    setTerm("");
    setOpen(false);
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
    if (e.key === "Escape" && open) {
      e.preventDefault();
      setOpen(false);
    }
  }

  if (value) {
    return (
      <div className={`flex items-center justify-between gap-3 rounded-md border px-3 py-2 ${
        disabled ? "border-slate-200 bg-slate-100" : "border-brand-200 bg-brand-50/60"
      }`}>
        <span className="min-w-0">
          <span className="font-medium text-slate-800">{patientLabel(value)}</span>
          {value.file_number && <span className="ml-2 text-sm text-slate-600">{value.file_number}</span>}
        </span>
        {!disabled && (
          <button
            type="button"
            onClick={() => { onChange(null); setTimeout(() => inputRef.current?.focus(), 0); }}
            className="shrink-0 text-sm font-medium text-brand-600 hover:underline"
          >
            Change
          </button>
        )}
      </div>
    );
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
          autoFocus={autoFocus}
          disabled={disabled}
          value={term}
          placeholder={placeholder}
          onChange={(e) => { setTerm(e.target.value); setOpen(true); }}
          // Opened by clicking or typing, never by focus alone: an
          // autoFocused field fires focus on mount, which had the list
          // hanging open before anyone had touched the page.
          onClick={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className={`w-full rounded-md border px-3 py-2 pr-9 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:bg-slate-100 ${
            invalid ? "border-red-400 bg-red-50" : "border-slate-300"
          }`}
        />
        <button
          type="button"
          tabIndex={-1}
          disabled={disabled}
          aria-label={open ? "Close patient list" : "Open patient list"}
          onClick={() => { setOpen((o) => !o); inputRef.current?.focus(); }}
          className="-ml-8 self-stretch px-2 text-slate-600 hover:text-slate-900 disabled:opacity-40"
        >
          ▾
        </button>
      </div>

      {open && (
        <div className="absolute z-30 mt-1 max-h-72 w-full overflow-auto rounded-md border border-slate-200 bg-white shadow-lg">
          {isFetching && options.length === 0 && (
            <p className="px-3 py-2 text-sm text-slate-700">Loading patients…</p>
          )}
          {!isFetching && options.length === 0 && (
            <p className="px-3 py-2 text-sm text-slate-700">
              {query ? emptyMessage : "No patients on your list yet."}
            </p>
          )}
          {options.map((p, index) => (
            <button
              type="button"
              key={p.id}
              onMouseEnter={() => setHighlight(index)}
              onClick={() => choose(p)}
              className={`block w-full px-3 py-2 text-left text-sm ${
                index === highlight ? "bg-brand-50 text-brand-900" : "hover:bg-slate-50"
              }`}
            >
              <span className="font-medium text-slate-800">{patientLabel(p)}</span>
              <span className="ml-2 text-slate-600">{p.file_number}</span>
              {p.phone_number && <span className="ml-2 text-slate-600">· {p.phone_number}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
