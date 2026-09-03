import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { useToast } from "../components/Toaster.jsx";

// Pharmacy stock control: what's on the shelf, what's running out, what's
// about to expire — plus the two ways stock legitimately changes outside
// dispensing: receiving a delivery and counting the shelf. Both go through
// the backend's inventory services, so each leaves a StockMovement behind.
const currency = (n) => new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(n ?? 0);

const REASON_LABEL = {
  received: "Received",
  prescription: "Dispensed",
  sale: "Sold",
  adjustment: "Stock count",
  expired_writeoff: "Expired write-off",
};

export default function InventoryDashboard() {
  const [tab, setTab] = useState("stock");

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Inventory</h1>

      <div className="flex gap-6 border-b text-sm">
        <TabButton active={tab === "stock"} onClick={() => setTab("stock")}>Stock on hand</TabButton>
        <TabButton active={tab === "receive"} onClick={() => setTab("receive")}>Receive stock</TabButton>
        <TabButton active={tab === "movements"} onClick={() => setTab("movements")}>Movement log</TabButton>
      </div>

      {tab === "stock" && <StockOnHand />}
      {tab === "receive" && <ReceiveStock />}
      {tab === "movements" && <MovementLog />}
    </div>
  );
}

function TabButton({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`pb-3 -mb-px border-b-2 ${active ? "border-brand-600 text-brand-600 font-medium" : "border-transparent text-slate-600"}`}
    >
      {children}
    </button>
  );
}

function useStock() {
  const items = useQuery({
    queryKey: ["items"],
    queryFn: () => api.get("/items/").then((r) => r.data.results ?? r.data),
  });
  const batches = useQuery({
    queryKey: ["batches"],
    queryFn: () => api.get("/batches/").then((r) => r.data.results ?? r.data),
  });
  return { items: items.data, batches: batches.data, isLoading: items.isLoading || batches.isLoading };
}

