import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";
import { useAuth } from "../auth/AuthContext.jsx";

const CATEGORY_TONE = {
  clinical: "bg-red-100 text-red-700",
  routing: "bg-amber-100 text-amber-800",
  billing: "bg-emerald-100 text-emerald-700",
  general: "bg-slate-100 text-slate-600",
};

// Notifications, and — for an admin — everyone's.
//
// The two views are kept apart on purpose. "Mine" is the inbox the bell
// counts and the only one you can mark read. "Everyone" is oversight: it
// answers "was the lab ever actually told?" without letting an admin clear
// other people's unread notifications, or drowning their own bell in the
// whole hospital's traffic.

export default function Notifications() {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const isAdmin = ["admin", "hospital_admin"].includes(user?.role);

  const [scope, setScope] = useState("mine");
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);

  const everyone = isAdmin && scope === "all";

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["notifications"] });
    queryClient.invalidateQueries({ queryKey: ["unread-count"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  }

  const { data, isLoading } = useQuery({
    queryKey: ["notifications", everyone ? "all" : "mine", category, unreadOnly, search],
    queryFn: () => api.get("/notifications/", {
      params: {
        ...(everyone ? { scope: "all", page_size: 200 } : {}),
        ...(category === "all" ? {} : { category }),
        ...(unreadOnly ? { is_read: false } : {}),
        ...(search.trim() ? { search: search.trim() } : {}),
      },
    }).then((r) => r.data.results ?? r.data),
  });

  // Only ever asked for on the everyone view — it is a whole-system count.
  const { data: overview } = useQuery({
    queryKey: ["notification-overview"],
    queryFn: () => api.get("/notifications/overview/").then((r) => r.data),
    enabled: everyone,
  });

  const markRead = useMutation({
    mutationFn: (id) => api.patch(`/notifications/${id}/`, { is_read: true }),
    onSuccess: refresh,
  });

  const markAllRead = useMutation({
    mutationFn: () => api.post("/notifications/mark_all_read/"),
    onSuccess: refresh,
  });

  const notifications = data ?? [];
  const unreadCount = notifications.filter((n) => !n.is_read).length;

  const categories = useMemo(
    () => ["all", ...new Set((data ?? []).map((n) => n.category))],
    [data],
  );

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
            Notifications
          </h1>
          <p className="mt-1 text-sm text-slate-700">
            {everyone
              ? "Everything the system has told anyone. Read-only — you can only mark your own as read."
              : "Everything addressed to you."}
          </p>
        </div>
        {!everyone && unreadCount > 0 && (
          <button
            onClick={() => markAllRead.mutate()}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
          >
            Mark all as read ({unreadCount})
          </button>
        )}
      </div>

      {isAdmin && (
        <div className="mb-4 flex overflow-hidden rounded-lg border border-slate-300 text-sm">
          {[["mine", "Mine"], ["all", "Everyone"]].map(([value, label]) => (
            <button
              key={value}
              onClick={() => setScope(value)}
              className={`flex-1 px-4 py-2 font-medium transition ${
                scope === value
                  ? "bg-brand-600 text-white"
                  : "bg-white text-slate-700 hover:bg-slate-50"}`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {everyone && overview && (
        <section className="mb-4 rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <span className="text-slate-700">
              <strong className="text-slate-900">{overview.total}</strong> in the system
            </span>
            <span className="text-slate-700">
              <strong className="text-slate-900">{overview.unread}</strong> still unread
            </span>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[26rem] text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-1 pr-3 font-semibold">Staff</th>
                  <th className="py-1 pr-3 font-semibold">Role</th>
                  <th className="py-1 pr-3 font-semibold">Sent</th>
                  <th className="py-1 font-semibold">Unread</th>
                </tr>
              </thead>
              <tbody>
                {overview.people.map((p) => (
                  <tr key={p.username} className="border-b border-slate-100 last:border-0">
                    <td className="py-1 pr-3 text-slate-800">{p.username}</td>
                    <td className="py-1 pr-3 text-slate-600">{p.role?.replaceAll("_", " ")}</td>
                    <td className="py-1 pr-3 text-slate-800">{p.total}</td>
                    <td className={`py-1 ${p.unread > 0 ? "font-semibold text-amber-800" : "text-slate-600"}`}>
                      {p.unread}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={everyone ? "Search title, message or staff name…" : "Search…"}
          className="min-w-[12rem] flex-1 rounded-lg border border-slate-300 px-3 py-2.5 text-base text-slate-900 sm:py-2 sm:text-sm"
        />
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-lg border border-slate-300 px-3 py-2.5 text-base text-slate-900 sm:py-2 sm:text-sm"
        >
          {categories.map((c) => (
            <option key={c} value={c}>{c === "all" ? "All categories" : c}</option>
          ))}
        </select>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700">
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(e) => setUnreadOnly(e.target.checked)}
            className="h-4 w-4 rounded border-slate-400 text-brand-600 focus:ring-brand-500"
          />
          Unread only
        </label>
      </div>

      {isLoading && <p className="text-slate-600">Loading…</p>}
      {!isLoading && notifications.length === 0 && (
        <p className="text-slate-600">
          {everyone ? "Nothing matches that." : "No notifications yet."}
        </p>
      )}

      <div className="grid gap-2">
        {notifications.map((n) => {
          // On the everyone view a row is somebody else's mail: it is read,
          // not acted on, so it does not link anywhere and cannot be marked.
          const mine = !everyone || n.recipient === user?.id;
          const body = (
            <div
              className={`flex items-start justify-between gap-4 rounded-lg border p-4 ${
                n.is_read ? "bg-white" : "border-brand-200 bg-brand-50"
              }`}
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  {!n.is_read && <span className="h-2 w-2 rounded-full bg-brand-500" />}
                  <span className="font-medium text-slate-900">{n.title}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium uppercase tracking-wide ${CATEGORY_TONE[n.category] ?? CATEGORY_TONE.general}`}>
                    {n.category}
                  </span>
                  {everyone && (
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                      → {n.recipient_name}
                      {n.recipient_role && ` · ${n.recipient_role.replaceAll("_", " ")}`}
                    </span>
                  )}
                </div>
                {n.message && <p className="mt-1 text-sm text-slate-700">{n.message}</p>}
                <p className="mt-2 text-xs text-slate-600">
                  {new Date(n.created_at).toLocaleString()}
                  {everyone && n.action_url && ` · links to ${n.action_url}`}
                </p>
              </div>
              {!n.is_read && mine && (
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    markRead.mutate(n.id);
                  }}
                  className="shrink-0 text-xs text-brand-600 hover:underline"
                >
                  Mark read
                </button>
              )}
            </div>
          );
          return n.action_url && mine ? (
            <Link key={n.id} to={n.action_url} onClick={() => !n.is_read && markRead.mutate(n.id)}>
              {body}
            </Link>
          ) : (
            <div key={n.id}>{body}</div>
          );
        })}
      </div>
    </div>
  );
}
