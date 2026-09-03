import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";

const currency = (n) => new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(n ?? 0);

// The billable categories, shared with the billing counter (Billing.jsx)
// so a service priced here always has a tab to bill it from.
export const BILLING_CATEGORIES = [
  {
    category: "card", title: "Card Types", label: "Hospital card",
    description: "Billed when a patient registers and buys a hospital card.",
    namePlaceholder: "e.g. Adult Card",
  },
  {
    category: "consultation", title: "Consultation Fees", label: "Consultation fee",
    description: "Billed when booking a patient with a doctor.",
    namePlaceholder: "e.g. General Consultation",
  },
  {
    category: "laboratory", title: "Laboratory", label: "Laboratory",
    description: "Tests a doctor refers a patient to the lab for.",
    namePlaceholder: "e.g. Full blood count",
  },
  {
    category: "ultrasound", title: "Ultrasound / Imaging", label: "Ultrasound",
    description: "Scans a doctor refers a patient for.",
    namePlaceholder: "e.g. Abdominal ultrasound",
  },
  {
    category: "eye", title: "Eye Clinic", label: "Eye clinic",
    description: "Eye tests and clinic fees.",
    namePlaceholder: "e.g. Refraction test",
  },
  {
    category: "procedure", title: "Procedures", label: "Procedure",
    description: "Dressings, injections and minor procedures.",
    namePlaceholder: "e.g. Wound dressing",
  },
  {
    category: "other", title: "Other", label: "Other",
    description: "Anything else the counter bills for.",
    namePlaceholder: "e.g. Medical report",
  },
];

export default function BillingItemsAdmin() {
  return (
    <div className="max-w-2xl mx-auto p-6 space-y-10">
      <div>
        <h1 className="text-2xl font-semibold">Billing Catalog</h1>
        <p className="text-sm text-slate-700 mt-1">
          What the counter can bill for, and what each costs. A service with no price here cannot be
          billed — which is how a patient reaches the cashier with nothing to pay.
        </p>
      </div>
      {BILLING_CATEGORIES.map((c) => (
        <CategorySection
          key={c.category}
          category={c.category}
          title={c.title}
          description={c.description}
          namePlaceholder={c.namePlaceholder}
        />
      ))}
    </div>
  );
}

function CategorySection({ category, title, description, namePlaceholder }) {
  const queryClient = useQueryClient();
  const emptyForm = { id: null, category, name: "", price: "", is_active: true };
  const [form, setForm] = useState(emptyForm);
  const queryKey = ["billing-items", category];

  const { data: items, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get("/billing-items/", { params: { category } }).then((r) => r.data.results ?? r.data),
  });

  const save = useMutation({
    mutationFn: (payload) =>
      payload.id ? api.patch(`/billing-items/${payload.id}/`, payload) : api.post("/billing-items/", payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      setForm(emptyForm);
    },
  });

  const toggleActive = useMutation({
    mutationFn: (item) => api.patch(`/billing-items/${item.id}/`, { is_active: !item.is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  });

  return (
    <section className="space-y-4">
      <div>
        <h2 className="font-semibold text-lg">{title}</h2>
        <p className="text-sm text-slate-600">{description}</p>
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); if (form.name && form.price) save.mutate(form); }}
        className="bg-white border rounded-xl p-5 space-y-4"
      >
        <h3 className="font-medium text-slate-800 text-sm">{form.id ? `Edit ${form.name}` : `New ${title.toLowerCase().slice(0, -1)}`}</h3>
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Name *</label>
            <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full border rounded-md px-3 py-2" placeholder={namePlaceholder} />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Price *</label>
            <input required type="number" min="0" step="0.01" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} className="w-full border rounded-md px-3 py-2" />
          </div>
        </div>
        <div className="flex justify-end gap-3">
          {form.id && (
            <button type="button" onClick={() => setForm(emptyForm)} className="border px-4 py-2 rounded-md hover:bg-gray-50">
              Cancel
            </button>
          )}
          <button type="submit" disabled={save.isPending} className="bg-brand-600 text-white px-5 py-2.5 rounded-md hover:bg-brand-700 disabled:opacity-50">
            {form.id ? "Save changes" : "Create"}
          </button>
        </div>
      </form>

      {isLoading && <p className="text-slate-600">Loading…</p>}

      <div className="grid gap-2">
        {(items ?? []).map((item) => (
          <div key={item.id} className="border rounded-lg p-4 flex items-center justify-between">
            <div>
              <span className="font-medium">{item.name}</span>
              <span className="text-sm text-slate-600 ml-2">{currency(item.price)}</span>
              {!item.is_active && <span className="text-xs text-red-500 ml-2">Disabled</span>}
            </div>
            <div className="flex gap-3 text-sm">
              <button onClick={() => setForm({ id: item.id, category, name: item.name, price: item.price, is_active: item.is_active })} className="text-brand-600 hover:underline">
                Edit
              </button>
              <button onClick={() => toggleActive.mutate(item)} className="text-slate-600 hover:underline">
                {item.is_active ? "Disable" : "Enable"}
              </button>
            </div>
          </div>
        ))}
        {!isLoading && (items ?? []).length === 0 && <p className="text-sm text-slate-600">None yet.</p>}
      </div>
    </section>
  );
}
