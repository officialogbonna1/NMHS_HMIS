import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import { BILLING_CATEGORIES } from "./BillingItemsAdmin.jsx";
import { BillSheet, ReceiptSheet } from "../components/PrintDocuments.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useToast } from "../components/Toaster.jsx";

const CAN_WAIVE_ROLES = ["admin", "hospital_admin", "cashier", "accountant"];

const currency = (n) => new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(n ?? 0);

const STATUS_TONE = {
  unpaid: "bg-red-100 text-red-700",
  partial: "bg-amber-100 text-amber-800",
  paid: "bg-emerald-100 text-emerald-700",
  waived: "bg-slate-100 text-slate-600",
  cancelled: "bg-slate-100 text-slate-400 line-through",
};

export default function PatientBillingTab({ patientId }) {
  // Which sheet is open, and the payment a receipt would be for.
  const [printing, setPrinting] = useState(null);
  const [receipt, setReceipt] = useState(null);

  const { data: patient } = useQuery({
    queryKey: ["patient", String(patientId)],
    queryFn: () => api.get(`/patients/${patientId}/`).then((r) => r.data),
  });
  const { user } = useAuth();
  const canWaive = CAN_WAIVE_ROLES.includes(user?.role);
  const queryClient = useQueryClient();

  const { data: ledgerResults } = useQuery({
    queryKey: ["ledger", patientId],
    queryFn: () => api.get("/ledgers/", { params: { patient: patientId } }).then((r) => r.data.results ?? r.data),
  });
  const ledger = ledgerResults?.[0];

  const { data: charges } = useQuery({
    queryKey: ["charges", "patient", patientId],
    queryFn: () => api.get("/charges/", { params: { patient: patientId } }).then((r) => r.data.results ?? r.data),
  });

  const { data: payments } = useQuery({
    queryKey: ["payments", "patient", patientId],
    queryFn: () => api.get("/payments/", { params: { patient: patientId } }).then((r) => r.data.results ?? r.data),
  });

  const { data: adjustments } = useQuery({
    queryKey: ["adjustments", "patient", patientId],
    queryFn: () => api.get("/adjustments/", { params: { patient: patientId } }).then((r) => r.data.results ?? r.data),
    // Adjustments (waivers/discounts/refunds) are cashier/accountant/admin
    // only on the backend — skip the call entirely for reception rather
    // than firing a request that will always 403.
    enabled: canWaive,
  });

  const invalidateAll = () => {
    ["ledger", "charges", "payments", "adjustments"].forEach((key) =>
      queryClient.invalidateQueries({ queryKey: [key, "patient", patientId] })
    );
    queryClient.invalidateQueries({ queryKey: ["ledger", patientId] });
  };

  const transactions = buildTransactionHistory(charges, payments, adjustments);

  const openCharges = (charges ?? []).filter((c) => ["unpaid", "partial"].includes(c.status));

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap justify-end gap-2">
        <button
          onClick={() => setPrinting("bill")}
          className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          🖨 Print bill
        </button>
        <button
          onClick={() => setPrinting("statement")}
          className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          🖨 Print full statement
        </button>
      </div>

      {printing && patient && (
        <BillSheet
          patient={patient}
          charges={printing === "bill" ? openCharges : (charges ?? [])}
          title={printing === "bill" ? "Invoice" : "Statement of account"}
          onClose={() => setPrinting(null)}
        />
      )}
      {receipt && patient && (
        <ReceiptSheet
          patient={patient}
          payment={receipt}
          balanceAfter={ledger?.outstanding_balance}
          onClose={() => setReceipt(null)}
        />
      )}

      <section className="grid sm:grid-cols-4 gap-3">
        <SummaryCard label="Total charges" value={ledger?.total_charges} />
        <SummaryCard label="Total paid" value={ledger?.total_payments} tone="text-emerald-700" />
        <SummaryCard label="Adjustments" value={ledger?.total_adjustments} />
        <SummaryCard label="Outstanding" value={ledger?.outstanding_balance} tone={Number(ledger?.outstanding_balance) > 0 ? "text-red-700" : "text-emerald-700"} />
      </section>

      <div className="grid md:grid-cols-2 gap-6">
        <BillFromCatalogForm patientId={patientId} onDone={invalidateAll} />
        <RecordPaymentForm patientId={patientId} onDone={invalidateAll} onPaid={setReceipt} />
      </div>

      <AddChargeForm patientId={patientId} onDone={invalidateAll} />

      <section>
        <h2 className="font-medium text-slate-800 mb-3">Transaction history</h2>
        {transactions.length === 0 && <p className="text-slate-600 text-sm">No charges, payments, or adjustments yet.</p>}
        <div className="border rounded-lg divide-y">
          {transactions.map((t) => (
            <TransactionRow key={`${t.kind}-${t.id}`} tx={t} onDone={invalidateAll} canWaive={canWaive} />
          ))}
        </div>
      </section>
    </div>
  );
}

