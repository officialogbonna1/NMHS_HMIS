import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";

import api from "../api/client";
import { Badge, Button, SearchInput, Skeleton } from "./ui.jsx";
import { LetterRail, PatientAvatar, PatientListRow, PatientSections } from "./patientListing.jsx";
import { responsibleDoctor, responsibleNurse, UNASSIGNED } from "./maternityAssignment.js";

// The maternity desk's patient list.
//
// **It is the front desk's patient screen, over the ward's patients.** Same
// search box, same rows, same A–Z sections, same letter rail — the listing
// itself is `components/patientListing.jsx`, which reception's `PatientsList`
// renders too, so there is one way a list of patients looks in this hospital
// and no second one to drift.
//
// What differs is only what a row *does*: reception's open the chart, these
// choose the mother whose maternity record loads below. So the rows are
// options in a listbox rather than links, and they carry the line a ward list
// needs under the name — which pregnancy, how far along, and who is
// responsible for her.
//
// **Nothing is filtered here.** The list is what `/maternity/patients/`
// returned, and the server returned what this user is allowed to see
// (`patients.access.patient_queryset_for`). Typing narrows the same request.

export default function MaternityPatientPicker({
  value,
  onChange,
  // The hospital's one search box, and the words it uses. A patient is found
  // the same way at the front desk, on the ward and here.
  placeholder = "Name or file number (e.g. NMHS-000001)",
  label = "Search maternity patients",
  emptyMessage = "No maternity patients match that.",
  // The front desk has to be able to find a woman who is not in Maternity yet
  // — that is the first step of putting her there. It widens nothing: the
  // endpoint returns this caller's own authorised patients either way, which
  // for a midwife is already exactly Maternity's list.
  allowBeyondMaternity = false,
}) {
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [everyone, setEveryone] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef(null);
  const listId = "maternity-patient-list";

  // A quarter second of settling, the way reception's list does it, so typing
  // a surname is one request rather than eight — and `keepPreviousData` holds
  // the rows on screen while the next one lands.
  useEffect(() => {
    const timer = setTimeout(() => setTerm(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const scope = everyone ? "all" : "maternity";
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["maternity-patients", scope, term],
    queryFn: () => api
      .get("/maternity/patients/", { params: { ...(term ? { search: term } : {}), scope } })
      .then((r) => r.data.results ?? r.data),
    placeholderData: keepPreviousData,
  });

  const options = useMemo(() => data ?? [], [data]);

  useEffect(() => setHighlight(0), [term, scope]);

  function choose(patient) {
    onChange(patient);
    setSearch("");
  }

  function onKeyDown(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setHighlight((h) => Math.min(Math.max(h + step, 0), Math.max(options.length - 1, 0)));
      return;
    }
    if (e.key === "Enter" && options[highlight]) {
      e.preventDefault();
      choose(options[highlight]);
    }
  }

  // Chosen: the list steps aside and her record loads below it.
  if (value) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-brand-200 bg-brand-50/60 px-4 py-3">
        <PatientAvatar patient={value} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="truncate font-medium text-slate-900">{value.name}</span>
            <span className="shrink-0 rounded-full bg-white px-2 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
              {value.patient_number}
            </span>
          </span>
          <span className="mt-0.5 block truncate text-sm text-slate-600">
            {maternityLine(value)}
          </span>
        </span>
        <Button variant="link" size="xs" className="shrink-0" onClick={() => onChange(null)}>
          Change
        </Button>
      </div>
    );
  }

  return (
    <div className="relative min-w-0 sm:pr-10">
      <SearchInput
        value={search}
        onChange={setSearch}
        placeholder={placeholder}
        label={label}
        inputRef={inputRef}
        inputProps={{
          role: "combobox",
          "aria-expanded": true,
          "aria-controls": listId,
          "aria-autocomplete": "list",
          onKeyDown,
        }}
      />
      {isFetching && !isLoading && (
        <p className="mt-1.5 text-sm text-slate-600" role="status">Searching…</p>
      )}

      <p className="mt-3 text-xs font-medium uppercase tracking-wide text-slate-500">
        {everyone ? "All patients you can see" : "Maternity patients"}
      </p>

      {isLoading && (
        <div className="mt-2 space-y-2" role="status" aria-label="Loading maternity patients">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
              <Skeleton className="h-11 w-11 shrink-0 rounded-full" />
              <div className="min-w-0 flex-1 space-y-2">
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="h-3 w-1/4" />
              </div>
            </div>
          ))}
        </div>
      )}

      {!isLoading && options.length === 0 && (
        <p className="mt-2 rounded-xl border border-dashed border-slate-300 bg-white px-4 py-6 text-center text-sm text-slate-700">
          {term ? emptyMessage : "No maternity patients on your list yet."}
        </p>
      )}

      <div className="mt-2">
        <PatientSections
          patients={options}
          listProps={{ id: listId, role: "listbox", "aria-label": "Maternity patients" }}
          renderRow={(patient, index) => (
            <PatientListRow
              key={patient.id}
              patient={patient}
              role="option"
              selected={index === highlight}
              highlighted={index === highlight}
              onMouseEnter={() => setHighlight(index)}
              onSelect={() => choose(patient)}
              secondary={<MaternityLine patient={patient} />}
            />
          )}
        />
      </div>

      {allowBeyondMaternity && (
        // One line, not a second control: the desk's normal list is the
        // ward's, and finding a woman who is not on it yet is the exception
        // that precedes putting her there.
        <p className="mt-3">
          <Button variant="link" size="xs" onClick={() => setEveryone((was) => !was)}>
            {everyone ? "Show maternity patients only" : "Search all patients you can see"}
          </Button>
        </p>
      )}

      <LetterRail patients={options} />
    </div>
  );
}

/** The line under a name on a ward list: where she is, and who has her. */
function MaternityLine({ patient }) {
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span>{maternityLine(patient)}</span>
      <Badge tone={patient.assigned_nurse ? "brand" : "neutral"}>
        {responsibleNurse(patient) === UNASSIGNED
          ? "No midwife named" : responsibleNurse(patient)}
      </Badge>
      <Badge tone={patient.assigned_doctor ? "brand" : "neutral"}>
        {responsibleDoctor(patient) === UNASSIGNED
          ? "No doctor named" : responsibleDoctor(patient)}
      </Badge>
    </span>
  );
}

/**
 * The line under a name: **how to reach her**, and nothing else.
 *
 * It carried the pregnancy number and the gestation too, which read as a chart
 * summary on what is a list of people — and both are on the record that opens
 * the moment she is picked, so the row was saying them twice.
 */
function maternityLine(patient) {
  return patient.phone_number || "";
}
