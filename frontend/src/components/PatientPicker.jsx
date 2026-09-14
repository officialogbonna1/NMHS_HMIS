import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { Button, controlClass } from "./ui.jsx";
import { Icon } from "./icons.jsx";
import { patientNumber } from "./patientIdentity.js";

// One patient picker for every screen that has to find a patient.
//
// It is a combobox, not a search box: clicking it opens the list you are
// allowed to see, so somebody who does not know how the name is spelled can
// scroll to it, and typing filters that same list by name, hospital number
// or phone number.
// A plain search input showed nothing until you had already guessed right.
//
// The list is whatever `/patients/` returns for the signed-in role — the
// backend scopes it (patients.access.patient_queryset_for), so reception
// browses everyone while a nurse browses only the patients routed to them.
//
// Two shapes of the same picker. The default is that combobox — the text box
// is the control. `variant="dropdown"` is a select-style button that stays
// shut until it is clicked, and then opens a panel holding the search box and
// the list: for a desk whose first job is choosing a patient, where a bare text
// box reads as "type something" rather than "pick one". Same endpoint, same
// scoping and the same keyboard handling either way.

export { patientNumber };

export function patientLabel(p) {
  if (!p) return "";
  return p.display ?? [p.last_name, p.first_name].filter(Boolean).join(", ");
}

export default function PatientPicker({
  value,                    // the selected patient object, or null
  onChange,                 // (patient | null) => void
  placeholder = "Search or pick a patient…",
  searchPlaceholder = "Type a name, NMHS number or phone…",   // dropdown only
  variant = "combobox",     // "combobox" | "dropdown"
  autoFocus = false,
  disabled = false,
  invalid = false,
  emptyMessage = "No patients match that.",
}) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [highlight, setHighlight] = useState(0);
  const [place, setPlace] = useState(null);
  const boxRef = useRef(null);
  const inputRef = useRef(null);
  const triggerRef = useRef(null);
  const panelRef = useRef(null);
  const listId = useId();
  const dropdown = variant === "dropdown";

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

  // A dropdown opens fresh each time: a search typed and abandoned is not
  // waiting in the box the next time somebody opens it.
  useEffect(() => { if (dropdown && !open) setTerm(""); }, [dropdown, open]);

  // Clicking away closes the list without choosing anything. The dropdown's
  // panel is portalled out of the box, so a click inside it counts as inside.
  useEffect(() => {
    if (!open) return undefined;
    function onDocClick(e) {
      if (boxRef.current?.contains(e.target) || panelRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  // Where the dropdown's panel goes. It is rendered into <body> at a fixed
  // position under the button rather than absolutely inside it, because
  // `Card` clips its contents (`overflow-hidden`): inside a card the panel was
  // cut off a few pixels below the button, and the patient list — loaded and
  // sitting there — could not be seen. Flips above the button when there is
  // no room below, and follows the button on scroll and resize.
  useLayoutEffect(() => {
    if (!dropdown || !open) return undefined;
    function measure() {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const gap = 4;
      const margin = 8;
      const below = window.innerHeight - rect.bottom - gap - margin;
      const above = rect.top - gap - margin;
      const flip = below < 240 && above > below;
      setPlace({
        left: rect.left,
        width: rect.width,
        ...(flip ? { bottom: window.innerHeight - rect.top + gap } : { top: rect.bottom + gap }),
        maxHeight: Math.max(180, Math.min(380, flip ? above : below)),
      });
    }
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [dropdown, open]);

  function choose(patient) {
    onChange(patient);
    setTerm("");
    setOpen(false);
    if (dropdown) setTimeout(() => triggerRef.current?.focus(), 0);
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
      if (dropdown) {
        // Closing the list, not whatever dialog the picker happens to sit in.
        e.stopPropagation();
        triggerRef.current?.focus();
      }
    }
  }

  if (value) {
    return (
      <div className={`flex items-center justify-between gap-3 rounded-md border px-3 py-2 ${
        disabled ? "border-slate-200 bg-slate-100" : "border-brand-200 bg-brand-50/60"
      }`}>
        <span className="min-w-0">
          <span className="font-medium text-slate-800">{patientLabel(value)}</span>
          {patientNumber(value) && <span className="ml-2 text-sm text-slate-600">{patientNumber(value)}</span>}
        </span>
        {!disabled && (
          <Button
            variant="link" size="xs" className="shrink-0"
            onClick={() => {
              onChange(null);
              setTimeout(() => (inputRef.current ?? triggerRef.current)?.focus(), 0);
            }}
          >
            Change
          </Button>
        )}
      </div>
    );
  }

  const renderOptions = (asListbox) => (
    <>
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
          {...(asListbox ? { role: "option", "aria-selected": index === highlight } : {})}
          onMouseEnter={() => setHighlight(index)}
          onClick={() => choose(p)}
          className={`block w-full px-3 py-2 text-left text-sm ${
            index === highlight ? "bg-brand-50 text-brand-900" : "hover:bg-slate-50"
          }`}
        >
          <span className="font-medium text-slate-800">{patientLabel(p)}</span>
          <span className="ml-2 text-slate-600">{patientNumber(p)}</span>
          {p.phone_number && <span className="ml-2 text-slate-600">· {p.phone_number}</span>}
        </button>
      ))}
    </>
  );

  if (dropdown) {
    return (
      <div ref={boxRef} className="relative">
        <button
          ref={triggerRef}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          autoFocus={autoFocus}
          disabled={disabled}
          // Shut until it is clicked — focus alone never opens it.
          onClick={() => setOpen((was) => !was)}
          onKeyDown={(e) => {
            if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) {
              e.preventDefault();
              setOpen(true);
            }
          }}
          className={`${controlClass} flex items-center justify-between gap-2 text-left ${
            invalid ? "!border-red-400 !bg-red-50" : ""}`}
        >
          <span className="min-w-0 truncate text-slate-600">{placeholder}</span>
          <Icon
            name="chevronDown" aria-hidden="true"
            className={`h-4 w-4 shrink-0 text-slate-600 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </button>

        {open && createPortal(
          <div
            ref={panelRef}
            style={{ position: "fixed", ...(place ?? {}) }}
            className="z-[60] flex flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg"
          >
            <div className="shrink-0 border-b border-slate-100 p-2">
              <input
                ref={inputRef}
                type="text"
                role="combobox"
                aria-expanded="true"
                aria-controls={listId}
                aria-autocomplete="list"
                aria-label="Search patients"
                autoFocus
                value={term}
                placeholder={searchPlaceholder}
                onChange={(e) => setTerm(e.target.value)}
                onKeyDown={onKeyDown}
                className={controlClass}
              />
            </div>
            <div id={listId} role="listbox" aria-label="Patients"
                 className="min-h-0 flex-1 overflow-y-auto overscroll-contain py-1">
              {renderOptions(true)}
            </div>
          </div>,
          document.body,
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
          {renderOptions(false)}
        </div>
      )}
    </div>
  );
}
