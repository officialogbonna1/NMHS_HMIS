import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";
import { Alert, Badge, Card, CardHeader, EmptyState, ErrorState, Page, PageHeader, Skeleton, SkeletonRows, Button } from "../components/ui.jsx";
import { Icon } from "../components/icons.jsx";

// The first screen everybody sees, so it is the one that sets the tone.
//
// It used to be gradient-bordered tiles on a dark hero panel — the look of a
// generic admin template. What a ward actually needs is the numbers legible at
// a glance and each one linking to the page it counts. Colour is kept for
// meaning (a count that needs attention) rather than decoration.
//
// The payload is unchanged: `/dashboard/` still returns cards, alerts, tasks
// and recent_activity exactly as before.

const TONES = {
  blue: "text-brand-700", violet: "text-violet-700", green: "text-emerald-700",
  amber: "text-amber-700", red: "text-red-700", slate: "text-slate-900",
};

const formatValue = (card) =>
  card.format === "currency"
    ? new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(card.value ?? 0)
    : (card.value ?? 0);

const PRIORITY_TONE = { emergency: "danger", urgent: "warning" };

export default function Dashboard() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get("/dashboard/").then((response) => response.data),
  });

  if (isLoading) return <DashboardSkeleton />;

  if (isError) {
    return (
      <Page width="wide">
        <ErrorState
          title="Could not load the dashboard."
          description="The server did not answer. Your work is not affected."
          onRetry={refetch}
        />
      </Page>
    );
  }

  return (
    <Page width="wide">
      <PageHeader
        icon="home"
        title={data.is_admin ? "Hospital operations" : `My ${data.role} workspace`}
        subtitle={data.is_admin
          ? "Care delivery, finances, capacity and activity, as they stand now."
          : "Your work queue, patient updates and department tasks."}
        meta={<span className="text-sm text-slate-600">{data.today}</span>}
        actions={
          <Button variant="soft" onClick={() => refetch()} disabled={isFetching}>
            <Icon name="clock" className={`h-4 w-4 shrink-0 ${isFetching ? "animate-spin" : ""}`} aria-hidden="true" />
            <span>{isFetching ? "Refreshing…" : "Refresh"}</span>
          </Button>
        }
      />

      {/* Two across on the smallest phone: these are short numbers, and one
          per row turned a six-card set into a scroll. */}
      <section aria-label="Key figures" className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-4">
        {data.cards.map((card) => (
          <Link
            key={card.key ?? card.label}
            to={card.href}
            className="group flex min-w-0 flex-col justify-between rounded-xl border border-slate-200 bg-white p-3 transition hover:border-brand-300 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:p-4"
          >
            <p className="text-sm leading-snug text-slate-600">{card.label}</p>
            <p className={`mt-1.5 text-xl font-semibold tabular-nums tracking-tight sm:text-2xl ${TONES[card.tone] ?? TONES.slate}`}>
              {formatValue(card)}
            </p>
          </Link>
        ))}
      </section>

      {data.alerts?.length > 0 && (
        <Alert tone="warning" className="mt-5" title="Needs attention">
          <ul className="mt-1.5 space-y-1">
            {data.alerts.map((alert) => (
              <li key={alert.label}>
                <Link
                  to={alert.href}
                  className="inline-flex min-h-[36px] items-center gap-1.5 rounded font-medium text-amber-900 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
                >
                  {alert.label}
                  <Icon name="chevronRight" className="h-4 w-4" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        </Alert>
      )}

      <div className="mt-5 grid gap-5 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader
            title={data.is_admin ? "Active hospital queues" : "My active queue"}
            actions={<Badge tone="success">Live</Badge>}
          />
          {data.tasks.length ? (
            <ul className="divide-y divide-slate-100">
              {data.tasks.map((task) => (
                <li key={task.id}>
                  <Link
                    to={task.href ?? "/patients"}
                    className="flex items-start justify-between gap-3 px-4 py-3 transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 sm:px-5"
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-slate-900">{task.patient}</span>
                      <span className="mt-0.5 block text-sm text-slate-600">
                        {task.purpose ? `${task.purpose} · ` : ""}{task.department} · {task.assigned_to}
                      </span>
                    </span>
                    <Badge tone={PRIORITY_TONE[task.priority] ?? "neutral"}>{task.priority}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="Nothing waiting"
              description="No active items are assigned to this workspace."
            />
          )}
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="Recent activity" />
          {data.recent_activity.length ? (
            <ul className="divide-y divide-slate-100">
              {data.recent_activity.map((event) => (
                <li key={event.id} className="px-4 py-3 sm:px-5">
                  <p className="text-sm font-medium text-slate-800">{event.action}</p>
                  {event.actor && <p className="mt-0.5 text-sm text-slate-600">{event.actor}</p>}
                  <p className="mt-0.5 text-xs text-slate-500">
                    {new Date(event.created_at).toLocaleString()}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No activity yet" description="Actions taken in the system appear here." />
          )}
        </Card>
      </div>
    </Page>
  );
}

function DashboardSkeleton() {
  return (
    <Page width="wide">
      <Skeleton className="h-7 w-64" />
      <Skeleton className="mt-2 h-4 w-80" />
      <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="rounded-xl border border-slate-200 bg-white p-4">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="mt-2.5 h-7 w-16" />
          </div>
        ))}
      </div>
      <div className="mt-5 grid gap-5 lg:grid-cols-5">
        <div className="rounded-xl border border-slate-200 bg-white p-5 lg:col-span-3"><SkeletonRows rows={4} /></div>
        <div className="rounded-xl border border-slate-200 bg-white p-5 lg:col-span-2"><SkeletonRows rows={3} /></div>
      </div>
    </Page>
  );
}
