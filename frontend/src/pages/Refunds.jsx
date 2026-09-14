import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import api from "../api/client";
import { useAuth } from "../auth/AuthContext.jsx";
import { CANCEL_ROLES, hasRole } from "../auth/roles.js";
import PatientPicker from "../components/PatientPicker.jsx";
import RefundAction from "../components/RefundAction.jsx";
import { patientNumber } from "../components/patientIdentity.js";
import {
  Badge, Button, Card, CardHeader, EmptyState, ErrorState, MetaStat, Page, PageHeader,
  SearchInput, Select, SkeletonRows, Tab, TabBar, TextLink,
} from "../components/ui.jsx";

/**
 * Refunds — money going back to a patient.
 *
 * Find the patient, pick the payment, refund all or part of what is left on
 * it. The original payment is never touched: the refund is recorded beside it,
 * and the bill it settled is reopened, because the service was delivered.
 *
 * Everything here goes through the refund entry point every other screen uses —
 * `RefundAction` → `RefundModal` → `POST /payments/<id>/refund/` →
 * `billing.services.refund_payment`. There is no second implementation.
 *
 * Cancelling a service the patient never received is a different decision and
 * lives on its own desk (`/service-cancellations`): it withdraws the bill as
 * well, and returns *all* of the money rather than a chosen amount.
 */
const currency = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 })
    .format(Number(n ?? 0));

const when = (value) => (value
  ? new Date(value).toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  })
  : "—");

const METHOD = { cash: "Cash", card: "Card", transfer: "Transfer", insurance: "Insurance" };
const CHANNEL = { front_desk: "Front desk", pharmacy: "Pharmacy", cashier: "Cashier" };
const PAGE_SIZE = 25;

function useDebounced(value, delay = 300) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

export default function Refunds() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "history" ? "history" : "refund";

  const summary = useQuery({
    queryKey: ["refunds", "summary"],
    queryFn: () => api.get("/refunds/summary/").then((r) => r.data),
  });

  return (
    <Page width="wide" className="space-y-5">
      <PageHeader
        className="mb-0"
        icon="refund"
        title="Refunds"
        subtitle="Money going back to a patient. Refund all or part of a payment — the original payment stays on the record and the refund is recorded beside it."
        meta={summary.data && (
          <>
            <MetaStat value={currency(summary.data.today?.amount)} label="refunded today" />
            <MetaStat value={summary.data.today?.count ?? 0} label="refunds today" />
            <MetaStat value={currency(summary.data.month?.amount)} label="this month" />
          </>
        )}
      />

      <TabBar label="Refunds" className="!mb-0">
        <Tab active={tab === "refund"} onClick={() => setSearchParams({})}>Refund a payment</Tab>
        <Tab active={tab === "history"} onClick={() => setSearchParams({ tab: "history" })}>
          Refund history
        </Tab>
      </TabBar>

      {tab === "refund" ? <RefundAPayment user={user} /> : <RefundHistory />}
    </Page>
  );
}

// ---------------------------------------------------------------------------
// Refund a payment
// ---------------------------------------------------------------------------

