import { Link } from "react-router-dom";

import { Icon } from "./icons.jsx";
import { patientNumber, patientUuidOf } from "./patientIdentity.js";

// **How a list of patients looks in this application**, in one place.
//
// The reference manual's "Your Patients" screen: an avatar, the name as
// "Last, First", the hospital number as a chip, one line of context under it,
// grouped under the initial of the surname with a letter rail down the side.
// Reception's `PatientsList` is where it was written and the maternity desk is
// the second screen to need it — so it moved here rather than being spelled a
// second time, the same reason `StockPanels.jsx` serves both stock workspaces.
//
// The one thing a caller varies is **what a row does**: reception's rows are
// links into the chart, maternity's are options in a listbox that choose the
// mother whose record loads below. Everything a reader sees is the same.

export const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");

const AVATAR_TONES = [
  "bg-blue-100 text-blue-700", "bg-violet-100 text-violet-700",
  "bg-emerald-100 text-emerald-700", "bg-amber-100 text-amber-800",
  "bg-rose-100 text-rose-700", "bg-brand-100 text-brand-700",
  "bg-cyan-100 text-cyan-700", "bg-fuchsia-100 text-fuchsia-700",
];

function hashCode(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) hash = (hash << 5) - hash + str.charCodeAt(i);
  return Math.abs(hash);
}

/** The server works the age out in the right unit, so a newborn reads "3 days". */
export function ageFrom(patient) {
  return patient.age_display ?? patient.age ?? null;
}

export function groupByLastInitial(patients) {
  return patients.reduce((acc, p) => {
    const letter = (p.last_name || "?")[0].toUpperCase();
    acc[letter] = acc[letter] || [];
    acc[letter].push(p);
    return acc;
  }, {});
}

/** Sex and age — the default line under a name. */
export function demographics(patient) {
  const age = ageFrom(patient);
  const sex = patient.sex === "M" ? "Male" : patient.sex === "F" ? "Female" : null;
  return [sex, age].filter(Boolean).join(" · ");
}

export function PatientAvatar({ patient }) {
  const initials = `${patient.first_name?.[0] ?? ""}${patient.last_name?.[0] ?? ""}`
    .toUpperCase();
  const tone = AVATAR_TONES[
    hashCode(`${patient.first_name}${patient.last_name}`) % AVATAR_TONES.length];
  return (
    <span aria-hidden="true"
          className={`grid h-11 w-11 shrink-0 place-items-center rounded-full text-sm font-semibold ${tone}`}>
      {initials || "?"}
    </span>
  );
}

const ROW_CLASS = "flex w-full items-center gap-3 px-4 py-3 text-left transition "
  + "hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 "
  + "focus-visible:ring-inset focus-visible:ring-brand-500";

/**
 * One patient, as a row.
 *
 * `to` makes it a link (reception, opening the chart); `onSelect` makes it a
 * button (the maternity desk, choosing whose record to load). `role` is passed
 * through so a row inside a listbox is an option, which is what a keyboard
 * and a screen reader need.
 */
export function PatientListRow({
  patient, to, onSelect, role, selected, highlighted = false, onMouseEnter,
  secondary,
}) {
  const body = (
    <>
      <PatientAvatar patient={patient} />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="truncate font-medium text-slate-900">
            {patient.last_name}, {patient.first_name}
          </span>
          {patientNumber(patient) && (
            <span className="shrink-0 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
              {patientNumber(patient)}
            </span>
          )}
        </span>
        <span className="mt-0.5 block truncate text-sm text-slate-600">
          {secondary ?? demographics(patient)}
        </span>
      </span>
      <Icon name="chevronRight" className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
    </>
  );

  if (to) {
    return <Link to={to} className={ROW_CLASS}>{body}</Link>;
  }
  return (
    <button
      type="button"
      {...(role ? { role, "aria-selected": Boolean(selected) } : {})}
      onClick={onSelect}
      onMouseEnter={onMouseEnter}
      className={`${ROW_CLASS} ${highlighted ? "bg-brand-50" : ""}`}
    >
      {body}
    </button>
  );
}

/** Reception's row: a link into the chart, addressed by the patient's UUID. */
export function PatientLinkRow({ patient }) {
  return <PatientListRow patient={patient} to={`/patients/${patientUuidOf(patient)}`} />;
}

/**
 * The list itself: a section per surname initial, each headed by its letter.
 *
 * `renderRow` is given one patient and its index across the whole list, so a
 * caller keeping a highlighted option can tell which row it is on.
 */
export function PatientSections({ patients, renderRow, listProps }) {
  const grouped = groupByLastInitial(patients);
  let index = -1;
  return (
    <div className="space-y-5" {...(listProps ?? {})}>
      {Object.keys(grouped).sort().map((letter) => (
        <section key={letter} id={`letter-${letter}`}
                 aria-labelledby={`heading-${letter}`} className="scroll-mt-20">
          <h2 id={`heading-${letter}`}
              className="mb-1.5 px-1 text-xs font-bold uppercase tracking-wider text-slate-600">
            {letter}
          </h2>
          <div className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            {grouped[letter].map((patient) => {
              index += 1;
              return renderRow(patient, index);
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

/**
 * The A–Z rail down the side.
 *
 * A pointer convenience — a thumb scrolls — so it is hidden below `sm`, and a
 * letter nobody is filed under is dimmed rather than removed, because a rail
 * that changed length as you typed would be impossible to aim at.
 */
export function LetterRail({ patients }) {
  const present = new Set(Object.keys(groupByLastInitial(patients)));
  if (present.size === 0) return null;
  return (
    <nav aria-label="Jump to letter"
         className="fixed right-2 top-1/2 z-10 hidden -translate-y-1/2 flex-col items-center rounded-full border border-slate-200 bg-white/90 px-0.5 py-2 shadow-sm backdrop-blur sm:flex">
      {ALPHABET.map((letter) => {
        const has = present.has(letter);
        return (
          <button
            key={letter} type="button" disabled={!has}
            aria-label={`Jump to ${letter}`}
            onClick={() => document.getElementById(`letter-${letter}`)
              ?.scrollIntoView({ behavior: "smooth", block: "start" })}
            className={`w-5 rounded text-[11px] font-semibold leading-[15px] transition ${
              has ? "text-brand-700 hover:bg-brand-50" : "cursor-default text-slate-400"}`}
          >
            {letter}
          </button>
        );
      })}
    </nav>
  );
}
