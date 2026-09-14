import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { useAuth } from "../auth/AuthContext.jsx";
import { hasRole, REFUND_ROLES } from "../auth/roles.js";
import CancelServiceModal from "../components/CancelServiceModal.jsx";
import PatientPicker, { patientLabel } from "../components/PatientPicker.jsx";
import { chargeActions, serviceStatus } from "../components/refundPolicy.js";
import { Icon } from "../components/icons.jsx";
import {
  Alert, Badge, Button, Card, EmptyState, ErrorState, Input, MetaStat, Page, PageHeader,
  Select, SkeletonRows, Tab, TabBar, TextLink,
} from "../components/ui.jsx";

/**
 * Service Cancellations — ending a patient's financial responsibility for a
 * service they were billed for and never received.
 *
 * The distinction this page exists to make obvious, and which the backend
 * holds whatever the screen does:
 *
 *   Cancel service    — nothing was paid. The bill is withdrawn; no money moves.
 *   Cancel & refund   — money was paid. The bill is withdrawn *and* everything
 *                       paid for it goes back, in one transaction. The amount
 *                       is never typed: it is all of it.
 *
 * Money going back for a service the patient *did* receive — any part of a
 * payment, the bill staying active — is the Refunds desk, and is not offered
 * here at all.
 *
 * Every figure is the server's. The list is filtered and paginated by
 * `/charges/`, and services a department has withdrawn but that are still
 * billed are pinned to the top (`billing.withdrawn`): they are the ones that
 * actually need somebody to act. The patient is chosen with the shared
 * dropdown picker — browse the list or type a number, name or phone.
 */
const currency = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 })
    .format(Number(n ?? 0));

const day = (value) => (value
  ? new Date(value).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })
  : "—");

const PAGE_SIZE = 25;

export const FILTERS = [
  { key: "awaiting", label: "Awaiting cancellation", params: { awaiting: 1 } },
  { key: "all", label: "All services", params: {} },
  { key: "paid", label: "Paid", params: { status: "paid" } },
  { key: "partial", label: "Partially paid", params: { status: "partial" } },
  { key: "unpaid", label: "Unpaid", params: { status: "unpaid" } },
  { key: "cancelled", label: "Cancelled", params: { status: "cancelled" } },
];

/** The `/charges/` query for a filter state. Exported so a test can hold the page to it. */
export function listParams({ filter, patient = null, department = "", from = "", to = "", page = 1 }) {
  const chosen = FILTERS.find((f) => f.key === filter) ?? FILTERS[1];
  return {
    ...chosen.params,
    // `?patient=` takes the integer pk, like every other nested filter.
    ...(patient ? { patient } : {}),
    ...(department ? { department } : {}),
    ...(from ? { created_from: from } : {}),
    ...(to ? { created_to: to } : {}),
    awaiting_first: 1,
    page,
    page_size: PAGE_SIZE,
  };
}

