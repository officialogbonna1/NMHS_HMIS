import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";
import { patientNumber } from "../components/patientIdentity.js";
import { TextLink, Page, PageHeader } from "../components/ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { PrintButton } from "../components/printing.jsx";
import PatientPicker from "../components/PatientPicker.jsx";

// A patient's full financial statement: every charge, payment, discount,
// waiver and refund on one timeline, with the balance as it stood after each
// one. Read-only on purpose — billing actions live on the Billing counter,
// this is the page you open when someone asks "what am I actually paying
// for?"
const currency = (n) => new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(n ?? 0);

const KIND_STYLE = {
  charge: { label: "Charge", tone: "bg-red-50 text-red-700 border-red-200", sign: "+" },
  payment: { label: "Payment", tone: "bg-emerald-50 text-emerald-700 border-emerald-200", sign: "−" },
  discount: { label: "Discount", tone: "bg-violet-50 text-violet-700 border-violet-200", sign: "−" },
  waiver: { label: "Waiver", tone: "bg-slate-50 text-slate-600 border-slate-200", sign: "−" },
  refund: { label: "Refund", tone: "bg-amber-50 text-amber-800 border-amber-200", sign: "+" },
};

const FILTERS = [
  ["all", "Everything"],
  ["charge", "Charges"],
  ["payment", "Payments"],
  ["credit", "Discounts & waivers"],
];

export default function TransactionHistory() {
  const [patient, setPatient] = useState(null);

  return (
    <Page width="wide" className="space-y-6">
      <PageHeader
        className="mb-0"
        icon="receipt"
        title="Transaction history"
        subtitle="Every charge, payment, discount, waiver and refund on one timeline."
      />

      <ChooseWhoseHistory patient={patient} onPick={setPatient} onClear={() => setPatient(null)} />

      {patient ? (
        <Statement patient={patient} />
      ) : (
        <RecentlyBilled onPick={setPatient} />
      )}
    </Page>
  );
}

function ChooseWhoseHistory({ patient, onPick, onClear }) {
  if (patient) {
    return (
      <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-white px-5 py-4">
        <div>
          <p className="font-semibold text-slate-800">{patient.last_name}, {patient.first_name}</p>
          <p className="text-sm text-slate-600">{patientNumber(patient)}</p>
        </div>
        <button onClick={onClear} className="rounded-lg border px-3 py-2 text-sm hover:bg-slate-50">
          Different patient
        </button>
      </section>
    );
  }

  return (
    <section className="rounded-xl border bg-white p-5">
      <label className="mb-1 block text-sm font-medium">Whose history do you need?</label>
      <PatientPicker
        value={null}
        onChange={(p) => p && onPick(p)}
        autoFocus
        placeholder="Search by name or file number, or pick from the list…"
        emptyMessage="No patient matches that."
      />
    </section>
  );
}

function Statement({ patient }) {
  const { user } = useAuth();
  const [filter, setFilter] = useState("all");

  const ledgerQuery = useQuery({
    queryKey: ["ledger", patient.id],
    queryFn: () => api.get("/ledgers/", { params: { patient: patient.id } }).then((r) => r.data.results ?? r.data),
  });
  const chargesQuery = useQuery({
    queryKey: ["charges", "patient", patient.id],
    queryFn: () => api.get("/charges/", { params: { patient: patient.id } }).then((r) => r.data.results ?? r.data),
  });
  const paymentsQuery = useQuery({
    queryKey: ["payments", "patient", patient.id],
    queryFn: () => api.get("/payments/", { params: { patient: patient.id } }).then((r) => r.data.results ?? r.data),
  });
  const adjustmentsQuery = useQuery({
    queryKey: ["adjustments", "patient", patient.id],
    queryFn: () => api.get("/adjustments/", { params: { patient: patient.id } }).then((r) => r.data.results ?? r.data),
  });

  const isLoading = [ledgerQuery, chargesQuery, paymentsQuery, adjustmentsQuery].some((q) => q.isLoading);
  const ledger = ledgerQuery.data?.[0];

  const rows = useMemo(
    () => buildStatement(chargesQuery.data, paymentsQuery.data, adjustmentsQuery.data),
    [chargesQuery.data, paymentsQuery.data, adjustmentsQuery.data],
  );

  const visible = rows.filter((r) =>
    filter === "all" ? true : filter === "credit" ? ["discount", "waiver"].includes(r.kind) : r.kind === filter,
  );

  if (isLoading) return <p className="text-sm text-slate-500">Loading the statement…</p>;

  const discountTotal = rows.filter((r) => r.kind === "discount").reduce((sum, r) => sum + r.amount, 0);

  return (
    <div className="space-y-6">
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Summary label="Total charged" value={ledger?.total_charges} />
        <Summary label="Total paid" value={ledger?.total_payments} tone="text-emerald-700" />
        <Summary label="Discounts & waivers" value={ledger?.total_adjustments} tone="text-violet-700"
                 note={discountTotal > 0 ? `${currency(discountTotal)} of it discounts` : null} />
        <Summary
          label="Still owing"
          value={ledger?.outstanding_balance}
          tone={Number(ledger?.outstanding_balance) > 0 ? "text-red-700" : "text-emerald-700"}
        />
      </section>

      <section className="overflow-hidden rounded-xl border bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
          <h2 className="font-semibold">Statement</h2>
          <div className="flex flex-wrap items-center gap-2">
            {/* The account on paper: the whole statement here, and the
                invoice — what is still owed — behind the caret, because a
                patient asking "what do I owe?" wants the second one. */}
            <PrintButton
              role={user?.role}
              size="sm"
              documents={["statement", "invoice"]}
              context={{ patientId: patient.id, patient }}
            />
            <div className="flex overflow-hidden rounded-lg border text-sm">
              {FILTERS.map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setFilter(value)}
                  className={`px-3 py-1.5 ${filter === value ? "bg-brand-600 text-white" : "hover:bg-slate-50"}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {visible.length === 0 && (
          <p className="p-8 text-center text-sm text-slate-600">
            {rows.length === 0 ? "Nothing has been billed to this patient yet." : "Nothing of that kind on this account."}
          </p>
        )}

        <div className="divide-y">
          {visible.map((row) => {
            const style = KIND_STYLE[row.kind];
            return (
              <div key={`${row.kind}-${row.id}`} className="flex items-start justify-between gap-4 px-5 py-3.5">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${style.tone}`}>
                      {style.label}
                    </span>
                    <span className="font-medium">{row.label}</span>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-600">
                    {new Date(row.date).toLocaleString()}
                    {row.by && ` · ${row.by}`}
                    {row.detail && ` · ${row.detail}`}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <p className={`font-semibold ${row.debit ? "text-red-700" : "text-emerald-700"}`}>
                    {style.sign}{currency(row.amount)}
                  </p>
                  <p className="text-xs text-slate-600">balance {currency(row.balanceAfter)}</p>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <p className="text-xs text-slate-500">
        Need to bill or take money? Use the <TextLink to="/billing">Billing counter</TextLink>.
      </p>
    </div>
  );
}

