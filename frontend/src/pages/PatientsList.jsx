import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";
import { useAuth } from "../auth/AuthContext.jsx";

const CAN_REGISTER_ROLES = ["reception", "admin", "hospital_admin"];
const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
const AVATAR_TONES = [
  "from-blue-400 to-blue-600",
  "from-violet-400 to-violet-600",
  "from-emerald-400 to-emerald-600",
  "from-amber-400 to-amber-600",
  "from-rose-400 to-rose-600",
  "from-brand-400 to-brand-600",
  "from-cyan-400 to-cyan-600",
  "from-fuchsia-400 to-fuchsia-600",
];

// Mirrors the reference manual's "Your Patients" screen: alphabetical
// listing, live search by name, letter jump.
export default function PatientsList() {
  const { user } = useAuth();
  const [search, setSearch] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["patients", search],
    queryFn: () => api.get("/patients/", { params: { search } }).then((r) => r.data.results ?? r.data),
  });

  const grouped = groupByLastInitial(data ?? []);
  const activeLetters = new Set(Object.keys(grouped));

  return (
    <div className="max-w-3xl mx-auto p-6 pr-14 sm:pr-16">
      <div className="flex items-center justify-between mb-1">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Patients</h1>
          <p className="text-sm text-slate-600 mt-0.5">{data ? `${data.length} patient${data.length === 1 ? "" : "s"}` : " "}</p>
        </div>
        {CAN_REGISTER_ROLES.includes(user?.role) && (
          <Link
            to="/patients/new"
            className="flex items-center gap-1.5 rounded-full bg-brand-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm shadow-brand-600/20 transition hover:bg-brand-700 hover:shadow-md"
          >
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4"><path d="M10 4a1 1 0 011 1v4h4a1 1 0 110 2h-4v4a1 1 0 11-2 0v-4H5a1 1 0 110-2h4V5a1 1 0 011-1z" /></svg>
            Add Patient
          </Link>
        )}
      </div>

      <div className="relative mt-5 mb-6">
        <svg viewBox="0 0 20 20" fill="currentColor" className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400">
          <path fillRule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.45 4.39l3.08 3.08a1 1 0 01-1.42 1.42l-3.08-3.08A7 7 0 012 9z" clipRule="evenodd" />
        </svg>
        <input
          className="w-full rounded-full border border-slate-200 bg-white py-2.5 pl-10 pr-4 text-sm shadow-sm outline-none transition focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
          placeholder="Search by name or file number (e.g. NMHS-000001)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {isLoading && (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-[68px] animate-pulse rounded-2xl bg-slate-100" />
          ))}
        </div>
      )}

      {!isLoading && (data ?? []).length === 0 && (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white py-14 text-center">
          <p className="text-sm text-slate-500">{search ? "No patients match your search." : "No patients registered yet."}</p>
        </div>
      )}

      <div className="space-y-6">
        {Object.keys(grouped).sort().map((letter) => (
          <div key={letter} id={`letter-${letter}`} className="scroll-mt-4">
            <div className="mb-2 px-1 text-xs font-bold uppercase tracking-wider text-slate-600">{letter}</div>
            <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-sm divide-y divide-slate-100">
              {grouped[letter].map((p) => (
                <PatientRow key={p.id} patient={p} />
              ))}
            </div>
          </div>
        ))}
      </div>

      {activeLetters.size > 0 && (
        <nav className="fixed right-1.5 top-1/2 z-10 hidden -translate-y-1/2 flex-col items-center gap-[1px] rounded-full bg-white/70 px-1 py-2 text-[10px] font-semibold text-slate-300 shadow-sm backdrop-blur sm:flex">
          {ALPHABET.map((letter) => (
            <button
              key={letter}
              type="button"
              disabled={!activeLetters.has(letter)}
              onClick={() => document.getElementById(`letter-${letter}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
              className={activeLetters.has(letter) ? "px-1 text-brand-600 hover:scale-125 transition" : "px-1 text-slate-200"}
            >
              {letter}
            </button>
          ))}
        </nav>
      )}
    </div>
  );
}

function PatientRow({ patient: p }) {
  const initials = `${p.first_name?.[0] ?? ""}${p.last_name?.[0] ?? ""}`.toUpperCase();
  const tone = AVATAR_TONES[hashCode(`${p.first_name}${p.last_name}`) % AVATAR_TONES.length];
  const age = ageFrom(p);

  return (
    <Link to={`/patients/${p.id}`} className="flex items-center gap-3 px-4 py-3.5 transition hover:bg-slate-50">
      <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-full bg-gradient-to-br ${tone} text-sm font-semibold text-white shadow-sm`}>
        {initials || "?"}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate font-medium text-slate-900">{p.last_name}, {p.first_name}</p>
          {p.file_number && (
            <span className="shrink-0 rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-medium text-brand-700">{p.file_number}</span>
          )}
        </div>
        <p className="mt-0.5 truncate text-xs text-slate-600">
          {p.sex === "M" ? "Male" : "Female"}{age != null && ` · ${age} yrs`}
        </p>
      </div>
      <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4 shrink-0 text-slate-300">
        <path fillRule="evenodd" d="M7.3 14.7a1 1 0 010-1.4L10.6 10 7.3 6.7a1 1 0 011.4-1.4l4 4a1 1 0 010 1.4l-4 4a1 1 0 01-1.4 0z" clipRule="evenodd" />
      </svg>
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
