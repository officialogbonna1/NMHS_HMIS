import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { Button, Page, PageHeader, TabBar, Tab } from "../components/ui.jsx";
import { useToast } from "../components/Toaster.jsx";

// The pharmacy counter. Prescriptions arrive here from doctors as requests;
// dispensing is what actually deducts stock (FEFO, server-side) and raises
// the charge, and the counter then takes payment against that charge.
const currency = (n) => new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(n ?? 0);

const STATUS_TONE = {
  pending: "bg-amber-100 text-amber-800",
  dispensed: "bg-emerald-100 text-emerald-700",
  cancelled: "bg-slate-100 text-slate-400 line-through",
};

export default function Pharmacy() {
  const [tab, setTab] = useState("queue");

  return (
    <Page width="wide">
      <PageHeader
        icon="pill"
        title="Pharmacy"
        subtitle="Process prescriptions, dispensing and pharmacy transactions."
      />

      <TabBar label="Pharmacy sections">
        <Tab active={tab === "queue"} onClick={() => setTab("queue")}>Dispensing queue</Tab>
        <Tab active={tab === "dispensed"} onClick={() => setTab("dispensed")}>Dispensed &amp; payment</Tab>
        <Tab active={tab === "payments"} onClick={() => setTab("payments")}>Pharmacy payments</Tab>
      </TabBar>

      {tab === "queue" && <DispensingQueue />}
      {tab === "dispensed" && <DispensedList />}
      {tab === "payments" && <PharmacyPayments />}
    </Page>
  );
}

function usePrescriptions(status) {
  return useQuery({
    queryKey: ["prescriptions", "status", status],
    queryFn: () => api.get("/prescriptions/", { params: { status } }).then((r) => r.data.results ?? r.data),
  });
}