function Summary({ label, value, tone, note }) {
  return (
    <div className="rounded-xl border bg-white p-4">
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`mt-1 text-lg font-bold ${tone ?? "text-slate-900"}`}>{currency(value)}</p>
      {note && <p className="mt-0.5 text-xs text-slate-600">{note}</p>}
    </div>
  );
}

// Oldest first to accumulate the running balance the way the ledger does
// (charges and refunds add, payments and credits subtract), then flipped so
// the newest entry reads first.
function buildStatement(charges, payments, adjustments) {
  const rows = [
    ...(charges ?? [])
      .filter((c) => c.status !== "cancelled")
      .map((c) => ({
        kind: "charge", id: c.id, date: c.created_at, label: c.description,
        amount: Number(c.amount), debit: true,
        detail: c.department_name || null,
      })),
    ...(payments ?? []).map((p) => ({
      kind: "payment", id: p.id, date: p.created_at,
      label: `Payment — ${p.method}`, amount: Number(p.amount), debit: false,
      by: p.received_by_name,
      detail: p.channel === "pharmacy" ? "pharmacy counter" : p.reference || null,
    })),
    ...(adjustments ?? []).map((a) => ({
      kind: a.kind, id: a.id, date: a.created_at,
      label: a.charge_description ? `${a.kind_label ?? a.kind} — ${a.charge_description}` : (a.kind_label ?? a.kind),
      amount: Number(a.amount), debit: a.kind === "refund",
      by: a.approved_by_name, detail: a.reason,
    })),
  ].sort((a, b) => new Date(a.date) - new Date(b.date));

  let balance = 0;
  for (const row of rows) {
    balance += row.debit ? row.amount : -row.amount;
    row.balanceAfter = balance;
  }
  return rows.reverse();
}

function RecentlyBilled({ onPick }) {
  const { data: ledgers, isLoading } = useQuery({
    queryKey: ["ledgers", "all"],
    queryFn: () => api.get("/ledgers/").then((r) => r.data.results ?? r.data),
  });

  const withActivity = (ledgers ?? []).filter((l) => Number(l.total_charges) > 0);

  return (
    <section className="overflow-hidden rounded-xl border bg-white">
      <div className="border-b px-5 py-4">
        <h2 className="font-semibold">Or pick from patients with an account</h2>
      </div>
      {isLoading && <p className="p-5 text-sm text-slate-500">Loading…</p>}
      {!isLoading && withActivity.length === 0 && (
        <p className="p-5 text-sm text-slate-600">Nobody has been billed yet.</p>
      )}
      <div className="divide-y">
        {withActivity.map((l) => (
          <button
            key={l.id}
            onClick={() => onPick(patientFromLedger(l))}
            className="flex w-full items-center justify-between px-5 py-3 text-left text-sm hover:bg-slate-50"
          >
            <span className="font-medium">{l.patient_name}</span>
            <span className="flex items-center gap-4 text-xs text-slate-600">
              <span>{currency(l.total_charges)} charged</span>
              <span className={Number(l.outstanding_balance) > 0 ? "font-semibold text-red-700" : "font-semibold text-emerald-700"}>
                {currency(l.outstanding_balance)} owing
              </span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

// Ledger rows carry "Last, First", the patient id and the hospital number —
// enough to drive the statement without a second lookup.
function patientFromLedger(ledger) {
  const [last = "", first = ""] = (ledger.patient_name ?? "").split(",");
  return { id: ledger.patient, last_name: last.trim(), first_name: first.trim(),
           patient_number: ledger.patient_number };
}