function RefundAPayment({ user }) {
  const [patient, setPatient] = useState(null);

  if (!patient) {
    return (
      <div className="space-y-3">
        <Card>
          <CardHeader
            title="Whose payment is going back?"
            description="Open the list to choose a patient, or search it by NMHS number, name or phone number."
          />
          <div className="px-4 py-4 sm:px-5">
            <PatientPicker
              variant="dropdown"
              value={null}
              onChange={(p) => p && setPatient(p)}
              placeholder="Select a patient"
              searchPlaceholder="Search by NMHS number, name or phone…"
              emptyMessage="No patient matches that."
            />
          </div>
        </Card>
        {hasRole(user, CANCEL_ROLES) && (
          <p className="text-sm text-slate-600">
            Patient never received the service?{" "}
            <TextLink to="/service-cancellations">Service Cancellations</TextLink> withdraws the
            bill and returns everything paid for it in one step.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <Card className="flex flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-5">
        <div className="min-w-0">
          <p className="font-semibold text-slate-900">{patient.last_name}, {patient.first_name}</p>
          <p className="text-sm text-slate-600">{patientNumber(patient)}</p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => setPatient(null)}>Different patient</Button>
      </Card>
      <PatientPayments patient={patient} />
      <PatientRefunds patient={patient} />
    </div>
  );
}

function PatientPayments({ patient }) {
  const payments = useQuery({
    queryKey: ["payments", "refunds-desk", patient.id],
    queryFn: () => api.get("/payments/", { params: { patient: patient.id, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });
  const rows = payments.data ?? [];
  const refundable = rows.filter((p) => Number(p.refundable_balance) > 0);
  const left = refundable.reduce((sum, p) => sum + Number(p.refundable_balance), 0);

  return (
    <Card>
      <CardHeader
        title="Payments"
        description="Pick the payment the money is going back from. Any amount up to what is left on it can be refunded."
        actions={rows.length > 0 && (
          <Badge tone={refundable.length ? "brand" : "neutral"}>
            {refundable.length ? `${currency(left)} refundable` : "Nothing refundable"}
          </Badge>
        )}
      />
      {payments.isLoading && <div className="p-4 sm:p-5"><SkeletonRows rows={3} /></div>}
      {payments.isError && (
        <div className="p-4 sm:p-5">
          <ErrorState title="The payments could not be loaded." onRetry={() => payments.refetch()} />
        </div>
      )}
      {!payments.isLoading && !payments.isError && rows.length === 0 && (
        <EmptyState icon="cash" title="No payments on this account."
                    description="There is nothing to refund until a payment has been taken." />
      )}
      {rows.length > 0 && (
        <ul className="divide-y divide-slate-100" aria-label="Payments">
          {rows.map((payment) => <PaymentRow key={payment.id} payment={payment} patient={patient} />)}
        </ul>
      )}
    </Card>
  );
}

function PaymentRow({ payment, patient }) {
  const refunded = Number(payment.amount_refunded ?? 0);
  const remaining = Number(payment.refundable_balance ?? 0);
  const state = remaining <= 0
    ? { label: "FULLY REFUNDED", tone: "neutral" }
    : refunded > 0 ? { label: "PARTLY REFUNDED", tone: "warning" } : null;

  return (
    <li className="grid gap-3 px-4 py-4 sm:px-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1.4fr)_auto] lg:items-center lg:gap-6">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium text-slate-900">Payment of {currency(payment.amount)}</p>
          {state && <Badge tone={state.tone}>{state.label}</Badge>}
        </div>
        <p className="mt-0.5 text-sm text-slate-600">
          {when(payment.created_at)} · {METHOD[payment.method] ?? payment.method}
          {payment.channel ? ` · ${CHANNEL[payment.channel] ?? payment.channel}` : ""}
        </p>
        <p className="text-sm text-slate-600">
          Received by {payment.received_by_name ?? "—"}
          {payment.reference ? ` · Ref ${payment.reference}` : ""}
        </p>
      </div>
      <dl className="grid grid-cols-3 gap-x-4 text-sm">
        <Figure label="Original payment" value={currency(payment.amount)} />
        <Figure label="Already refunded" value={currency(refunded)}
                tone={refunded > 0 ? "text-amber-800" : undefined} />
        <Figure label="Remaining" value={currency(remaining)}
                tone={remaining > 0 ? "text-slate-900" : "text-slate-500"} />
      </dl>
      <div className="flex lg:justify-end">
        <RefundAction payment={payment} patient={patient} />
      </div>
    </li>
  );
}

function PatientRefunds({ patient }) {
  const refunds = useQuery({
    queryKey: ["refunds", "patient", patient.id],
    queryFn: () => api.get("/refunds/", { params: { patient: patient.id, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });
  const rows = refunds.data ?? [];

  return (
    <Card>
      <CardHeader title="Refunds already made to this patient"
                  description="Newest first. Each one names the payment it came from." />
      {refunds.isLoading && <div className="p-4 sm:p-5"><SkeletonRows rows={2} /></div>}
      {!refunds.isLoading && rows.length === 0 && (
        <p className="px-4 py-5 text-sm text-slate-600 sm:px-5">No refunds have been made to this patient.</p>
      )}
      {rows.length > 0 && (
        <ul className="divide-y divide-slate-100" aria-label="Refunds to this patient">
          {rows.map((refund) => <RefundRow key={refund.id} refund={refund} />)}
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Refund history — the register
// ---------------------------------------------------------------------------

function RefundHistory() {
  const [search, setSearch] = useState("");
  const [method, setMethod] = useState("");
  const [page, setPage] = useState(1);
  const term = useDebounced(search);
  useEffect(() => { setPage(1); }, [term, method]);

  const params = {
    ...(term.trim() ? { search: term.trim() } : {}),
    ...(method ? { method } : {}),
    page, page_size: PAGE_SIZE,
  };
  const register = useQuery({
    queryKey: ["refunds", "register", params],
    queryFn: () => api.get("/refunds/", { params }).then((r) => r.data),
  });
  const rows = register.data?.results ?? (Array.isArray(register.data) ? register.data : []);
  const count = register.data?.count ?? rows.length;
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));

  return (
    <Card>
      <CardHeader
        title="Refund history"
        description="Every refund, newest first — why, how the money went back, who processed it and which payment it came from."
      />
      <div className="flex flex-col gap-2 border-b border-slate-100 px-4 py-3 sm:flex-row sm:px-5">
        <SearchInput className="sm:flex-1" value={search} onChange={setSearch} label="Search refunds"
                     placeholder="NMHS number, patient name, reason or reference…" />
        <Select aria-label="Refund method" className="sm:w-44" value={method}
                onChange={(e) => setMethod(e.target.value)}>
          <option value="">Every method</option>
          {Object.entries(METHOD).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </Select>
      </div>

      {register.isLoading && <div className="p-4 sm:p-5"><SkeletonRows rows={4} /></div>}
      {register.isError && (
        <div className="p-4 sm:p-5">
          <ErrorState title="The refund history could not be loaded." onRetry={() => register.refetch()} />
        </div>
      )}
      {!register.isLoading && !register.isError && rows.length === 0 && (
        <EmptyState icon="refund"
                    title={term || method ? "No refund matches that." : "No refunds have been made yet."} />
      )}
      {rows.length > 0 && (
        <ul className="divide-y divide-slate-100" aria-label="Refund history">
          {rows.map((refund) => <RefundRow key={refund.id} refund={refund} showPatient />)}
        </ul>
      )}
      {pages > 1 && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-sm text-slate-600 sm:px-5">
          <span>Page {page} of {pages} · {count} refunds</span>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" disabled={page <= 1}
                    onClick={() => setPage((p) => p - 1)}>Previous</Button>
            <Button size="sm" variant="secondary" disabled={page >= pages}
                    onClick={() => setPage((p) => p + 1)}>Next</Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function RefundRow({ refund, showPatient = false }) {
  const services = (refund.allocations ?? []).map((a) => a.charge_description).filter(Boolean);
  return (
    <li className="grid gap-3 px-4 py-4 sm:px-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1.6fr)] lg:gap-6">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-semibold tabular-nums text-amber-800">{currency(refund.amount)} refunded</p>
          <Badge tone="success">Completed</Badge>
          {refund.from_cancellation && <Badge tone="neutral">Service cancelled</Badge>}
        </div>
        {showPatient && (
          <p className="mt-1 text-sm text-slate-800">
            {refund.patient_name ?? "Walk-in customer (POS)"}
            {refund.patient_number && <span className="text-slate-600"> · {refund.patient_number}</span>}
          </p>
        )}
        <p className="mt-1 text-sm text-slate-700">{refund.reason}</p>
        <p className="mt-0.5 text-sm text-slate-600">Refund #{refund.id} · {when(refund.created_at)}</p>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
        <Figure label="Method" value={refund.method_label ?? METHOD[refund.method] ?? refund.method} />
        <Figure label="Reference" value={refund.reference || "—"} />
        <Figure label="From payment"
                value={`${currency(refund.payment_amount)} · ${when(refund.payment_date).split(",")[0]}`} />
        <Figure label="Processed by" value={refund.processed_by_name ?? "—"} />
        {services.length > 0 && (
          <div className="col-span-2 min-w-0 sm:col-span-4">
            <dt className="text-xs text-slate-500">Came back off</dt>
            <dd className="text-slate-800">{services.join(", ")}</dd>
          </div>
        )}
      </dl>
    </li>
  );
}

function Figure({ label, value, tone }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={`break-words font-medium tabular-nums ${tone ?? "text-slate-800"}`}>{value}</dd>
    </div>
  );
}
