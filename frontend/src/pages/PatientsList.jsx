import { useEffect, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";
import { patientNumber } from "../components/patientIdentity.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { EmptyState, Page, PageHeader, SearchInput, Skeleton, Button, MetaStat } from "../components/ui.jsx";
import { Icon } from "../components/icons.jsx";

const CAN_REGISTER_ROLES = ["reception", "admin", "hospital_admin"];
const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
const AVATAR_TONES = [
  "bg-blue-100 text-blue-700", "bg-violet-100 text-violet-700",
  "bg-emerald-100 text-emerald-700", "bg-amber-100 text-amber-800",
  "bg-rose-100 text-rose-700", "bg-brand-100 text-brand-700",
  "bg-cyan-100 text-cyan-700", "bg-fuchsia-100 text-fuchsia-700",
];

// Mirrors the reference manual's "Your Patients" screen: alphabetical
// listing, live search by name, letter jump.
export default function PatientsList() {
  const { user } = useAuth();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");

  // The search box used to be the query key directly, so every keystroke of
  // "Okonkwo" fired eight requests at `/patients/` and the list flickered
  // through eight results. A quarter second of settling makes it one, and
  // `keepPreviousData` holds the last list on screen while it lands.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["patients", query],
    queryFn: () => api.get("/patients/", { params: { search: query } }).then((r) => r.data.results ?? r.data),
    placeholderData: keepPreviousData,
  });

  const patients = data ?? [];
  const grouped = groupByLastInitial(patients);
  const activeLetters = new Set(Object.keys(grouped));

  return (
    // No `pr-14` any more: that gutter was reserving room for the letter rail
    // at every width, including the phones where the rail is not rendered.
    <Page width="narrow" className="relative sm:pr-12">
      <PageHeader
        icon="users"
        title="Patients"
        subtitle="View, register and manage patient records."
        meta={data && <MetaStat value={patients.length} label={`patient${patients.length === 1 ? "" : "s"}`} />}
        actions={CAN_REGISTER_ROLES.includes(user?.role) && (
          <Button to="/patients/new">
            <Icon name="plus" className="h-4 w-4" aria-hidden="true" />
            Register patient
          </Button>
        )}
        toolbar={
          <div className="min-w-0 flex-1">
            <SearchInput
              value={search}
              onChange={setSearch}
              label="Search patients"
              placeholder="Name or file number (e.g. NMHS-000001)"
            />
            {/* Only while a *new* search is in flight — the list below stays put. */}
            {isFetching && !isLoading && (
              <p className="mt-1.5 text-sm text-slate-600" role="status">Searching…</p>
            )}
          </div>
        }
      />

      {isLoading && (
        <div className="space-y-2" role="status" aria-label="Loading patients">
          {Array.from({ length: 6 }, (_, i) => (
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

      {!isLoading && patients.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white">
          <EmptyState
            icon="users"
            title={search ? "No patients match that search" : "No patients registered yet"}
            description={search ? "Try part of a surname, or the file number." : undefined}
          />
        </div>
      )}

      <div className="space-y-5">
        {Object.keys(grouped).sort().map((letter) => (
          <section key={letter} id={`letter-${letter}`} aria-labelledby={`heading-${letter}`} className="scroll-mt-20">
            <h2 id={`heading-${letter}`} className="mb-1.5 px-1 text-xs font-bold uppercase tracking-wider text-slate-600">
              {letter}
            </h2>
            <div className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
              {grouped[letter].map((p) => <PatientRow key={p.id} patient={p} />)}
            </div>
          </section>
        ))}
      </div>

      {/* The jump rail is a pointer convenience; a thumb scrolls. It sat at
          slate-200/300, which was invisible against white. */}
      {activeLetters.size > 0 && (
        <nav aria-label="Jump to letter"
             className="fixed right-2 top-1/2 z-10 hidden -translate-y-1/2 flex-col items-center rounded-full border border-slate-200 bg-white/90 px-0.5 py-2 shadow-sm backdrop-blur sm:flex">
          {ALPHABET.map((letter) => {
            const has = activeLetters.has(letter);
            return (
              <button
                key={letter} type="button" disabled={!has}
                aria-label={`Jump to ${letter}`}
                onClick={() => document.getElementById(`letter-${letter}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
                className={`w-5 rounded text-[11px] font-semibold leading-[15px] transition ${
                  has ? "text-brand-700 hover:bg-brand-50" : "cursor-default text-slate-400"}`}
              >
                {letter}
              </button>
            );
          })}
        </nav>
      )}
    </Page>
  );
}

function PatientRow({ patient: p }) {
  const initials = `${p.first_name?.[0] ?? ""}${p.last_name?.[0] ?? ""}`.toUpperCase();
  const tone = AVATAR_TONES[hashCode(`${p.first_name}${p.last_name}`) % AVATAR_TONES.length];
  const age = ageFrom(p);

  return (
    <Link
      to={`/patients/${p.id}`}
      className="flex items-center gap-3 px-4 py-3 transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500"
    >
      <span aria-hidden="true" className={`grid h-11 w-11 shrink-0 place-items-center rounded-full text-sm font-semibold ${tone}`}>
        {initials || "?"}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="truncate font-medium text-slate-900">{p.last_name}, {p.first_name}</span>
          {patientNumber(p) && (
            <span className="shrink-0 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
              {patientNumber(p)}
            </span>
          )}
        </span>
        <span className="mt-0.5 block truncate text-sm text-slate-600">
          {p.sex === "M" ? "Male" : "Female"}{age != null && ` · ${age}`}
        </span>
      </span>
      <Icon name="chevronRight" className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
    </Link>
  );
}

// The server works this out from the birthdate in the right unit, so a
// newborn reads "3 days" rather than the "0" a years-only division gives.
function ageFrom(p) {
  return p.age_display ?? null;
}

function hashCode(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) hash = (hash << 5) - hash + str.charCodeAt(i);
  return Math.abs(hash);
}

function groupByLastInitial(patients) {
  return patients.reduce((acc, p) => {
    const letter = (p.last_name || "?")[0].toUpperCase();
    acc[letter] = acc[letter] || [];
    acc[letter].push(p);
    return acc;
  }, {});
}