function DispensingQueue() {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const { data: pending, isLoading } = usePrescriptions("pending");

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["prescriptions"] });
    queryClient.invalidateQueries({ queryKey: ["items"] });
    queryClient.invalidateQueries({ queryKey: ["batches"] });
  };

  const dispense = useMutation({
    mutationFn: (id) => api.post(`/prescriptions/${id}/dispense/`),
    onSuccess: (response) => {
      refresh();
      showToast({
        title: "Dispensed",
        message: `${response.data.item_name} — ${currency(response.data.dispensed_value)} charged to ${response.data.patient_name}`,
      });
    },
    onError: (error) => showToast({
      title: "Could not dispense",
      message: error.response?.data?.detail || "Please try again.",
      tone: "error",
    }),
  });

  const cancel = useMutation({
    mutationFn: ({ id, reason }) => api.post(`/prescriptions/${id}/cancel/`, { reason }),
    onSuccess: () => { refresh(); showToast({ title: "Prescription cancelled" }); },
    onError: (error) => showToast({
      title: "Could not cancel",
      message: error.response?.data?.detail || "Please try again.",
      tone: "error",
    }),
  });

  if (isLoading) return <p className="text-slate-600">Loading…</p>;
  if (!pending?.length) return <p className="text-slate-600 text-sm">Nothing waiting to be dispensed.</p>;

  return (
    <div className="grid gap-2">
      {pending.map((p) => (
        <div key={p.id} className="border rounded-lg bg-white p-4 flex items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-medium">{p.item_name} ×{p.quantity} {p.item_unit}</span>
              <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${STATUS_TONE[p.status]}`}>Awaiting dispensing</span>
            </div>
            <p className="text-sm text-slate-600 mt-1">
              {p.patient_name} · prescribed by Dr. {p.doctor_name} · {new Date(p.created_at).toLocaleString()}
            </p>
            {p.dosage_instructions && <p className="text-sm text-slate-700 mt-1">{p.dosage_instructions}</p>}
          </div>
          <div className="flex items-center gap-3 text-sm shrink-0">
            <button
              onClick={() => dispense.mutate(p.id)}
              disabled={dispense.isPending}
              className="bg-brand-600 text-white px-4 py-2 rounded-md hover:bg-brand-700 disabled:opacity-50"
            >
              Dispense
            </button>
            <Button
              variant="linkDanger" size="xs"
              onClick={() => {
                const reason = prompt("Why is this prescription not being filled?");
                if (reason !== null) cancel.mutate({ id: p.id, reason });
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

function DispensedList() {
  const { data: dispensed, isLoading } = usePrescriptions("dispensed");

  if (isLoading) return <p className="text-slate-600">Loading…</p>;
  if (!dispensed?.length) return <p className="text-slate-600 text-sm">Nothing dispensed yet.</p>;

  // One row per patient: the counter collects against the patient's balance,
  // not against a single prescription line.
  const byPatient = new Map();
  for (const p of dispensed) {
    const entry = byPatient.get(p.patient) ?? { patient: p.patient, name: p.patient_name, lines: [] };
    entry.lines.push(p);
    byPatient.set(p.patient, entry);
  }

  return (
    <div className="grid gap-4">
      {[...byPatient.values()].map((group) => (
        <PatientCounterCard key={group.patient} group={group} />
      ))}
    </div>
  );
}

function PatientCounterCard({ group }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("cash");

  const { data: ledgerResults } = useQuery({
    queryKey: ["ledger", group.patient],
    queryFn: () => api.get("/ledgers/", { params: { patient: group.patient } }).then((r) => r.data.results ?? r.data),
  });
  const ledger = ledgerResults?.[0];
  const outstanding = Number(ledger?.outstanding_balance ?? 0);

  const pay = useMutation({
    mutationFn: () => api.post("/payments/", { patient: group.patient, amount, method }),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ["ledger", group.patient] });
      queryClient.invalidateQueries({ queryKey: ["payments"] });
      setAmount("");
      showToast({ title: "Payment received", message: `${group.name} — ${currency(response.data.amount)}` });
    },
    onError: (error) => showToast({
      title: "Could not record payment",
      message: error.response?.data?.detail || "Please try again.",
      tone: "error",
    }),
  });

  return (
    <div className="border rounded-xl bg-white p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="font-medium">{group.name}</p>
          <ul className="mt-1 text-sm text-slate-600 space-y-0.5">
            {group.lines.map((line) => (
              <li key={line.id}>
                {line.item_name} ×{line.quantity} — {currency(line.dispensed_value)}
                <span className="text-slate-500"> · {new Date(line.dispensed_at).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="text-right shrink-0">
          <p className="text-xs text-slate-600">Outstanding</p>
          <p className={`text-lg font-bold ${outstanding > 0 ? "text-red-700" : "text-emerald-700"}`}>{currency(outstanding)}</p>
        </div>
      </div>

      {outstanding > 0 && (
        <form
          onSubmit={(e) => { e.preventDefault(); if (amount) pay.mutate(); }}
          className="mt-4 flex flex-wrap items-center gap-3 border-t pt-4"
        >
          <input
            type="number" min="1" step="0.01" value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="Amount"
            className="w-32 border rounded-md px-3 py-2 text-sm"
          />
          <select value={method} onChange={(e) => setMethod(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
            <option value="cash">Cash</option>
            <option value="card">Card</option>
            <option value="transfer">Transfer</option>
            <option value="insurance">Insurance</option>
          </select>
          <Button variant="link" size="xs" onClick={() => setAmount(String(outstanding))}>
            Pay in full
          </Button>
          <button
            type="submit"
            disabled={!amount || pay.isPending}
            className="bg-brand-600 text-white px-4 py-2 rounded-md text-sm hover:bg-brand-700 disabled:opacity-50"
          >
            {pay.isPending ? "Recording…" : "Take payment"}
          </button>
        </form>
      )}
    </div>
  );
}

function PharmacyPayments() {
  const { data: payments, isLoading } = useQuery({
    queryKey: ["payments", "pharmacy"],
    queryFn: () => api.get("/payments/", { params: { channel: "pharmacy" } }).then((r) => r.data.results ?? r.data),
  });

  if (isLoading) return <p className="text-slate-600">Loading…</p>;
  if (!payments?.length) return <p className="text-slate-600 text-sm">No payments taken at the pharmacy counter yet.</p>;

  const total = payments.reduce((sum, p) => sum + Number(p.amount), 0);

  return (
    <div>
      <div className="rounded-xl border bg-white p-4 mb-4">
        <p className="text-xs text-slate-600">Collected at the pharmacy counter</p>
        <p className="mt-1 text-lg font-bold text-emerald-700">{currency(total)}</p>
      </div>
      <div className="border rounded-lg divide-y bg-white">
        {payments.map((p) => (
          <div key={p.id} className="px-4 py-3 flex items-center justify-between text-sm">
            <div>
              <p className="font-medium">{p.patient_name}</p>
              <p className="text-xs text-slate-500 mt-0.5">
                {new Date(p.created_at).toLocaleString()} · {p.method} · {p.received_by_name}
              </p>
            </div>
            <span className="font-medium text-emerald-700">{currency(p.amount)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
