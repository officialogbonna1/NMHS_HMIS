import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";

const CATEGORY_TONE = {
  clinical: "bg-red-100 text-red-700",
  routing: "bg-amber-100 text-amber-800",
  billing: "bg-emerald-100 text-emerald-700",
  general: "bg-slate-100 text-slate-600",
};

export default function Notifications() {
  const queryClient = useQueryClient();

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["notifications"] });
    queryClient.invalidateQueries({ queryKey: ["unread-count"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  }

  const { data, isLoading } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get("/notifications/").then((r) => r.data.results ?? r.data),
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

  return (
    <div className="max-w-3xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold">Notifications</h1>
        {unreadCount > 0 && (
          <button
            onClick={() => markAllRead.mutate()}
            className="text-sm text-brand-600 hover:underline"
          >
            Mark all as read ({unreadCount})
          </button>
        )}
      </div>

      {isLoading && <p className="text-slate-600">Loading…</p>}
      {!isLoading && notifications.length === 0 && (
        <p className="text-slate-600">No notifications yet.</p>
      )}

      <div className="grid gap-2">
        {notifications.map((n) => {
          const body = (
            <div
              className={`border rounded-lg p-4 flex items-start justify-between gap-4 ${
                n.is_read ? "bg-white" : "bg-brand-50 border-brand-200"
              }`}
            >
              <div>
                <div className="flex items-center gap-2">
                  {!n.is_read && <span className="h-2 w-2 rounded-full bg-brand-500" />}
                  <span className="font-medium text-slate-900">{n.title}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium uppercase tracking-wide ${CATEGORY_TONE[n.category] ?? CATEGORY_TONE.general}`}>{n.category}</span>
                </div>
                {n.message && <p className="text-sm text-slate-700 mt-1">{n.message}</p>}
                <p className="text-xs text-slate-500 mt-2">{new Date(n.created_at).toLocaleString()}</p>
              </div>
              {!n.is_read && (
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    markRead.mutate(n.id);
                  }}
                  className="text-xs text-brand-600 hover:underline shrink-0"
                >
                  Mark read
                </button>
              )}
            </div>
          );
          return n.action_url ? (
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