export default function ServiceCancellations() {
  const { user } = useAuth();
  const [filter, setFilter] = useState("all");
  const [patient, setPatient] = useState(null);
  const [department, setDepartment] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [acting, setActing] = useState(null);   // { charge, mode }

  useEffect(() => { setPage(1); }, [filter, patient, department, from, to]);

  const summary = useQuery({
    // Under "charges" so every cancellation, which invalidates that prefix,
    // refreshes the figures in the header too.
    queryKey: ["charges", "cancellation-summary"],
    queryFn: () => api.get("/charges/cancellation-summary/").then((r) => r.data),
  });
  const departments = useQuery({
    queryKey: ["departments", "cancellation-filter"],
    queryFn: () => api.get("/departments/", { params: { page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
    staleTime: 5 * 60 * 1000,
  });

  const params = listParams({ filter, patient: patient?.id, department, from, to, page });
  const list = useQuery({
    queryKey: ["charges", "service-cancellations", params],
    queryFn: () => api.get("/charges/", { params }).then((r) => r.data),
  });

  const rows = list.data?.results ?? (Array.isArray(list.data) ? list.data : []);
  const count = list.data?.count ?? rows.length;
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const awaiting = summary.data?.awaiting;
  const narrowed = Boolean(patient || department || from || to);
  const mayRefund = hasRole(user, REFUND_ROLES);

  const clearFilters = () => { setPatient(null); setDepartment(""); setFrom(""); setTo(""); };

  return (
    <Page width="wide" className="space-y-5">
      <PageHeader
        className="mb-0"
        icon="receiptX"
        title="Service Cancellations"
        subtitle="Cancel a service the patient was billed for but never received. If they already paid for it, everything they paid goes back in the same step."
        meta={summary.data && (
          <>
            <MetaStat value={awaiting?.count ?? 0} label="awaiting cancellation"
                      tone={awaiting?.count > 0 ? "warning" : "neutral"} />
            <MetaStat value={currency(awaiting?.held)} label="paid on them" />
            <MetaStat value={summary.data.open ?? 0} label="open services" />
            <MetaStat value={summary.data.cancelled_this_month?.count ?? 0} label="cancelled this month" />
          </>
        )}
        toolbar={(
          <>
            <div className="min-w-0 sm:min-w-[18rem] sm:flex-1">
              <PatientPicker
                variant="dropdown"
                value={patient}
                onChange={setPatient}
                placeholder="Select a patient"
                searchPlaceholder="Search by NMHS number, name or phone…"
                emptyMessage="No patient matches that."
              />
            </div>
            <Select aria-label="Department" className="sm:w-48" value={department}
                    onChange={(e) => setDepartment(e.target.value)}>
              <option value="">All departments</option>
              {(departments.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </Select>
            <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center">
              <Input type="date" aria-label="Billed from" value={from} max={to || undefined}
                     onChange={(e) => setFrom(e.target.value)} />
              <Input type="date" aria-label="Billed to" value={to} min={from || undefined}
                     onChange={(e) => setTo(e.target.value)} />
            </div>
            {narrowed && (
              <Button variant="link" size="sm" onClick={clearFilters}>Clear filters</Button>
            )}
          </>
        )}
      />

      <section className="grid gap-3 md:grid-cols-2" aria-label="Which cancellation to use">
        <Explainer icon="receiptX" title="Cancel service" tone="text-slate-700 bg-slate-100 ring-slate-200">
          <strong>Nothing was paid.</strong> The bill is withdrawn and the patient owes nothing for
          it. No money moves.
        </Explainer>
        <Explainer icon="refund" title="Cancel & refund" tone="text-amber-800 bg-amber-50 ring-amber-200">
          <strong>Money was paid.</strong> The bill is withdrawn and everything paid for it goes
          back, in one step. The payment and the refund both stay on the record.
        </Explainer>
      </section>
      {mayRefund && (
        <p className="-mt-2 text-sm text-slate-600">
          Giving back part of a payment for a service the patient <em>did</em> receive?{" "}
          <TextLink to="/refunds">Use Refunds</TextLink> — the service stays active.
        </p>
      )}

      {awaiting?.count > 0 && filter !== "awaiting" && (
        <Alert tone="warning"
               title={`${awaiting.count} ${awaiting.count === 1 ? "service was" : "services were"} withdrawn but ${awaiting.count === 1 ? "is" : "are"} still billed`}>
          The department took {awaiting.count === 1 ? "it" : "them"} back after the patient was
          billed. Cancel {awaiting.count === 1 ? "it" : "them"} so nobody pays for a service they
          did not receive.
          <div className="mt-2">
            <Button size="sm" variant="secondary" onClick={() => setFilter("awaiting")}>
              Review {awaiting.count === 1 ? "it" : "them"}
            </Button>
          </div>
        </Alert>
      )}

      <Card>
        <div className="px-4 pt-1 sm:px-5">
          <TabBar label="Filter services" className="!mb-0">
            {FILTERS.map((f) => (
              <Tab key={f.key} active={filter === f.key} onClick={() => setFilter(f.key)}>
                {f.label}
                {f.key === "awaiting" && awaiting?.count > 0 && (
                  <span className="ml-1.5 rounded-full bg-amber-100 px-1.5 text-xs font-semibold text-amber-800">
                    {awaiting.count}
                  </span>
                )}
              </Tab>
            ))}
          </TabBar>
        </div>

        {list.isLoading && <div className="p-4 sm:p-5"><SkeletonRows rows={4} /></div>}

        {list.isError && (
          <div className="p-4 sm:p-5">
            <ErrorState
              title="The services could not be loaded."
              description={list.error?.response?.status === 403
                ? "Your role cannot open the Service Cancellations desk."
                : "Check the connection and try again."}
              onRetry={() => list.refetch()}
            />
          </div>
        )}

        {!list.isLoading && !list.isError && rows.length === 0 && (
          <EmptyState
            icon="receiptX"
            title={filter === "awaiting" && !narrowed
              ? "Nothing is waiting to be cancelled."
              : patient ? `No services match for ${patientLabel(patient)}.` : "No services match these filters."}
            description={filter === "awaiting" && !narrowed
              ? "No department has withdrawn a service that is still billed. To cancel anything else, choose the patient under All services."
              : "Choose a different patient, or clear the filters."}
            action={filter !== "all" || narrowed ? (
              <Button variant="secondary" size="sm"
                      onClick={() => { clearFilters(); setFilter("all"); }}>
                Show all services
              </Button>
            ) : null}
          />
        )}

        {rows.length > 0 && (
          <ul className="divide-y divide-slate-100" aria-label="Services">
            {rows.map((charge) => (
              <ServiceRow key={charge.id} charge={charge} user={user}
                          onAct={(mode) => setActing({ charge, mode })} />
            ))}
          </ul>
        )}

        {pages > 1 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-sm text-slate-600 sm:px-5">
            <span>Page {page} of {pages} · {count} services</span>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" disabled={page <= 1}
                      onClick={() => setPage((p) => p - 1)}>Previous</Button>
              <Button size="sm" variant="secondary" disabled={page >= pages}
                      onClick={() => setPage((p) => p + 1)}>Next</Button>
            </div>
          </div>
        )}
      </Card>

      {acting && (
        <CancelServiceModal
          charge={acting.charge} mode={acting.mode} onClose={() => setActing(null)}
        />
      )}
    </Page>
  );
}

function Explainer({ icon, title, tone, children }) {
  return (
    <Card className="flex gap-3 p-4">
      <span aria-hidden="true" className={`grid h-9 w-9 shrink-0 place-items-center rounded-[10px] ring-1 ring-inset ${tone}`}>
        <Icon name={icon} className="h-[18px] w-[18px]" />
      </span>
      <div className="min-w-0">
        <p className="font-semibold text-slate-900">{title}</p>
        <p className="mt-0.5 text-sm text-slate-700">{children}</p>
      </div>
    </Card>
  );
}

function ServiceRow({ charge, user, onAct }) {
  const status = serviceStatus(charge);
  const actions = chargeActions(charge, user, { format: currency });
  const cancelled = charge.status === "cancelled";
  const refunded = Number(charge.amount_refunded ?? 0);

  return (
    <li className="grid gap-3 px-4 py-4 sm:px-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1.5fr)_minmax(0,auto)] lg:items-center lg:gap-6">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className={`font-medium ${cancelled ? "text-slate-500 line-through" : "text-slate-900"}`}>
            {charge.description}
          </p>
          <Badge tone={status.tone}>{status.label}</Badge>
          {charge.service_withdrawn && (
            <Badge tone="warning">Withdrawn by {charge.department_name ?? "the department"}</Badge>
          )}
        </div>
        <p className="mt-1 text-sm text-slate-800">
          {charge.patient_uuid
            ? <TextLink to={`/patients/${charge.patient_uuid}/billing`}>{charge.patient_name}</TextLink>
            : charge.patient_name}
          {charge.patient_number && <span className="text-slate-600"> · {charge.patient_number}</span>}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-slate-600">
          <span>{charge.department_name ?? "No department"}</span>
          <span aria-hidden="true" className="text-slate-400">·</span>
          <span>Billed {day(charge.created_at)}</span>
        </p>
        {cancelled && (
          <p className="mt-1 text-sm text-slate-600">
            Cancelled{charge.cancelled_by_name ? ` by ${charge.cancelled_by_name}` : ""}
            {charge.cancelled_at ? ` on ${day(charge.cancelled_at)}` : ""}
            {charge.cancellation_reason ? ` — ${charge.cancellation_reason}` : ""}
          </p>
        )}
      </div>

      {/* In the order a cashier reads them: what it cost, what came in, what
          already went back, what is still owed. */}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
        <Amount label="Charged" value={charge.amount} />
        <Amount label="Paid" value={charge.amount_paid} />
        <Amount label="Refunded" value={refunded} tone={refunded > 0 ? "text-amber-800" : undefined} />
        <Amount label="Outstanding" value={charge.outstanding}
                tone={Number(charge.outstanding) > 0 ? "text-red-700" : "text-emerald-700"} />
      </dl>

      <div className="flex min-w-0 flex-wrap gap-2 lg:justify-end">
        {actions.visible && actions.cancelAndRefund && (
          <Button size="sm" variant="danger" onClick={() => onAct("cancel_and_refund")}>
            <Icon name="refund" className="h-4 w-4" aria-hidden="true" />
            Cancel &amp; refund {currency(actions.refundable)}
          </Button>
        )}
        {actions.visible && actions.cancel && (
          <Button size="sm" variant="dangerOutline" onClick={() => onAct("cancel")}>
            <Icon name="receiptX" className="h-4 w-4" aria-hidden="true" />
            Cancel service
          </Button>
        )}
        {actions.visible && !cancelled && !actions.cancel && !actions.cancelAndRefund && actions.reason && (
          <p className="text-sm text-slate-600 lg:max-w-[15rem] lg:text-right">{actions.reason}</p>
        )}
      </div>
    </li>
  );
}

function Amount({ label, value, tone }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={`tabular-nums font-medium ${tone ?? "text-slate-800"}`}>{currency(value)}</dd>
    </div>
  );
}
