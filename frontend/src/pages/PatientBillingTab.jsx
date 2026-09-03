import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
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

  return (
    <div className="space-y-8">
      <section className="grid sm:grid-cols-4 gap-3">
        <SummaryCard label="Total charges" value={ledger?.total_charges} />
        <SummaryCard label="Total paid" value={ledger?.total_payments} tone="text-emerald-700" />
        <SummaryCard label="Adjustments" value={ledger?.total_adjustments} />
        <SummaryCard label="Outstanding" value={ledger?.outstanding_balance} tone={Number(ledger?.outstanding_balance) > 0 ? "text-red-700" : "text-emerald-700"} />
      </section>

      <div className="grid md:grid-cols-2 gap-6">
        <BillFromCatalogForm patientId={patientId} onDone={invalidateAll} />
        <RecordPaymentForm patientId={patientId} onDone={invalidateAll} />
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

const BILLING_TYPES = [
  { value: "consultation", label: "Consultation Fee" },
  { value: "card", label: "Card" },
];

function BillFromCatalogForm({ patientId, onDone }) {
  const [category, setCategory] = useState("consultation");
  const [itemId, setItemId] = useState("");

  const { data: items } = useQuery({
    queryKey: ["billing-items", category],
    queryFn: () => api.get("/billing-items/", { params: { category, is_active: true } }).then((r) => r.data.results ?? r.data),
  });

  const billItem = useMutation({
    mutationFn: () => {
      const item = items.find((i) => String(i.id) === itemId);
      return api.post("/charges/", { patient: patientId, description: item.name, amount: item.price });
    },
    onSuccess: () => {
      onDone();
      setItemId("");
    },
  });

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (itemId) billItem.mutate(); }}
      className="bg-white border rounded-xl p-5 space-y-3"
    >
      <h2 className="font-medium text-slate-800">Bill a fee or card</h2>
      <div>
        <label className="block text-sm font-medium mb-1">Billing type</label>
        <select
          value={category}
          onChange={(e) => { setCategory(e.target.value); setItemId(""); }}
          className="w-full border rounded-md px-3 py-2 text-sm"
        >
          {BILLING_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium mb-1">{category === "card" ? "Card type" : "Consultation fee"}</label>
        <select value={itemId} onChange={(e) => setItemId(e.target.value)} className="w-full border rounded-md px-3 py-2 text-sm">
          <option value="">Select…</option>
          {(items ?? []).map((i) => (
            <option key={i.id} value={i.id}>{i.name} — {currency(i.price)}</option>
          ))}
        </select>
      </div>
      {billItem.isError && <p className="text-xs text-red-600">Could not bill this item.</p>}
      <button type="submit" disabled={!itemId || billItem.isPending} className="bg-brand-600 text-white px-4 py-2 rounded-md text-sm hover:bg-brand-700 disabled:opacity-50">
        {billItem.isPending ? "Billing…" : "Bill"}
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

function RecordPaymentForm({ patientId, onDone }) {
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
