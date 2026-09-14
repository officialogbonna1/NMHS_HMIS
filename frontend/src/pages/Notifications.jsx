import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import api from "../api/client";
import {
  Button, EmptyState, ErrorState, MetaStat, Modal, Page, PageHeader, Tab, TabBar,
} from "../components/ui.jsx";
import { useToast } from "../components/Toaster.jsx";
import { useAuth } from "../auth/AuthContext.jsx";

const CATEGORY_TONE = {
  clinical: "bg-red-100 text-red-700",
  routing: "bg-amber-100 text-amber-800",
  billing: "bg-emerald-100 text-emerald-700",
  general: "bg-slate-100 text-slate-600",
};

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// Notifications, and — for an admin — everyone's.
//
// The two views are kept apart on purpose. "Mine" is the inbox the bell
// counts and the only one you can mark read. "Everyone" is oversight: it
// answers "was the lab ever actually told?" without letting an admin clear
// other people's unread notifications, or drowning their own bell in the
// whole hospital's traffic.
//
// Nothing here deletes. A notification leaves the inbox by being archived:
// the server keeps it, the Archived tab shows it, and Restore puts it back.
// Archiving is only ever your own, so the everyone view stays read-only.

export default function Notifications() {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { showToast } = useToast();
  const isAdmin = ["admin", "hospital_admin"].includes(user?.role);

  // The folder lives in the URL, so `/notifications?tab=archived` can be linked.
  const [params, setParams] = useSearchParams();
  const archived = params.get("tab") === "archived";

  const [scope, setScope] = useState("mine");
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [confirmArchiveAll, setConfirmArchiveAll] = useState(false);

  const everyone = isAdmin && scope === "all";
  const filtering = category !== "all" || unreadOnly || search.trim() !== "";

  function showFolder(next) {
    setParams(next === "archived" ? { tab: "archived" } : {}, { replace: true });
    setSelected(new Set());
  }

  function refresh() {
    for (const key of ["notifications", "unread-count", "dashboard", "notification-overview"]) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  }

  const failed = (title) => (error) => showToast({
    tone: "error", title, message: error.response?.data?.detail ?? "Please try again.",
  });

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["notifications", everyone ? "all" : "mine", archived ? "archived" : "inbox",
               category, unreadOnly, search],
    queryFn: () => api.get("/notifications/", {
      params: {
        ...(everyone ? { scope: "all", page_size: 200 } : {}),
        ...(archived ? { archived: true } : {}),
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

  const setRead = useMutation({
    mutationFn: ({ id, isRead }) => api.patch(`/notifications/${id}/`, { is_read: isRead }),
    onSuccess: refresh,
    onError: failed("Could not update the notification"),
  });

  const markAllRead = useMutation({
    mutationFn: () => api.post("/notifications/mark_all_read/"),
    onSuccess: refresh,
  });

  const archiveOne = useMutation({
    mutationFn: (id) => api.post(`/notifications/${id}/archive/`),
    onSuccess: () => {
      refresh();
      showToast({ title: "Notification archived", message: "It is kept under Archived." });
    },
    onError: failed("Could not archive the notification"),
  });

  const restoreOne = useMutation({
    mutationFn: (id) => api.post(`/notifications/${id}/unarchive/`),
    onSuccess: () => {
      refresh();
      showToast({ title: "Moved back to your inbox" });
    },
    onError: failed("Could not restore the notification"),
  });

  const archiveSelected = useMutation({
    mutationFn: (ids) => api.post("/notifications/archive_selected/", { ids }),
    onSuccess: (response) => {
      setSelected(new Set());
      refresh();
      showToast({ title: `${plural(response.data.archived, "notification")} archived`,
                  message: "They are kept under Archived." });
    },
    onError: failed("Could not archive those notifications"),
  });

  const archiveAll = useMutation({
    mutationFn: () => api.post("/notifications/archive_all/"),
    onSuccess: (response) => {
      setConfirmArchiveAll(false);
      setSelected(new Set());
      refresh();
      showToast({ title: `${plural(response.data.archived, "notification")} archived`,
                  message: "Your inbox is clear. Everything is kept under Archived." });
    },
    onError: failed("Could not archive your notifications"),
  });

  const notifications = data ?? [];
  const unreadCount = notifications.filter((n) => !n.is_read).length;
  const categories = ["all", ...new Set(notifications.map((n) => n.category))];

  // Only your own inbox is selectable: an archived row has one action, and the
  // everyone view is somebody else's mail.
  const selectable = !everyone && !archived;
  const selectedIds = notifications.filter((n) => selected.has(n.id)).map((n) => n.id);
  const allSelected = notifications.length > 0 && selectedIds.length === notifications.length;

  function toggle(id) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(notifications.map((n) => n.id)));
  }

  const subtitle = everyone
    ? archived
      ? "What anyone has archived. Read-only — archiving is only ever your own."
      : "Everything the system has told anyone. Read-only — you can only mark your own as read."
    : archived
      ? "Notifications you have archived. They are kept, and can be moved back to your inbox."
      : "Everything addressed to you.";

  return (
    <Page>
      <PageHeader
        icon="bell"
        title="Notifications"
        subtitle={subtitle}
        meta={!everyone && !archived && unreadCount > 0 && (
          <MetaStat value={unreadCount} label="unread" tone="danger" />
        )}
        actions={!everyone && !archived && (notifications.length > 0 || filtering) && (
          <>
            {unreadCount > 0 && (
              <Button variant="secondary" onClick={() => markAllRead.mutate()}>
                Mark all as read ({unreadCount})
              </Button>
            )}
            <Button variant="secondary" onClick={() => setConfirmArchiveAll(true)}>
              Archive all
            </Button>
          </>
        )}
      />

      {isAdmin && (
        <div className="mb-4 flex overflow-hidden rounded-lg border border-slate-300 text-sm">
          {[["mine", "Mine"], ["all", "Everyone"]].map(([value, label]) => (
            <button
              key={value}
              onClick={() => { setScope(value); setSelected(new Set()); }}
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

      <TabBar label="Notification folders">
        <Tab active={!archived} onClick={() => showFolder("inbox")}>Inbox</Tab>
        <Tab active={archived} onClick={() => showFolder("archived")}>Archived</Tab>
      </TabBar>

      {everyone && overview && (
        <section className="mb-4 rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <span className="text-slate-700">
              <strong className="text-slate-900">{overview.total}</strong> in the system
            </span>
            <span className="text-slate-700">
              <strong className="text-slate-900">{overview.unread}</strong> still unread
            </span>
            <span className="text-slate-700">
              <strong className="text-slate-900">{overview.archived}</strong> archived
            </span>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[30rem] text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-1 pr-3 font-semibold">Staff</th>
                  <th className="py-1 pr-3 font-semibold">Role</th>
                  <th className="py-1 pr-3 font-semibold">Sent</th>
                  <th className="py-1 pr-3 font-semibold">Unread</th>
                  <th className="py-1 font-semibold">Archived</th>
                </tr>
              </thead>
              <tbody>
                {overview.people.map((p) => (
                  <tr key={p.username} className="border-b border-slate-100 last:border-0">
                    <td className="py-1 pr-3 text-slate-800">{p.username}</td>
                    <td className="py-1 pr-3 text-slate-600">{p.role?.replaceAll("_", " ")}</td>
                    <td className="py-1 pr-3 text-slate-800">{p.total}</td>
                    <td className={`py-1 pr-3 ${p.unread > 0 ? "font-semibold text-amber-800" : "text-slate-600"}`}>
                      {p.unread}
                    </td>
                    <td className="py-1 text-slate-600">{p.archived}</td>
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

      {selectable && notifications.length > 0 && (
        <div className="mb-2 flex min-h-[44px] flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5">
          <label className="inline-flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-700">
            <input
              type="checkbox"
              checked={allSelected}
              ref={(el) => { if (el) el.indeterminate = selectedIds.length > 0 && !allSelected; }}
              onChange={toggleAll}
              className="h-4 w-4 rounded border-slate-400 text-brand-600 focus:ring-brand-500"
            />
            {selectedIds.length > 0 ? `${selectedIds.length} selected` : "Select all"}
          </label>
          {selectedIds.length > 0 && (
            <Button
              variant="soft" size="sm"
              onClick={() => archiveSelected.mutate(selectedIds)}
              disabled={archiveSelected.isPending}
            >
              Archive selected ({selectedIds.length})
            </Button>
          )}
        </div>
      )}

      {isLoading && <p className="text-slate-600">Loading…</p>}
      {isError && <ErrorState title="Could not load notifications" onRetry={refetch} />}
      {!isLoading && !isError && notifications.length === 0 && (
        everyone || filtering ? (
          <p className="text-slate-600">Nothing matches that.</p>
        ) : archived ? (
          <EmptyState
            icon="archive"
            title="Nothing archived"
            description="Notifications you archive are kept here, and can be moved back to your inbox."
          />
        ) : (
          <EmptyState
            icon="bell"
            title="Your inbox is empty"
            description="New notifications appear here. Anything you archived is under Archived."
          />
        )
      )}

      <ul className="grid gap-2" aria-label={archived ? "Archived notifications" : "Notifications"}>
        {notifications.map((n) => {
          // On the everyone view a row is somebody else's mail: it is read,
          // not acted on, so it does not link anywhere and cannot be changed.
          const mine = !everyone || n.recipient === user?.id;
          const text = (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {!n.is_read && <span aria-hidden="true" className="h-2 w-2 rounded-full bg-brand-500" />}
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
                {n.archived_at && ` · archived ${new Date(n.archived_at).toLocaleString()}`}
                {everyone && n.action_url && ` · links to ${n.action_url}`}
              </p>
            </>
          );
          return (
            <li
              key={n.id}
              className={`flex items-start gap-2 rounded-lg border p-3 sm:p-4 ${
                n.is_read ? "border-slate-200 bg-white" : "border-brand-200 bg-brand-50"
              }`}
            >
              {selectable && mine && (
                <label className="-my-1.5 -ml-1.5 grid h-11 w-11 shrink-0 cursor-pointer place-items-center sm:h-9 sm:w-9">
                  <input
                    type="checkbox"
                    aria-label={`Select ${n.title}`}
                    checked={selected.has(n.id)}
                    onChange={() => toggle(n.id)}
                    className="h-4 w-4 rounded border-slate-400 text-brand-600 focus:ring-brand-500"
                  />
                </label>
              )}
              <div className="min-w-0 flex-1">
                {n.action_url && mine ? (
                  <Link
                    to={n.action_url}
                    onClick={() => !n.is_read && setRead.mutate({ id: n.id, isRead: true })}
                    className="block rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  >
                    {text}
                  </Link>
                ) : text}
                {mine && (
                  <div className="-ml-2 mt-1 flex flex-wrap gap-1">
                    <Button
                      variant="link" size="xs"
                      onClick={() => setRead.mutate({ id: n.id, isRead: !n.is_read })}
                    >
                      {n.is_read ? "Mark unread" : "Mark read"}
                    </Button>
                    {archived ? (
                      <Button variant="link" size="xs" onClick={() => restoreOne.mutate(n.id)}>
                        Restore to inbox
                      </Button>
                    ) : (
                      <Button variant="linkMuted" size="xs" onClick={() => archiveOne.mutate(n.id)}>
                        Archive
                      </Button>
                    )}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <Modal
        open={confirmArchiveAll}
        onClose={() => setConfirmArchiveAll(false)}
        title="Archive all notifications?"
        description="Everything in your inbox moves to Archived."
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirmArchiveAll(false)}>Cancel</Button>
            <Button onClick={() => archiveAll.mutate()} disabled={archiveAll.isPending}>
              Archive all
            </Button>
          </>
        }
      >
        <p className="text-sm text-slate-700">
          Nothing is deleted. Archived notifications stay on record, stop counting on the bell,
          and can be moved back to your inbox from the Archived tab.
        </p>
      </Modal>
    </Page>
  );
}
