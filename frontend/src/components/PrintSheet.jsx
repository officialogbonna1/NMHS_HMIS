import { createContext, useContext, useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";

// A printable document, shown on screen as a preview and sent to the
// printer as-is. Browser printing rather than a generated PDF: it needs no
// library, works with whatever printer the desk already has, and "Save as
// PDF" is in the same dialog for anyone who wants a file.
//
// Everything outside #print-area is hidden at print time by the rules in
// index.css, so what comes out is the document alone.

// The hospital's own details.
//
// These are now a **database row** (`core.HospitalSettings`), edited on
// Administration → Hospital settings or in Django admin — renaming the
// hospital was a code change and a deployment until then. What is left here
// is the fallback the application starts with before the row has loaded, and
// the shape everything reads.
//
// `useHospital()` is the hook to use in a component. `HOSPITAL` stays as the
// live snapshot for the printed documents, which render inside a component
// that has already fetched it; it is kept in step by `HospitalProvider`.
export const HOSPITAL = {
  name: "NMHS",
  fullName: "Ngozi Maternity and Hospital Services",
  address: "Aba, Abia State",
  phone: "",
};

const HospitalContext = createContext(HOSPITAL);

/**
 * Loads the hospital's details once and keeps `HOSPITAL` in step.
 *
 * Every signed-in user reads this (the header, the login page and every
 * printed document need it), which is why the endpoint is readable by
 * everyone and writable only by an admin.
 */
export function HospitalProvider({ children }) {
  const { data } = useQuery({
    queryKey: ["hospital-settings"],
    queryFn: () => api.get("/hospital-settings/current/").then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });

  const value = useMemo(() => {
    if (!data) return HOSPITAL;
    const loaded = {
      name: data.name || HOSPITAL.name,
      fullName: data.full_name || HOSPITAL.fullName,
      address: data.address || "",
      phone: data.phone || "",
      email: data.email || "",
    };
    // The documents read the module constant directly — they are rendered
    // deep inside print sheets that take no props — so it is updated in place
    // rather than threaded through every one of them.
    Object.assign(HOSPITAL, loaded);
    return loaded;
  }, [data]);

  return <HospitalContext.Provider value={value}>{children}</HospitalContext.Provider>;
}

export function useHospital() {
  return useContext(HospitalContext);
}

export default function PrintSheet({ title, onClose, children }) {
  // Escape closes, and the preview never traps the person behind it.
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 overflow-auto bg-slate-900/50 p-4 print:static print:bg-white print:p-0">
      <div className="mx-auto max-w-3xl">
        <div className="no-print mb-3 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-white px-4 py-3 shadow">
          <p className="font-medium text-slate-800">{title}</p>
          <div className="flex gap-2">
            <button
              onClick={() => window.print()}
              className="rounded-md bg-brand-600 px-5 py-2 text-sm font-medium text-white hover:bg-brand-700"
            >
              🖨 Print
            </button>
            <button
              onClick={onClose}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700"
            >
              Close
            </button>
          </div>
        </div>

        <div id="print-area" className="rounded-xl bg-white p-8 shadow print:rounded-none print:shadow-none">
          {children}
        </div>
      </div>
    </div>
  );
}

export function SheetHeader({ documentTitle, reference, date }) {
  return (
    <header className="mb-6 flex items-start justify-between gap-6 border-b-2 border-slate-800 pb-4">
      <div>
        <p className="text-xl font-bold tracking-tight text-slate-900">{HOSPITAL.fullName}</p>
        <p className="text-sm text-slate-700">{HOSPITAL.address}</p>
        {HOSPITAL.phone && <p className="text-sm text-slate-700">{HOSPITAL.phone}</p>}
      </div>
      <div className="text-right">
        <p className="text-lg font-semibold uppercase tracking-wide text-slate-900">{documentTitle}</p>
        {reference && <p className="text-sm text-slate-700">No. {reference}</p>}
        <p className="text-sm text-slate-700">{date ?? new Date().toLocaleString()}</p>
      </div>
    </header>
  );
}

