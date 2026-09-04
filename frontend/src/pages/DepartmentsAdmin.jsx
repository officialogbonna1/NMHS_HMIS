import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { Badge, Page, PageHeader } from "../components/ui.jsx";

const emptyForm = { id: null, name: "", code: "", manager: "", staff: [], is_active: true };

export default function DepartmentsAdmin() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(emptyForm);
  const [expanded, setExpanded] = useState(null);

  const { data: departments, isLoading } = useQuery({
    queryKey: ["departments"],
    queryFn: () => api.get("/departments/").then((r) => r.data.results ?? r.data),
  });

  const { data: users } = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get("/users/").then((r) => r.data.results ?? r.data),
  });

  const save = useMutation({
    mutationFn: (payload) =>
      payload.id ? api.patch(`/departments/${payload.id}/`, payload) : api.post("/departments/", payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["departments"] });
      setForm(emptyForm);
    },
  });

  const toggleActive = useMutation({
    mutationFn: (dept) => api.patch(`/departments/${dept.id}/`, { is_active: !dept.is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["departments"] }),
  });

  return (
    <Page className="space-y-8">
      <PageHeader
        className="mb-0"
        icon="building"
        title="Departments"
        subtitle="Units, the staff attached to them, and the services each one offers."
      />

      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ ...form, manager: form.manager || null, code: form.code || slugify(form.name) });
        }}
        className="bg-white border rounded-xl p-5 space-y-4"
      >
        <h2 className="font-medium text-slate-800">{form.id ? "Edit department" : "New department"}</h2>
        <div className="grid md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Name *</label>
            <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full border rounded-md px-3 py-2" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Code</label>
            <input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="auto from name" className="w-full border rounded-md px-3 py-2" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Manager</label>
            <select value={form.manager ?? ""} onChange={(e) => setForm({ ...form, manager: e.target.value })} className="w-full border rounded-md px-3 py-2">
              <option value="">None</option>
              {(users ?? []).map((u) => (
                <option key={u.id} value={u.id}>{u.first_name || u.username} ({u.role})</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Staff</label>
            <select
              multiple
              value={form.staff}
              onChange={(e) => setForm({ ...form, staff: Array.from(e.target.selectedOptions, (o) => o.value) })}
              className="w-full border rounded-md px-3 py-2 h-24"
            >
              {(users ?? []).map((u) => (
                <option key={u.id} value={u.id}>{u.first_name || u.username} ({u.role})</option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex justify-end gap-3">
          {form.id && (
            <button type="button" onClick={() => setForm(emptyForm)} className="border px-4 py-2 rounded-md hover:bg-gray-50">
              Cancel
            </button>
          )}
          <button type="submit" disabled={save.isPending} className="bg-brand-600 text-white px-5 py-2.5 rounded-md hover:bg-brand-700 disabled:opacity-50">
            {form.id ? "Save changes" : "Create department"}
          </button>
        </div>
      </form>

      {isLoading && <p className="text-slate-600">Loading…</p>}

      <div className="grid gap-2">
        {(departments ?? []).map((d) => (
          <div key={d.id} className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="font-medium text-slate-900">{d.name}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-2 text-sm text-slate-600">
                  <span>{d.code}</span>
                  {!d.is_active && <Badge tone="danger">Disabled</Badge>}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap gap-1">
                <button
                  onClick={() =>
                    setForm({ id: d.id, name: d.name, code: d.code, manager: d.manager ?? "", staff: (d.staff ?? []).map(String), is_active: d.is_active })
                  }
                  className="min-h-[36px] rounded-lg px-3 py-1.5 text-sm font-medium text-brand-700 transition hover:bg-brand-50"
                >
                  Edit
                </button>
                <button onClick={() => toggleActive.mutate(d)} className="min-h-[36px] rounded-lg px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100">
                  {d.is_active ? "Disable" : "Enable"}
                </button>
                <button onClick={() => setExpanded(expanded === d.id ? null : d.id)} className="min-h-[36px] rounded-lg px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100">
                  {expanded === d.id ? "Hide services" : "Services"}
                </button>
              </div>
            </div>
            {expanded === d.id && <DepartmentServices departmentId={d.id} />}
          </div>
        ))}
      </div>
    </Page>
  );
}

function DepartmentServices({ departmentId }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");

  const { data: services } = useQuery({
    queryKey: ["services", departmentId],
    queryFn: () => api.get("/services/", { params: { department: departmentId } }).then((r) => r.data.results ?? r.data),
  });

  const addService = useMutation({
    mutationFn: () => api.post("/services/", { department: departmentId, name, code: slugify(name), price: price || 0 }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["services", departmentId] });
      setName("");
      setPrice("");
    },
  });

  return (
    <div className="mt-4 pl-4 border-l-2 border-gray-100 space-y-2">
      {(services ?? []).map((s) => (
        <div key={s.id} className="text-sm flex justify-between text-slate-700">
          <span>{s.name}</span>
          <span>₦{Number(s.price).toLocaleString()}</span>
        </div>
      ))}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (name) addService.mutate();
        }}
        className="flex gap-2"
      >
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Service name" className="border rounded-md px-2 py-1 text-sm flex-1" />
        <input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="Price" type="number" className="border rounded-md px-2 py-1 text-sm w-28" />
        <Button type="submit" variant="link" size="xs">Add</Button>
      </form>
    </div>
  );
}

function slugify(text) {
  return text.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}