function StockOnHand() {
  const { items, batches, isLoading } = useStock();
  const lowStock = items?.filter((i) => i.is_low_stock) ?? [];
  const expiringSoon = (batches ?? []).filter((b) => b.quantity > 0 && daysUntil(b.expiry_date) <= 30);

  if (isLoading) return <p className="text-slate-600">Loading…</p>;

  return (
    <div className="space-y-8">
      {lowStock.length > 0 && (
        <section>
          <h2 className="font-medium text-red-600 mb-2">⚠ Low stock ({lowStock.length})</h2>
          <div className="grid gap-2">
            {lowStock.map((i) => (
              <div key={i.id} className="border rounded-lg bg-white p-3 flex justify-between">
                <span>{i.name}</span>
                <span className="text-sm text-slate-600">{i.total_quantity} / threshold {i.reorder_threshold}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {expiringSoon.length > 0 && (
        <section>
          <h2 className="font-medium text-amber-600 mb-2">⏳ Expiring within 30 days ({expiringSoon.length})</h2>
          <div className="grid gap-2">
            {expiringSoon.map((b) => (
              <div key={b.id} className="border rounded-lg bg-white p-3 flex justify-between">
                <span>{b.item_name} — batch {b.batch_no}</span>
                <span className="text-sm text-slate-600">{b.expiry_date} · qty {b.quantity}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="font-medium text-slate-800 mb-2">Batches</h2>
        <p className="text-xs text-slate-600 mb-3">
          Batches are dispensed first-expiry-first-out. Counting a batch corrects it to what is physically on the shelf and logs the difference.
        </p>
        {(batches ?? []).length === 0 && <p className="text-slate-600 text-sm">No stock has been received yet.</p>}
        <div className="grid gap-2">
          {(batches ?? []).map((b) => <BatchRow key={b.id} batch={b} />)}
        </div>
      </section>
    </div>
  );
}

function BatchRow({ batch }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [counting, setCounting] = useState(false);
  const [counted, setCounted] = useState("");

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["batches"] });
    queryClient.invalidateQueries({ queryKey: ["items"] });
    queryClient.invalidateQueries({ queryKey: ["stock-movements"] });
  };

  const submitCount = useMutation({
    mutationFn: () => api.post(`/batches/${batch.id}/count/`, { counted_quantity: Number(counted) }),
    onSuccess: (response) => {
      refresh();
      setCounting(false);
      setCounted("");
      showToast({ title: "Stock count recorded", message: `${batch.item_name} — now ${response.data.quantity}` });
    },
    onError: (error) => showToast({
      title: "Could not record count",
      message: error.response?.data?.detail || "Please try again.",
      tone: "error",
    }),
  });

  const writeOff = useMutation({
    mutationFn: () => api.post(`/batches/${batch.id}/write_off/`),
    onSuccess: () => { refresh(); showToast({ title: "Expired stock written off" }); },
    onError: (error) => showToast({
      title: "Could not write off",
      message: error.response?.data?.detail || "Please try again.",
      tone: "error",
    }),
  });

  return (
    <div className="border rounded-lg bg-white p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-medium">{batch.item_name}</span>
            <span className="text-xs text-slate-500">batch {batch.batch_no}</span>
            {batch.is_expired && <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-red-100 text-red-700">Expired</span>}
          </div>
          <p className="text-sm text-slate-600 mt-1">
            Expires {batch.expiry_date} · {currency(batch.sale_price)} per {batch.item_unit}
            {batch.supplier && ` · ${batch.supplier}`}
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm shrink-0">
          <span className="font-medium">{batch.quantity}</span>
          <button onClick={() => setCounting((c) => !c)} className="text-brand-600 hover:underline">Count</button>
          {batch.is_expired && batch.quantity > 0 && (
            <button onClick={() => confirm("Write off the remaining units of this expired batch?") && writeOff.mutate()} className="text-red-600 hover:underline">
              Write off
            </button>
          )}
        </div>
      </div>

      {counting && (
        <form
          onSubmit={(e) => { e.preventDefault(); if (counted !== "") submitCount.mutate(); }}
          className="mt-3 flex items-center gap-3 border-t pt-3"
        >
          <label className="text-sm text-slate-700">Counted on shelf</label>
          <input
            type="number" min="0" value={counted} autoFocus
            onChange={(e) => setCounted(e.target.value)}
            className="w-28 border rounded-md px-3 py-2 text-sm"
          />
          <button type="submit" disabled={counted === "" || submitCount.isPending} className="bg-brand-600 text-white px-4 py-2 rounded-md text-sm disabled:opacity-50">
            {submitCount.isPending ? "Saving…" : "Record count"}
          </button>
          <button type="button" onClick={() => { setCounting(false); setCounted(""); }} className="text-sm text-slate-600">Cancel</button>
        </form>
      )}
    </div>
  );
}

function ReceiveStock() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [form, setForm] = useState({
    item: "", batch_no: "", quantity: "", cost_price: "", sale_price: "", expiry_date: "", supplier: "",
  });

  const { data: items } = useQuery({
    queryKey: ["items"],
    queryFn: () => api.get("/items/").then((r) => r.data.results ?? r.data),
  });

  const receive = useMutation({
    mutationFn: () => api.post("/batches/", { ...form, item: Number(form.item), quantity: Number(form.quantity) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batches"] });
      queryClient.invalidateQueries({ queryKey: ["items"] });
      queryClient.invalidateQueries({ queryKey: ["stock-movements"] });
      showToast({ title: "Stock received", message: `${form.quantity} unit(s) added` });
      setForm({ item: "", batch_no: "", quantity: "", cost_price: "", sale_price: "", expiry_date: "", supplier: "" });
    },
    onError: (error) => showToast({
      title: "Could not receive stock",
      message: error.response?.data?.detail || "Check the fields and try again.",
      tone: "error",
    }),
  });

  const set = (field, value) => setForm((f) => ({ ...f, [field]: value }));
  const canSubmit = form.item && form.batch_no && form.quantity && form.cost_price && form.sale_price && form.expiry_date;

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (canSubmit) receive.mutate(); }}
      className="bg-white border rounded-xl p-5 space-y-4 max-w-2xl"
    >
      <h2 className="font-medium text-slate-800">Receive a delivery</h2>
      <p className="text-xs text-slate-600 -mt-2">
        Each delivery is its own batch so expiry is tracked per batch — dispensing always takes from the earliest-expiring one first.
      </p>

      <div className="grid md:grid-cols-2 gap-4">
        <div className="md:col-span-2">
          <label className="block text-sm font-medium mb-1">Drug *</label>
          <select value={form.item} onChange={(e) => set("item", e.target.value)} className="w-full border rounded-md px-3 py-2">
            <option value="">Select drug</option>
            {(items ?? []).map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
          </select>
        </div>
        <Field label="Batch number *" value={form.batch_no} onChange={(v) => set("batch_no", v)} />
        <Field label="Quantity *" type="number" min="1" value={form.quantity} onChange={(v) => set("quantity", v)} />
        <Field label="Cost price *" type="number" min="0" step="0.01" value={form.cost_price} onChange={(v) => set("cost_price", v)} />
        <Field label="Sale price *" type="number" min="0" step="0.01" value={form.sale_price} onChange={(v) => set("sale_price", v)} />
        <Field label="Expiry date *" type="date" value={form.expiry_date} onChange={(v) => set("expiry_date", v)} />
        <Field label="Supplier" value={form.supplier} onChange={(v) => set("supplier", v)} />
      </div>

      <button type="submit" disabled={!canSubmit || receive.isPending} className="bg-brand-600 text-white px-5 py-2.5 rounded-md hover:bg-brand-700 disabled:opacity-50">
        {receive.isPending ? "Receiving…" : "Receive stock"}
      </button>
    </form>
  );
}

function Field({ label, value, onChange, type = "text", ...rest }) {
  return (
    <div>
      <label className="block text-sm font-medium mb-1">{label}</label>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)} className="w-full border rounded-md px-3 py-2" {...rest} />
    </div>
  );
}

function MovementLog() {
  const { data: movements, isLoading } = useQuery({
    queryKey: ["stock-movements"],
    queryFn: () => api.get("/stock-movements/").then((r) => r.data.results ?? r.data),
  });

  if (isLoading) return <p className="text-slate-600">Loading…</p>;
  if (!movements?.length) return <p className="text-slate-600 text-sm">No stock movements recorded yet.</p>;

  return (
    <div className="border rounded-lg divide-y bg-white">
      {movements.map((m) => (
        <div key={m.id} className="px-4 py-3 flex items-center justify-between text-sm">
          <div>
            <p className="font-medium">{m.item_name} <span className="text-slate-500 font-normal">· batch {m.batch_no}</span></p>
            <p className="text-xs text-slate-500 mt-0.5">
              {REASON_LABEL[m.reason] ?? m.reason} · {new Date(m.created_at).toLocaleString()} · {m.performed_by_name}
            </p>
          </div>
          <span className={`font-medium ${m.change < 0 ? "text-red-700" : "text-emerald-700"}`}>
            {m.change > 0 ? "+" : ""}{m.change}
          </span>
        </div>
      ))}
    </div>
  );
}

function daysUntil(dateStr) {
  return Math.ceil((new Date(dateStr) - new Date()) / (1000 * 60 * 60 * 24));
}