function SummaryCard({ label, value, tone }) {
  return (
    <div className="rounded-xl border bg-white p-4">
      <p className="text-xs text-slate-600">{label}</p>
      <p className={`mt-1 text-lg font-bold ${tone ?? "text-slate-900"}`}>{currency(value)}</p>
    </div>
  );
}

function buildTransactionHistory(charges, payments, adjustments) {
  const rows = [
    ...(charges ?? []).map((c) => ({ kind: "charge", id: c.id, date: c.created_at, label: c.description, amount: c.amount, debit: true, status: c.status, raw: c })),
    ...(payments ?? []).map((p) => ({ kind: "payment", id: p.id, date: p.created_at, label: `Payment (${p.method})`, amount: p.amount, debit: false })),
    ...(adjustments ?? []).map((a) => ({ kind: "adjustment", id: a.id, date: a.created_at, label: `${a.kind[0].toUpperCase()}${a.kind.slice(1)}${a.charge_description ? ` — ${a.charge_description}` : ""}`, amount: a.amount, debit: a.kind === "refund" })),
  ];
  return rows.sort((a, b) => new Date(b.date) - new Date(a.date));
}

function TransactionRow({ tx, onDone, canWaive }) {
  const waive = useMutation({
    mutationFn: (reason) => api.post(`/charges/${tx.id}/waive/`, { reason }),
    onSuccess: onDone,
  });
  const cancelCharge = useMutation({
    mutationFn: () => api.post(`/charges/${tx.id}/cancel/`),
    onSuccess: onDone,
  });

  return (
    <div className="px-4 py-3 flex items-center justify-between gap-4 text-sm">
      <div>
        <div className="flex items-center gap-2">
          <span className="font-medium">{tx.label}</span>
          {tx.status && <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${STATUS_TONE[tx.status]}`}>{tx.status}</span>}
        </div>
        <p className="text-xs text-slate-500 mt-0.5">{new Date(tx.date).toLocaleString()}</p>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <span className={`font-medium ${tx.debit ? "text-red-700" : "text-emerald-700"}`}>
          {tx.debit ? "-" : "+"}{currency(tx.amount)}
        </span>
        {canWaive && tx.kind === "charge" && ["unpaid", "partial"].includes(tx.status) && (
          <>
            <button
              onClick={() => { const reason = prompt("Reason for waiving this charge:"); if (reason) waive.mutate(reason); }}
              className="text-xs text-brand-600 hover:underline"
            >
              Waive
            </button>
            <button
              onClick={() => confirm("Cancel this charge?") && cancelCharge.mutate()}
              className="text-xs text-red-600 hover:underline"
            >
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  );
}


// Everything the hospital charges for, in one place: the priced catalogue
// for anything that has a price, and a write-in for the one-off that does
// not. What is typed here is saved as the charge description, so it reads
// the same on the statement as a catalogued item does.
function BillFromCatalogForm({ patientId, onDone }) {
  const [category, setCategory] = useState("consultation");
  const [itemId, setItemId] = useState("");
  const [customDescription, setCustomDescription] = useState("");
  const [customAmount, setCustomAmount] = useState("");
  const [error, setError] = useState(null);

  const chosen = BILLING_CATEGORIES.find((c) => c.category === category);
  const { data: items } = useQuery({
    queryKey: ["billing-items", category],
    queryFn: () => api.get("/billing-items/", { params: { category, is_active: true, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });

  const priced = items ?? [];
  // Only "Other" is typed in. Everything else is billed at the price the
  // catalogue holds, so the same service costs the same at every window.
  const writeIn = category === "other";

  const bill = useMutation({
    mutationFn: () => {
      if (writeIn) {
        return api.post("/charges/", {
          patient: patientId,
          description: customDescription.trim(),
          amount: customAmount,
          source_type: category,
        });
      }
      const item = priced.find((i) => String(i.id) === itemId);
      return api.post("/charges/", {
        patient: patientId, description: item.name, amount: item.price, source_type: category,
      });
    },
    onSuccess: () => {
      onDone();
      setItemId(""); setCustomDescription(""); setCustomAmount(""); setError(null);
    },
    onError: (err) => setError(readError(err, "Could not bill this item.")),
  });

  const ready = writeIn
    ? customDescription.trim().length > 0 && Number(customAmount) > 0
    : Boolean(itemId);

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (ready) bill.mutate(); }}
      className="bg-white border rounded-xl p-5 space-y-3"
    >
      <h2 className="font-medium text-slate-800">Bill for a service</h2>
      <div>
        <label className="block text-sm font-medium mb-1 text-slate-700">What are you billing for?</label>
        <select
          value={category}
          onChange={(e) => { setCategory(e.target.value); setItemId(""); setError(null); }}
          className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm"
        >
          {BILLING_CATEGORIES.map((c) => (
            <option key={c.category} value={c.category}>{c.label}</option>
          ))}
        </select>
      </div>

      {!writeIn && (
        <div>
          <label className="block text-sm font-medium mb-1 text-slate-700">{chosen?.title ?? "Item"}</label>
          <select
            value={itemId}
            onChange={(e) => setItemId(e.target.value)}
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm"
          >
            <option value="">Select…</option>
            {priced.map((i) => (
              <option key={i.id} value={i.id}>{i.name} — {currency(i.price)}</option>
            ))}
          </select>
          {items && priced.length === 0 && (
            <p className="mt-1 text-sm text-amber-700">
              Nothing priced under {chosen?.label} yet — add it under Billing Catalog.
            </p>
          )}
        </div>
      )}

      {writeIn && (
        <>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">What is it for? *</label>
            <input
              value={customDescription}
              onChange={(e) => setCustomDescription(e.target.value)}
              placeholder="e.g. Medical report, ambulance, dressing pack"
              className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm"
            />
            <p className="mt-1 text-sm text-slate-600">
              This is what the patient sees on their statement, so write it the way you would say it.
            </p>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Amount *</label>
            <input
              type="number" min="0" step="0.01"
              value={customAmount}
              onChange={(e) => setCustomAmount(e.target.value)}
              placeholder="0.00"
              className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm"
            />
          </div>
        </>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        type="submit"
        disabled={!ready || bill.isPending}
        className="bg-brand-600 text-white px-4 py-2 rounded-md text-sm hover:bg-brand-700 disabled:opacity-50"
      >
        {bill.isPending ? "Billing…" : "Bill"}
      </button>
    </form>
  );
}

function AddChargeForm({ patientId, onDone }) {
  const [description, setDescription] = useState("");
  const [amount, setAmount] = useState("");
  const [departmentId, setDepartmentId] = useState("");

  const { data: departments } = useQuery({
    queryKey: ["departments"],
    queryFn: () => api.get("/departments/").then((r) => r.data.results ?? r.data),
  });

  const addCharge = useMutation({
    mutationFn: () => api.post("/charges/", { patient: patientId, description, amount, department: departmentId || null }),
    onSuccess: () => {
      onDone();
      setDescription("");
      setAmount("");
      setDepartmentId("");
    },
  });

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (description && amount) addCharge.mutate(); }}
      className="bg-white border rounded-xl p-5 space-y-3"
    >
      <h2 className="font-medium text-slate-800">Add a charge</h2>
      <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Description" className="w-full border rounded-md px-3 py-2 text-sm" />
      <div className="flex gap-3">
        <input type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Amount" className="w-1/2 border rounded-md px-3 py-2 text-sm" />
        <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)} className="w-1/2 border rounded-md px-3 py-2 text-sm">
          <option value="">No department</option>
          {(departments ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </div>
      {addCharge.isError && <p className="text-xs text-red-600">Could not add this charge.</p>}
      <button type="submit" disabled={!description || !amount || addCharge.isPending} className="bg-brand-600 text-white px-4 py-2 rounded-md text-sm hover:bg-brand-700 disabled:opacity-50">
        {addCharge.isPending ? "Adding…" : "Add charge"}
      </button>
    </form>
  );
}

function RecordPaymentForm({ patientId, onDone, onPaid }) {
  const { showToast } = useToast();
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("cash");
  const [reference, setReference] = useState("");

  const recordPayment = useMutation({
    mutationFn: () => api.post("/payments/", { patient: patientId, amount, method, reference }),
    onSuccess: (response) => {
      onDone();
      showToast({ title: "Payment recorded", message: `${response.data.patient_name} — ${currency(response.data.amount)}` });
      setAmount("");
      setReference("");
      // Offer the receipt straight away — the patient is still at the desk.
      onPaid?.(response.data);
    },
  });

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (amount) recordPayment.mutate(); }}
      className="bg-white border rounded-xl p-5 space-y-3"
    >
      <h2 className="font-medium text-slate-800">Record a payment</h2>
      <div className="flex gap-3">
        <input type="number" min="1" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Amount" className="w-1/2 border rounded-md px-3 py-2 text-sm" />
        <select value={method} onChange={(e) => setMethod(e.target.value)} className="w-1/2 border rounded-md px-3 py-2 text-sm">
          <option value="cash">Cash</option>
          <option value="card">Card</option>
          <option value="transfer">Transfer</option>
          <option value="insurance">Insurance</option>
        </select>
      </div>
      <input value={reference} onChange={(e) => setReference(e.target.value)} placeholder="Reference (optional)" className="w-full border rounded-md px-3 py-2 text-sm" />
      {recordPayment.isError && <p className="text-xs text-red-600">{recordPayment.error?.response?.data?.detail || "Could not record this payment."}</p>}
      <button type="submit" disabled={!amount || recordPayment.isPending} className="bg-brand-600 text-white px-4 py-2 rounded-md text-sm hover:bg-brand-700 disabled:opacity-50">
        {recordPayment.isPending ? "Recording…" : "Record payment"}
      </button>
    </form>
  );
}
