import { useEffect } from "react";

// A printable document, shown on screen as a preview and sent to the
// printer as-is. Browser printing rather than a generated PDF: it needs no
// library, works with whatever printer the desk already has, and "Save as
// PDF" is in the same dialog for anyone who wants a file.
//
// Everything outside #print-area is hidden at print time by the rules in
// index.css, so what comes out is the document alone.

// The hospital's own details. Edit here and every printed document, the
// login page and the app header follow — nothing else hardcodes the name.
export const HOSPITAL = {
  name: "NMHS",
  fullName: "Ngozi Maternity and Hospital Services",
  address: "Aba, Abia State",
  phone: "",
};

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

export function PatientBlock({ patient }) {
  if (!patient) return null;
  return (
    <section className="mb-6 grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
      <Field label="Patient" value={`${patient.last_name}, ${patient.first_name}`} strong />
      <Field label="File number" value={patient.file_number} strong />
      <Field label="Sex" value={patient.sex_display ?? patient.sex} />
      {/* Age as well as the date: a card for a newborn is read at a glance. */}
      <Field
        label="Age"
        value={[patient.age_display, patient.birthdate && `b. ${patient.birthdate}`]
          .filter(Boolean).join(" · ") || "—"}
      />
      <Field label="Phone" value={patient.phone_number || "—"} />
      <Field
        label="Address"
        value={[patient.street_address, patient.city, patient.state, patient.country]
          .filter(Boolean).join(", ") || "—"}
      />
    </section>
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
