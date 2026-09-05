import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import PrintSheet, { SheetHeader, PatientBlock, SheetFooter, SheetStatus, Field, money, HOSPITAL }
  from "./PrintSheet.jsx";

// The three things the front desk hands a patient: the card they bring
// back every visit, the bill for what they owe, and the receipt for what
// they have paid.

export function PatientCardSheet({ patient, onClose }) {
  return (
    <PrintSheet title={`Patient card — ${patient.file_number}`} onClose={onClose}>
      <SheetHeader documentTitle="Patient Registration" reference={patient.file_number}
                   date={patient.created_at ? new Date(patient.created_at).toLocaleString() : undefined} />

      {/* The file number is the whole point of the slip, so it is the
          largest thing on it — a patient reading it out over the phone
          should not have to hunt. */}
      <div className="mb-6 rounded-lg border-2 border-slate-800 px-6 py-4 text-center">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-700">File number</p>
        <p className="mt-1 text-3xl font-bold tracking-widest text-slate-900">{patient.file_number}</p>
      </div>

      <PatientBlock patient={patient} />

      {patient.emergency_contact_name && (
        <section className="mb-6 rounded-lg border border-slate-400 px-4 py-3">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-700">
            In an emergency, contact
          </p>
          <p className="mt-1 font-semibold text-slate-900">
            {patient.emergency_contact_name}
            {patient.emergency_contact_relationship && ` (${patient.emergency_contact_relationship})`}
          </p>
          <p className="text-sm text-slate-900">
            {[patient.emergency_contact_phone, patient.emergency_contact_alt_phone]
              .filter(Boolean).join(" · ")}
          </p>
          {patient.emergency_contact_address && (
            <p className="text-sm text-slate-800">{patient.emergency_contact_address}</p>
          )}
        </section>
      )}

      {patient.short_note && (
        <section className="mb-6">
          <p className="text-sm font-semibold text-slate-900">Note</p>
          <p className="text-sm text-slate-800">{patient.short_note}</p>
        </section>
      )}

      <SheetFooter
        signatory="Registered by"
        note={`Please bring this card, or quote the file number above, on every visit to ${HOSPITAL.name}.`}
      />
    </PrintSheet>
  );
}

export function BillSheet({ patient, charges, onClose, title = "Invoice" }) {
  const rows = charges ?? [];
  const total = rows.reduce((sum, c) => sum + Number(c.amount ?? 0), 0);
  const paid = rows.reduce((sum, c) => sum + Number(c.amount_paid ?? 0), 0);
  const discounted = rows.reduce((sum, c) => sum + Number(c.amount_discounted ?? 0), 0);
  const due = total - paid - discounted;

  return (
    <PrintSheet title={`${title} — ${patient.file_number}`} onClose={onClose}>
      <SheetHeader documentTitle={title} reference={patient.file_number} />
      <PatientBlock patient={patient} />

      <table className="mb-4 w-full text-sm">
        <thead>
          <tr className="border-y border-slate-400 text-left">
            <th className="py-2">Date</th>
            <th className="py-2">Description</th>
            <th className="py-2 text-right">Amount</th>
            <th className="py-2 text-right">Paid</th>
            <th className="py-2 text-right">Balance</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} className="border-b border-slate-200">
              <td className="py-2 align-top">{new Date(c.created_at).toLocaleDateString()}</td>
              <td className="py-2 align-top">
                {c.description}
                {Number(c.amount_discounted) > 0 && (
                  <span className="block text-xs text-slate-700">
                    less {money(c.amount_discounted)} discount
                  </span>
                )}
              </td>
              <td className="py-2 text-right align-top">{money(c.amount)}</td>
              <td className="py-2 text-right align-top">{money(c.amount_paid)}</td>
              <td className="py-2 text-right align-top font-medium">
                {money(Number(c.amount) - Number(c.amount_paid) - Number(c.amount_discounted))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={5} className="py-4 text-center text-slate-700">No charges.</td></tr>
          )}
        </tbody>
      </table>

      <section className="keep-together ml-auto w-72 text-sm">
        <Totals label="Total charged" value={total} />
        {discounted > 0 && <Totals label="Discount" value={-discounted} />}
        <Totals label="Paid" value={-paid} />
        <div className="mt-1 flex justify-between border-t-2 border-slate-800 pt-2 text-base font-bold">
          <span>Balance due</span>
          <span>{money(due)}</span>
        </div>
      </section>

      <SheetFooter
        signatory="Billed by"
        note={due > 0 ? "Please settle the balance at the cash desk." : "Nothing further is owed on this invoice."}
      />
    </PrintSheet>
  );
}

