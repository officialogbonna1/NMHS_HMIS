import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { Badge, Page, PageHeader, SearchInput } from "../components/ui.jsx";

const ROLES = [
  ["admin", "Super Admin"], ["hospital_admin", "Hospital Admin"], ["doctor", "Doctor"], ["nurse", "Nurse"],
  ["reception", "Reception"], ["pharmacist", "Pharmacist"], ["laboratory", "Laboratory Scientist"],
  ["radiology", "Radiology Staff"], ["optometrist", "Optometrist"], ["ophthalmologist", "Ophthalmologist / Eye Doctor"],
  ["surgeon", "Surgeon"], ["anesthetist", "Anesthetist"], ["cashier", "Billing Officer / Cashier"],
  ["ward_manager", "Ward Manager"], ["records_officer", "Records Officer"], ["inventory_manager", "Inventory Manager"],
  ["accountant", "Accountant"], ["executive", "Management / Executive"], ["custom", "Custom role"],
];

const emptyForm = { id: null, username: "", first_name: "", last_name: "", email: "", role: "reception", department: "", password: "", must_change_password: true };

export default function UsersAdmin() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(emptyForm);
  const [resetTarget, setResetTarget] = useState(null);
  const [search, setSearch] = useState("");

  const { data: users, isLoading } = useQuery({
    // Searched on the server, over the same fields the API declares: staff
    // number, name, username, email. `NMHS-S000001` finds one person.
    queryKey: ["users", search],
    queryFn: () => api.get("/users/", { params: search ? { search } : {} }).then((r) => r.data.results ?? r.data),
  });

  const save = useMutation({
    mutationFn: (payload) => {
      const { id, password, ...body } = payload;
      if (id) return api.patch(`/users/${id}/`, body);
      if (!password) throw new Error("Password required");
      return api.post("/users/", { ...body, password });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      setForm(emptyForm);
    },
  });

  const toggleActive = useMutation({
    mutationFn: (u) => api.patch(`/users/${u.id}/`, { is_active: !u.is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });

  const resetPassword = useMutation({
    mutationFn: ({ id, password }) => api.post(`/users/${id}/set_password/`, { password }),
    onSuccess: () => setResetTarget(null),
  });

  return (
    <Page className="space-y-8">
      <PageHeader
        className="mb-0"
        icon="shield"
        title="Users"
        subtitle="Staff accounts and the role each one signs in with."
        toolbar={
          <SearchInput
            value={search}
            onChange={setSearch}
            label="Search staff"
            placeholder="Staff no., name, username or email…"
            className="sm:max-w-sm"
          />
        }
      />

      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(form);
        }}
        className="bg-white border rounded-xl p-5 space-y-4"
      >
        <h2 className="font-medium text-slate-800">{form.id ? `Edit ${form.username}` : "New user"}</h2>
        <div className="grid md:grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Username *</label>
            <input required disabled={!!form.id} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} className="w-full border rounded-md px-3 py-2 disabled:bg-gray-50" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">First name</label>
            <input value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} className="w-full border rounded-md px-3 py-2" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Last name</label>
            <input value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} className="w-full border rounded-md px-3 py-2" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Email</label>
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className="w-full border rounded-md px-3 py-2" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Role *</label>
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className="w-full border rounded-md px-3 py-2">
              {ROLES.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Department</label>
            <input value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })} className="w-full border rounded-md px-3 py-2" placeholder="e.g. Front Desk" />
          </div>
          {!form.id && (
            <div>
              <label className="block text-sm font-medium mb-1">Password *</label>
              <input type="password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} className="w-full border rounded-md px-3 py-2" />
            </div>
          )}
        </div>

        {save.isError && <p className="text-sm text-red-600">{save.error?.message || "Could not save this user."}</p>}

        <div className="flex justify-end gap-3">
          {form.id && (
            <button type="button" onClick={() => setForm(emptyForm)} className="border px-4 py-2 rounded-md hover:bg-gray-50">
              Cancel
            </button>
          )}
          <button type="submit" disabled={save.isPending} className="bg-brand-600 text-white px-5 py-2.5 rounded-md hover:bg-brand-700 disabled:opacity-50">
            {form.id ? "Save changes" : "Create user"}
          </button>
        </div>
      </form>

      {isLoading && <p className="text-slate-600">Loading…</p>}

      <div className="grid gap-2">
        {(users ?? []).map((u) => (
          <div key={u.id} className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <p className="font-medium text-slate-900">{u.first_name || u.username} {u.last_name}</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-600">
                {u.staff_number && (
                  <span className="rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
                    Staff No. {u.staff_number}
                  </span>
                )}
                <span>@{u.username}</span>
                <span aria-hidden="true" className="text-slate-400">·</span>
                <span className="capitalize">{u.role.replaceAll("_", " ")}</span>
                {u.department && <><span aria-hidden="true" className="text-slate-400">·</span><span>{u.department}</span></>}
                {!u.is_active && <Badge tone="danger">Disabled</Badge>}
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-1">
              <button
                onClick={() => setForm({ id: u.id, username: u.username, first_name: u.first_name, last_name: u.last_name, email: u.email, role: u.role, department: u.department, password: "", must_change_password: u.must_change_password })}
                className="min-h-[36px] rounded-lg px-3 py-1.5 text-sm font-medium text-brand-700 transition hover:bg-brand-50"
              >
                Edit
              </button>
              <button onClick={() => toggleActive.mutate(u)} className="min-h-[36px] rounded-lg px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100">
                {u.is_active ? "Disable" : "Enable"}
              </button>
              <button onClick={() => setResetTarget(u)} className="min-h-[36px] rounded-lg px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100">
                Reset password
              </button>
            </div>
          </div>
        ))}
      </div>

      {resetTarget && (
        <ResetPasswordDialog
          user={resetTarget}
          onCancel={() => setResetTarget(null)}
          onConfirm={(password) => resetPassword.mutate({ id: resetTarget.id, password })}
          pending={resetPassword.isPending}
        />
      )}
    </Page>
  );
}

function ResetPasswordDialog({ user, onCancel, onConfirm, pending }) {
  const [password, setPassword] = useState("");
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-xl p-5 w-full max-w-sm space-y-4">
        <h2 className="font-medium">Reset password for {user.username}</h2>
        <input
          type="password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="New password"
          className="w-full border rounded-md px-3 py-2"
        />
        <div className="flex justify-end gap-3">
          <button onClick={onCancel} className="border px-4 py-2 rounded-md hover:bg-gray-50">Cancel</button>
          <button
            onClick={() => password && onConfirm(password)}
            disabled={!password || pending}
            className="bg-brand-600 text-white px-4 py-2 rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {pending ? "Saving…" : "Set password"}
          </button>
        </div>
      </div>
    </div>
  );
}