/**
 * Who this document is about — the one block every printed document opens
 * with, so a sheet that gets separated from its folder can still be put back.
 *
 * Deliberately shape-tolerant. Reception's documents pass the full patient
 * record from `/patients/<id>/`; a department's pass the compact identity
 * its own endpoint returns (`{name, file_number, sex, age}`) rather than
 * refetching the patient to print one line. Both read the same here, which
 * is what stops the name being formatted two ways on two sheets.
 *
 * The address row appears only for the documents that carry an address at
 * all: a laboratory request form does not need one, and an empty labelled
 * row on a form invites somebody to write in it.
 */
export function PatientBlock({ patient }) {
  if (!patient) return null;
  const name = patient.name ?? `${patient.last_name}, ${patient.first_name}`;
  const age = patient.age ?? patient.age_display;
  // Either the joined string the chart's overview sends, or the parts as the
  // patient record holds them.
  const address = patient.address ?? [patient.street_address, patient.city,
                                      patient.state, patient.country]
    .filter(Boolean).join(", ");
  const hasAddress = ["address", "street_address", "city", "state", "country"]
    .some((key) => key in patient);
  return (
    <section className="mb-6 grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
      <Field label="Patient" value={name} strong />
      <Field label="File number" value={patient.file_number} strong />
      <Field label="Sex" value={patient.sex_display ?? patient.sex ?? "—"} />
      {/* Age as well as the date: a card for a newborn is read at a glance. */}
      <Field
        label="Age"
        value={[age, patient.birthdate && `b. ${patient.birthdate}`]
          .filter(Boolean).join(" · ") || "—"}
      />
      <Field label="Phone" value={patient.phone_number || "—"} />
      {hasAddress && <Field label="Address" value={address || "—"} />}
    </section>
  );
}

/**
 * A heading over one block of a document. Printed documents are read by
 * somebody scanning for one thing — what was asked for, what was found —
 * so every block says which it is.
 */
export function SheetSection({ title, note, className = "", children }) {
  return (
    <section className={`mb-6 break-inside-avoid ${className}`}>
      {title && (
        <h2 className="mb-2 border-b-2 border-slate-800 pb-1 text-sm font-bold uppercase tracking-wide text-slate-900">
          {title}
          {note && (
            <span className="ml-2 font-normal normal-case tracking-normal text-slate-700">
              {note}
            </span>
          )}
        </h2>
      )}
      {children}
    </section>
  );
}

/**
 * The band across the top of a document that is not the finished thing —
 * "Provisional", "Not yet dispensed". A word, not a colour: a coloured
 * panel is either dropped by the driver or burns toner.
 */
export function Stamp({ children }) {
  return (
    <p className="mb-5 border-2 border-slate-800 px-4 py-2 text-center text-sm font-bold uppercase tracking-[0.2em] text-slate-900">
      {children}
    </p>
  );
}

/**
 * Ruled lines for what the printed form exists to have written on it — the
 * bench's readings, the ward's instructions. A form with no space to write
 * is a form somebody turns over.
 */
export function WriteInLines({ rows = 4, label }) {
  return (
    <div>
      {label && <p className="mb-2 text-sm text-slate-700">{label}</p>}
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="mt-4 border-b border-slate-400" />
      ))}
    </div>
  );
}

/**
 * The preview while a self-fetching document loads, and what it says when
 * the fetch fails. A document that renders half its data is worse than one
 * that says it could not load: half a result is read as the whole result.
 */
export function SheetStatus({ title, isError, message, onClose }) {
  return (
    <PrintSheet title={title} onClose={onClose}>
      <p className="text-slate-700">
        {isError
          ? (message ?? "Could not load this document. Close and try again.")
          : "Loading…"}
      </p>
    </PrintSheet>
  );
}

export function Field({ label, value, strong }) {
  return (
    <p className="flex gap-2">
      <span className="w-32 shrink-0 text-slate-700">{label}:</span>
      <span className={strong ? "font-semibold text-slate-900" : "text-slate-900"}>{value}</span>
    </p>
  );
}

export function SheetFooter({ note, signatory = "Signature" }) {
  return (
    <footer className="mt-10 border-t border-slate-300 pt-4 text-sm text-slate-700">
      {note && <p className="mb-6">{note}</p>}
      <div className="flex justify-between gap-8">
        <div className="w-56 border-t border-slate-500 pt-1">{signatory}</div>
        <div className="w-56 border-t border-slate-500 pt-1 text-right">Date</div>
      </div>
    </footer>
  );
}

export const money = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", minimumFractionDigits: 2 })
    .format(Number(n ?? 0));