/**
 * The bill, fetched rather than handed in — for the places that print one
 * without already holding the charges: the chart's Billing tab header and
 * the print menu behind it.
 *
 * `mode` is the difference between the two money documents the desk uses.
 * An **invoice** is what is still owed, so it lists the open charges only; a
 * **statement** is the whole account, settled rows included. Printing an
 * invoice that lists last month's paid charges is how a patient is asked to
 * pay twice.
 */
export function PatientBillSheet({ patientId, mode = "invoice", onClose }) {
  const patientQuery = useQuery({
    queryKey: ["patient", String(patientId)],
    queryFn: () => api.get(`/patients/${patientId}/`).then((r) => r.data),
  });
  const chargesQuery = useQuery({
    queryKey: ["charges", "patient", patientId],
    queryFn: () => api.get("/charges/", { params: { patient: patientId, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });

  const title = mode === "statement" ? "Statement" : "Invoice";
  if (patientQuery.isLoading || chargesQuery.isLoading || patientQuery.isError || chargesQuery.isError) {
    return (
      <SheetStatus
        title={title}
        isError={patientQuery.isError || chargesQuery.isError}
        onClose={onClose}
      />
    );
  }

  const all = chargesQuery.data ?? [];
  const rows = mode === "statement"
    ? all
    : all.filter((charge) => ["unpaid", "partial"].includes(charge.status));

  return (
    <BillSheet patient={patientQuery.data} charges={rows} title={title} onClose={onClose} />
  );
}

export function ReceiptSheet({ patient, payment, balanceAfter, onClose }) {
  return (
    <PrintSheet title={`Receipt — ${patient.file_number}`} onClose={onClose}>
      <SheetHeader documentTitle="Receipt" reference={payment?.id ? `R-${String(payment.id).padStart(6, "0")}` : undefined}
                   date={payment?.created_at ? new Date(payment.created_at).toLocaleString() : undefined} />
      <PatientBlock patient={patient} />

      <div className="mb-6 rounded-lg border-2 border-slate-800 px-6 py-4 text-center">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-700">Amount received</p>
        <p className="mt-1 text-3xl font-bold text-slate-900">{money(payment?.amount)}</p>
        <p className="mt-1 text-sm text-slate-800">{amountInWords(payment?.amount)}</p>
      </div>

      <section className="mb-6 grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
        <Field label="Paid by" value={METHOD[payment?.method] ?? payment?.method ?? "—"} />
        <Field label="Received at" value={CHANNEL[payment?.channel] ?? payment?.channel ?? "—"} />
        {payment?.reference && <Field label="Reference" value={payment.reference} />}
        {payment?.received_by_name && <Field label="Received by" value={payment.received_by_name} />}
      </section>

      {balanceAfter != null && (
        <section className="keep-together ml-auto w-72 text-sm">
          <div className="flex justify-between border-t-2 border-slate-800 pt-2 text-base font-bold">
            <span>{Number(balanceAfter) > 0 ? "Balance still owing" : "Balance"}</span>
            <span>{money(balanceAfter)}</span>
          </div>
        </section>
      )}

      <SheetFooter
        signatory="Received by"
        note="Thank you. Please keep this receipt — it is your proof of payment."
      />
    </PrintSheet>
  );
}

function Totals({ label, value }) {
  return (
    <div className="flex justify-between py-0.5">
      <span className="text-slate-700">{label}</span>
      <span>{money(value)}</span>
    </div>
  );
}

const METHOD = { cash: "Cash", card: "Card", transfer: "Bank transfer", insurance: "Insurance" };
const CHANNEL = { front_desk: "Front desk", pharmacy: "Pharmacy", cashier: "Cash desk" };

// Naira in words, for the line a receipt is expected to carry. Whole naira
// only — kobo is on the figure above it.
function amountInWords(amount) {
  const naira = Math.floor(Number(amount ?? 0));
  if (!naira) return "";
  return `${capitalise(inWords(naira))} naira only`;
}

const ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
  "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
const TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];

function inWords(n) {
  if (n < 20) return ONES[n];
  if (n < 100) return TENS[Math.floor(n / 10)] + (n % 10 ? `-${ONES[n % 10]}` : "");
  if (n < 1000) return `${ONES[Math.floor(n / 100)]} hundred${n % 100 ? ` and ${inWords(n % 100)}` : ""}`;
  for (const [value, name] of [[1e9, "billion"], [1e6, "million"], [1e3, "thousand"]]) {
    if (n >= value) {
      return `${inWords(Math.floor(n / value))} ${name}${n % value ? ` ${inWords(n % value)}` : ""}`;
    }
  }
  return String(n);
}

function capitalise(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
