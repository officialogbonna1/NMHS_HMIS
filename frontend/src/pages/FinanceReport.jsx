import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import api from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  Alert, Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, Input,
  Page, PageHeader, Skeleton, Table, TableWrap, Td, Th, THead, Tr, naira,
} from "../components/ui.jsx";
import { Icon } from "../components/icons.jsx";

/**
 * The hospital's money over a date range.
 *
 * Every figure on this page is computed by `apps/billing/reporting.py` and
 * arrives in one `GET /finance/report/`. Nothing here adds anything up: a
 * dashboard that sums a page of transactions in the browser is wrong the
 * moment there is a second page, and hospital-wide revenue is not something
 * to download row by row.
 *
 * **Two bases, kept apart, each labelled.** The page reports the same period
 * two ways and never blends them, because they answer different questions and
 * only one of them reconciles:
 *
 *   - *Collected* — money that actually arrived between the two dates. This
 *     is Total Revenue. A discount is not revenue and a waiver is not
 *     revenue: nobody handed anything over.
 *   - *Billed* — the charges raised between the two dates and what has become
 *     of them since. This is the block where
 *     gross − discounts − waivers − collected = outstanding closes exactly.
 *
 * One page, two audiences (rule 29's shape): a cashier and an admin see the
 * same tables, and the cashier additionally sees what their own desk took,
 * because that is the figure they reconcile at the end of a shift.
 */

const PRESETS = [
  ["today", "Today"],
  ["yesterday", "Yesterday"],
  ["week", "This week"],
  ["month", "This month"],
  ["last_month", "Last month"],
  ["year", "This year"],
];

const STATUS_TONE = {
  paid: "success", partial: "warning", unpaid: "danger",
  waived: "neutral", deferred: "warning", cancelled: "neutral",
};
const STATUS_LABEL = {
  paid: "Paid", partial: "Part paid", unpaid: "Unpaid",
  waived: "Waived", deferred: "Pay later", cancelled: "Cancelled",
};

const money = (value) => naira(Number(value ?? 0));

export default function FinanceReport() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();

  const preset = params.get("preset") || "today";
  const from = params.get("from") || "";
  const to = params.get("to") || "";

  const query = useQuery({
    queryKey: ["finance-report", preset, from, to],
    queryFn: () =>
      api.get("/finance/report/", {
        params: { preset, ...(preset === "custom" ? { from, to } : {}) },
      }).then((response) => response.data),
    // Money moves while you are looking at it, but not so fast that a
    // refetch on every window focus is useful.
    staleTime: 30_000,
  });

  const { data, isLoading, isError, error, refetch, isFetching } = query;

  const setRange = (next) => {
    const merged = { preset: next.preset ?? preset };
    if (merged.preset === "custom") {
      merged.from = next.from ?? from;
      merged.to = next.to ?? to;
    }
    setParams(merged, { replace: true });
  };

  if (isError) {
    return (
      <Page width="wide">
        <PageHeader icon="cash" title="Financial report" />
        <ErrorState
          title="Could not load the financial report."
          description={error?.response?.data?.detail ?? "The server did not answer."}
          onRetry={refetch}
        />
      </Page>
    );
  }

  const isCashDesk = ["cashier", "accountant"].includes(user?.role);

  return (
    <Page width="wide">
      <PageHeader
        icon="cash"
        title={isCashDesk ? "Collections & revenue" : "Hospital finances"}
        subtitle={
          isCashDesk
            ? "What this desk and the hospital took, what was written off, and what is still owed."
            : "Revenue collected, amounts billed, discounts, waivers and outstanding balances across every department."
        }
        meta={
          data && (
            <span className="text-sm text-slate-600">
              {data.period.label}
              {" · "}
              {data.period.from === data.period.to
                ? data.period.from
                : `${data.period.from} to ${data.period.to}`}
            </span>
          )
        }
        actions={
          <Button variant="soft" onClick={() => refetch()} disabled={isFetching}>
            <Icon name="clock" className={`h-4 w-4 shrink-0 ${isFetching ? "animate-spin" : ""}`} aria-hidden="true" />
            <span>{isFetching ? "Refreshing…" : "Refresh"}</span>
          </Button>
        }
        toolbar={<DateFilter preset={preset} from={from} to={to} onChange={setRange} />}
      />

      {isLoading || !data ? <ReportSkeleton /> : (
        <div className="space-y-5">
          <FacilityRevenue revenue={data.facility_revenue} />
          <SummaryCards data={data} showDesk={isCashDesk} />
          <ReconciliationNote data={data} />

          <div className="grid gap-5 lg:grid-cols-5">
            <RevenueChart rows={data.departments} total={data.collections.total} className="lg:col-span-3" />
            <MethodBreakdown collections={data.collections} className="lg:col-span-2" />
          </div>

          <DepartmentTable rows={data.departments} total={data.totals} />
          <PosSalesBlock pos={data.pos_sales} />
          <TransactionTable rows={data.transactions} period={data.period} />
        </div>
      )}
    </Page>
  );
}

/* ------------------------------------------------------------------ filter */

/**
 * One range for the whole page. Presets are the common questions; the custom
 * pair is for everything else, and it only applies once both dates are set —
 * a half-filled range would otherwise refetch on every keystroke and answer a
 * question nobody asked.
 */
function DateFilter({ preset, from, to, onChange }) {
  const [draftFrom, setDraftFrom] = useState(from);
  const [draftTo, setDraftTo] = useState(to);
  const custom = preset === "custom";
  const today = new Date().toISOString().slice(0, 10);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Reporting period">
        {PRESETS.map(([key, label]) => (
          <Button
            key={key}
            size="sm"
            variant={preset === key ? "primary" : "secondary"}
            aria-pressed={preset === key}
            onClick={() => onChange({ preset: key })}
          >
            {label}
          </Button>
        ))}
        <Button
          size="sm"
          variant={custom ? "primary" : "secondary"}
          aria-pressed={custom}
          onClick={() => onChange({ preset: "custom", from: draftFrom || today, to: draftTo || today })}
        >
          Custom range
        </Button>
      </div>

      {custom && (
        <form
          className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3 sm:flex-row sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            onChange({ preset: "custom", from: draftFrom, to: draftTo });
          }}
        >
          <Field label="From" className="min-w-0 sm:w-48">
            <Input type="date" value={draftFrom} max={draftTo || today}
                   onChange={(event) => setDraftFrom(event.target.value)} />
          </Field>
          <Field label="To" className="min-w-0 sm:w-48">
            <Input type="date" value={draftTo} min={draftFrom} max={today}
                   onChange={(event) => setDraftTo(event.target.value)} />
          </Field>
          <Button type="submit" size="sm" disabled={!draftFrom || !draftTo}>Apply</Button>
        </form>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- summary */

/**
 * The headline figures.
 *
 * Ordered so the two bases do not read as one list: what came in, then what
 * was billed and what became of it. Each card says which it is in its own
 * hint line rather than relying on the reader to remember.
 */
/**
 * Total Facility Revenue: every successful payment ever received, less every
 * refund ever processed, across the whole facility.
 *
 * Deliberately *not* part of the period above. It reads `facility_revenue`,
 * which the server computes with no date range, so choosing Today or last
 * month never moves it. Discounts, waivers and anything still owed are not in
 * it — that money was never received.
 */
function FacilityRevenue({ revenue }) {
  if (!revenue) return null;
  return (
    <section
      aria-label="Total Facility Revenue"
      className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 sm:p-5"
    >
      <p className="text-sm font-medium text-slate-700">Total Facility Revenue</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-emerald-800 sm:text-3xl">
        {money(revenue.total)}
      </p>
      <p className="mt-1 text-sm text-slate-600">
        Net payments retained by the facility since records began
      </p>
    </section>
  );
}

function SummaryCards({ data, showDesk }) {
  const { collections, charges, outstanding_now: outstandingNow } = data;

  const cards = [
    {
      key: "revenue", label: "Gross payments received", value: money(collections.total),
      hint: "Payments received in this period", tone: "green", href: "/transactions",
    },
    // Refunds are their own category, never netted into the figure above:
    // the payments did arrive, and a period that hides what went back out
    // cannot be reconciled against the drawer.
    {
      key: "refunds", label: "Refunds", value: money(collections.refunds),
      hint: `${collections.refunds_count ?? 0} refund(s) processed in this period`,
      tone: "amber", href: "/waivers",
    },
    {
      key: "net_revenue", label: "Net revenue retained", value: money(collections.net),
      hint: "Gross payments less refunds", tone: "green", href: "/transactions",
    },
    showDesk && {
      key: "desk", label: "Taken at this desk", value: money(collections.my_desk),
      hint: `${collections.my_desk_transactions ?? 0} of ${collections.transactions} transactions`,
      tone: "blue", href: "/billing",
    },
    {
      key: "part", label: "Part payments", value: money(collections.part_payments),
      hint: `Collected on ${collections.part_paid_charges} bill(s) still owing`,
      tone: "amber", href: "/outstanding",
    },
    {
      key: "transactions", label: "Transactions", value: collections.transactions,
      hint: "Payments recorded in this period", tone: "slate", href: "/transactions",
    },
    {
      key: "gross", label: "Gross charges", value: money(charges.gross),
      hint: `${charges.count} bill(s) raised in this period`, tone: "slate", href: "/billing",
    },
    // The reconciling figures, so these cards, the department table and the
    // reconciliation strip below all say the same thing. What was *approved*
    // in the period is a different question and is answered under the strip.
    {
      key: "discounts", label: "Total discounts", value: money(charges.discounts),
      hint: "Written off on bills raised in this period", tone: "violet", href: "/waivers",
    },
    {
      key: "waivers", label: "Total waived", value: money(charges.waivers),
      hint: "Waived on bills raised in this period", tone: "violet", href: "/waivers",
    },
    {
      key: "net", label: "Net amount due", value: money(charges.net_due),
      hint: "Gross less discounts, waivers and returns", tone: "slate", href: "/billing",
    },
    {
      key: "outstanding", label: "Outstanding, this period", value: money(charges.outstanding),
      hint: "Unsettled on bills raised in this period", tone: "red", href: "/outstanding",
    },
    {
      key: "owed", label: "Total outstanding, all time", value: money(outstandingNow),
      hint: "Everything the hospital is owed today", tone: "red", href: "/outstanding",
    },
  ].filter(Boolean);

  return (
    <section aria-label="Financial summary" className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
      {cards.map((card) => (
        <Link
          key={card.key}
          to={card.href}
          className="group flex min-w-0 flex-col justify-between rounded-xl border border-slate-200 bg-white p-3 transition hover:border-brand-300 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:p-4"
        >
          <p className="text-sm leading-snug text-slate-600">{card.label}</p>
          <p className={`mt-1.5 text-lg font-semibold tabular-nums tracking-tight sm:text-xl ${TONES[card.tone]}`}>
            {card.value}
          </p>
          <p className="mt-1 text-xs leading-snug text-slate-500">{card.hint}</p>
        </Link>
      ))}
    </section>
  );
}

const TONES = {
  blue: "text-brand-700", violet: "text-violet-700", green: "text-emerald-700",
  amber: "text-amber-700", red: "text-red-700", slate: "text-slate-900",
};

/** The identity spelled out, so the table above can be checked rather than trusted. */
function ReconciliationNote({ data }) {
  const { reconciliation: r } = data;
  const line = [
    ["Gross charges", r.gross],
    ["− Discounts", r.less_discounts],
    ["− Waivers", r.less_waivers],
    // Only when there were any: most periods have no POS returns on a bill.
    ...(Number(r.less_returns) ? [["− Goods returned", r.less_returns]] : []),
    ["= Net amount due", r.net_due],
    ["− Collected", r.less_collected],
    ["= Outstanding", r.outstanding],
  ];
  return (
    <Alert tone={r.balances ? "info" : "warning"} title="Reconciliation — bills raised in this period">
      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
        {line.map(([label, value], index) => (
          <span key={label} className="whitespace-nowrap">
            <span className={index >= 3 && label.startsWith("=") ? "font-semibold text-slate-900" : "text-slate-700"}>
              {label}
            </span>{" "}
            <span className="font-medium tabular-nums text-slate-900">{money(value)}</span>
          </span>
        ))}
      </div>
      {!r.balances && (
        <p className="mt-1.5 text-sm">These figures do not balance. Report it — do not work from this page.</p>
      )}
      <CashRetained reconciliation={r} />
      <ApprovedInPeriod adjustments={data.adjustments} />
    </Alert>
  );
}

/**
 * The cash-basis identity, kept beside the cohort one and deliberately not
 * mixed into it: what came in, what went back out, what the hospital kept.
 *
 * A refund is not a negative payment — the payment row it answers is still
 * there and is still counted in the gross — so this is the only line on the
 * page that nets the two, and it says so.
 */
function CashRetained({ reconciliation: r }) {
  if (!Number(r.less_refunds)) return null;
  const line = [
    ["Gross payments received", r.gross_collected],
    ["− Refunds", r.less_refunds],
    ["= Net revenue retained", r.net_collected],
  ];
  return (
    <div className="mt-2 border-t border-sky-200/70 pt-2">
      <p className="text-sm font-medium text-slate-800">Cash in this period</p>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
        {line.map(([label, value]) => (
          <span key={label} className="whitespace-nowrap">
            <span className={label.startsWith("=") ? "font-semibold text-slate-900" : "text-slate-700"}>
              {label}
            </span>{" "}
            <span className="font-medium tabular-nums text-slate-900">{money(value)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * The write-off register, which answers a different question from the line
 * above it: what was *approved* between these dates, whenever the bill it
 * forgives was raised.
 *
 * It is shown here, right against the reconciliation, because the two can
 * honestly differ and the reader is owed the reason rather than left to
 * wonder which number is wrong. A discount granted today on last month's bill
 * counts here and not there; so does one posted against a patient's ledger
 * with no single bill behind it, which is what `unlinked` names.
 */
function ApprovedInPeriod({ adjustments: a }) {
  const nothing = !Number(a.discounts) && !Number(a.waivers) && !Number(a.refunds);
  if (nothing) return null;
  return (
    <p className="mt-2 border-t border-sky-200/70 pt-2 text-sm text-slate-700">
      <span className="font-medium text-slate-800">Approved in this period:</span>{" "}
      discounts <span className="font-medium tabular-nums text-slate-900">{money(a.discounts)}</span>{" "}
      ({a.discounts_count}) · waivers{" "}
      <span className="font-medium tabular-nums text-slate-900">{money(a.waivers)}</span>{" "}
      ({a.waivers_count}) · refunds{" "}
      <span className="font-medium tabular-nums text-slate-900">{money(a.refunds)}</span>{" "}
      ({a.refunds_count}). Counted by approval date, so this differs from the line above where a
      write-off was granted against a bill raised in another period.
      {Number(a.unlinked) > 0 && (
        <>
          {" "}
          <span className="font-medium text-slate-800">{money(a.unlinked)}</span> of it
          ({a.unlinked_count}) was approved against a patient&rsquo;s balance rather than a single
          bill, so it reduces what they owe without appearing in any department&rsquo;s column.
        </>
      )}
    </p>
  );
}

/* ------------------------------------------------------------------ chart */

/**
 * Revenue collected per department, largest first.
 *
 * One measure, so one colour: colouring each bar differently would encode the
 * bar's own length a second time and spend the only free channel saying
 * nothing. Length is the comparison; the value is direct-labelled beside each
 * bar so nobody has to read it off an axis. Inline SVG — no chart library for
 * eight bars, the way `icons.jsx` carries its own paths.
 */
function RevenueChart({ rows, total, className = "" }) {
  const bars = useMemo(
    () => rows.map((row) => ({ ...row, value: Number(row.received ?? 0) }))
      .filter((row) => row.value > 0)
      .sort((a, b) => b.value - a.value),
    [rows],
  );
  const max = bars.length ? bars[0].value : 0;
  const grand = Number(total ?? 0);

  return (
    <Card className={className}>
      <CardHeader
        title="Revenue collected by department"
        description="Money received in this period, attributed to the bill it settled. The bars add up to the total collected."
      />
      <div className="px-4 pb-4 sm:px-5 sm:pb-5">
        {!bars.length ? (
          <EmptyState
            icon="cash"
            title="Nothing collected in this period."
            description="Choose a wider date range, or check the billing counter."
          />
        ) : (
          <ul className="space-y-2.5">
            {bars.map((bar) => {
              const share = grand > 0 ? (bar.value / grand) * 100 : 0;
              return (
                <li key={bar.key} className="min-w-0">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0 truncate text-sm font-medium text-slate-800">{bar.label}</span>
                    <span className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">
                      {money(bar.value)}
                      <span className="ml-1.5 font-normal text-slate-500">{share.toFixed(0)}%</span>
                    </span>
                  </div>
                  {/* A 10px track with a 4px-rounded fill anchored left. The
                      title is what a pointer reads; the figure above is what
                      everybody else reads, so no tooltip is load-bearing. */}
                  <div className="mt-1 h-2.5 w-full overflow-hidden rounded bg-slate-100">
                    <div
                      className="h-full rounded bg-brand-500"
                      style={{ width: `${max > 0 ? Math.max((bar.value / max) * 100, 1.5) : 0}%` }}
                      title={`${bar.label}: ${money(bar.value)}`}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {bars.some((bar) => bar.key === "unattributed") && (
          <p className="mt-3 text-sm text-slate-600">
            <span className="font-medium text-slate-700">Unattributed</span> is money taken before
            the system recorded which bill a payment settled. It is real revenue with no department
            behind it, and it only ever appears on historic periods.
          </p>
        )}
      </div>
    </Card>
  );
}

/* -------------------------------------------------------- payment methods */

function MethodBreakdown({ collections, className = "" }) {
  const total = Number(collections.total ?? 0);
  const rows = collections.by_method;

  return (
    <Card className={className}>
      <CardHeader
        title="How it was paid"
        description="Every method reconciles to the collected total."
      />
      {!rows.length ? (
        <div className="px-4 pb-4 sm:px-5 sm:pb-5">
          <EmptyState icon="cash" title="No payments in this period." />
        </div>
      ) : (
        <>
          <ul className="divide-y divide-slate-100">
            {rows.map((row) => {
              const share = total > 0 ? (Number(row.amount) / total) * 100 : 0;
              return (
                <li key={row.key} className="flex items-center justify-between gap-3 px-4 py-2.5 sm:px-5">
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-slate-800">{row.label}</span>
                    <span className="text-xs text-slate-500">{row.count} transaction(s) · {share.toFixed(0)}%</span>
                  </span>
                  <span className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">
                    {money(row.amount)}
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="flex items-center justify-between gap-3 border-t border-slate-200 bg-slate-50 px-4 py-2.5 sm:px-5">
            <span className="text-sm font-semibold text-slate-900">Total collected</span>
            <span className="text-sm font-semibold tabular-nums text-slate-900">{money(total)}</span>
          </div>
          {collections.by_channel?.length > 1 && (
            <div className="border-t border-slate-100 px-4 py-2.5 text-sm text-slate-600 sm:px-5">
              Taken at:{" "}
              {collections.by_channel.map((row, index) => (
                <span key={row.key}>
                  {index > 0 && " · "}
                  <span className="text-slate-800">{row.label}</span>{" "}
                  <span className="font-medium tabular-nums text-slate-900">{money(row.amount)}</span>
                </span>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------ departments */

const DEPARTMENT_COLUMNS = [
  ["gross", "Gross charges"],
  ["discounts", "Discounts"],
  ["waivers", "Waivers"],
  // Goods a registered patient brought back to the pharmacy POS.
  ["returns", "Returns"],
  ["net_due", "Net due"],
  ["collected", "Collected"],
  ["part_payments", "Part paid"],
  ["outstanding", "Outstanding"],
];

function DepartmentTable({ rows, total }) {
  return (
    <Card>
      <CardHeader
        title="Department breakdown"
        description="Bills raised in this period and what has become of them. Every row reconciles: gross − discounts − waivers − returns − collected = outstanding."
        actions={<Badge tone="neutral">{total.charges} charge(s)</Badge>}
      />
      {!rows.length ? (
        <div className="px-4 pb-4 sm:px-5 sm:pb-5">
          <EmptyState icon="cash" title="No charges raised in this period." />
        </div>
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <Tr>
                <Th>Department</Th>
                {DEPARTMENT_COLUMNS.map(([key, label]) => (
                  <Th key={key} className="text-right">{label}</Th>
                ))}
                <Th className="text-right">Received</Th>
                <Th className="text-right">Refunded</Th>
                <Th className="text-right">Net kept</Th>
              </Tr>
            </THead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row) => (
                <Tr key={row.key}>
                  <Td className="font-medium text-slate-900">{row.label}</Td>
                  {DEPARTMENT_COLUMNS.map(([key]) => (
                    <Td key={key} className={`text-right tabular-nums ${
                      key === "outstanding" && Number(row[key]) > 0 ? "text-red-700" : "text-slate-800"}`}>
                      {money(row[key])}
                    </Td>
                  ))}
                  <Td className="text-right font-medium tabular-nums text-emerald-700">{money(row.received)}</Td>
                  <Td className={`text-right tabular-nums ${
                    Number(row.refunded) > 0 ? "font-medium text-amber-800" : "text-slate-800"}`}>
                    {money(row.refunded)}
                  </Td>
                  <Td className="text-right font-medium tabular-nums text-emerald-700">{money(row.net_received)}</Td>
                </Tr>
              ))}
              <Tr className="border-t-2 border-slate-300 bg-slate-50">
                <Td className="font-semibold text-slate-900">Total</Td>
                {DEPARTMENT_COLUMNS.map(([key]) => (
                  <Td key={key} className="text-right font-semibold tabular-nums text-slate-900">
                    {money(total[key])}
                  </Td>
                ))}
                <Td className="text-right font-semibold tabular-nums text-emerald-700">{money(total.received)}</Td>
                <Td className="text-right font-semibold tabular-nums text-amber-800">{money(total.refunded)}</Td>
                <Td className="text-right font-semibold tabular-nums text-emerald-700">{money(total.net_received)}</Td>
              </Tr>
            </tbody>
          </Table>
        </TableWrap>
      )}
      <div className="border-t border-slate-100 px-4 py-2.5 text-sm text-slate-600 sm:px-5">
        <span className="font-medium text-slate-700">Collected</span> is money settled against these
        bills, whenever it arrived. <span className="font-medium text-slate-700">Received</span> is
        cash that arrived in this period, whenever the bill was raised — it is what the chart plots.{" "}
        <span className="font-medium text-slate-700">Refunded</span> is money handed back in this
        period, attributed to the department that took it, and{" "}
        <span className="font-medium text-slate-700">Net kept</span> is Received less Refunded.
      </div>
    </Card>
  );
}


/**
 * The pharmacy POS till in the period, as it recorded itself. A walk-in sale
 * has no bill, so its gross and its discount appear in no other block on this
 * page; a registered patient's purchase also sits in the department table as a
 * Pharmacy charge — which is why this block is shown beside that table and is
 * never added to it. `Paid` is the POS share of Gross payments received.
 */
function PosSalesBlock({ pos }) {
  if (!pos) return null;
  const rows = [pos.walk_in, pos.registered, pos];
  return (
    <Card>
      <CardHeader
        title="Pharmacy POS"
        description="Till sales in this period: gross − discounts = paid, and paid − returned = kept."
        actions={pos.reconciles
          ? <Badge tone="success">Reconciles with payments</Badge>
          : <Badge tone="danger">Does not reconcile — report it</Badge>}
      />
      <TableWrap>
        <Table>
          <THead>
            <Tr>
              <Th>Customers</Th><Th className="text-right">Sales</Th><Th className="text-right">Gross</Th>
              <Th className="text-right">Discounts</Th><Th className="text-right">Paid</Th>
              <Th className="text-right">Returned</Th><Th className="text-right">Kept</Th>
            </Tr>
          </THead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => (
              <Tr key={row.key} className={row.key === "total" ? "border-t-2 border-slate-300 bg-slate-50" : ""}>
                <Td className={row.key === "total" ? "font-semibold text-slate-900" : "font-medium text-slate-900"}>{row.label}</Td>
                <Td className="text-right tabular-nums">{row.count}</Td>
                <Td className="text-right tabular-nums">{money(row.gross)}</Td>
                <Td className="text-right tabular-nums">{money(row.discounts)}</Td>
                <Td className="text-right font-medium tabular-nums text-emerald-700">{money(row.paid)}</Td>
                <Td className={`text-right tabular-nums ${Number(row.returned) > 0 ? "font-medium text-amber-800" : ""}`}>{money(row.returned)}</Td>
                <Td className="text-right font-medium tabular-nums text-emerald-700">{money(row.net)}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrap>
    </Card>
  );
}

/* ----------------------------------------------------------- transactions */

function TransactionTable({ rows, period }) {
  return (
    <Card>
      <CardHeader
        title="Transactions"
        description={`Charges raised ${period.from === period.to ? `on ${period.from}` : `between ${period.from} and ${period.to}`}, most recent first.`}
        actions={<Button size="sm" variant="link" to="/transactions">Full statement</Button>}
      />
      {!rows.length ? (
        <div className="px-4 pb-4 sm:px-5 sm:pb-5">
          <EmptyState icon="receipt" title="No transactions in this period." />
        </div>
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <Tr>
                <Th>Time</Th>
                <Th>Patient</Th>
                <Th>Patient no.</Th>
                <Th>Department</Th>
                <Th>Service</Th>
                <Th className="text-right">Amount due</Th>
                <Th className="text-right">Discount</Th>
                <Th className="text-right">Waiver</Th>
                <Th className="text-right">Paid</Th>
                <Th className="text-right">Balance</Th>
                <Th>Method</Th>
                <Th>Status</Th>
              </Tr>
            </THead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row) => (
                <Tr key={row.id}>
                  <Td className="whitespace-nowrap text-slate-600">
                    {new Date(row.time).toLocaleString("en-NG", {
                      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
                    })}
                  </Td>
                  <Td className="font-medium text-slate-900">
                    {/* The chart is addressed by UUID, never by the row's
                        primary key — the same identity every other link uses. */}
                    <Link
                      to={`/patients/${row.patient_uuid}/billing`}
                      className="rounded hover:text-brand-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      {row.patient}
                    </Link>
                  </Td>
                  <Td className="whitespace-nowrap tabular-nums text-slate-600">{row.patient_number}</Td>
                  <Td className="whitespace-nowrap text-slate-700">{row.department}</Td>
                  <Td className="text-slate-800">{row.service}</Td>
                  <Td className="text-right tabular-nums text-slate-800">{money(row.amount_due)}</Td>
                  <Td className="text-right tabular-nums text-slate-600">{money(row.discount)}</Td>
                  <Td className="text-right tabular-nums text-slate-600">{money(row.waiver)}</Td>
                  <Td className="text-right tabular-nums text-slate-800">{money(row.amount_paid)}</Td>
                  <Td className={`text-right tabular-nums ${Number(row.balance) > 0 ? "font-medium text-red-700" : "text-slate-600"}`}>
                    {money(row.balance)}
                  </Td>
                  <Td className="whitespace-nowrap text-slate-700">
                    {row.methods.length ? row.methods.join(", ") : "—"}
                  </Td>
                  <Td>
                    <Badge tone={STATUS_TONE[row.status] ?? "neutral"}>
                      {STATUS_LABEL[row.status] ?? row.status}
                    </Badge>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </TableWrap>
      )}
    </Card>
  );
}

/* --------------------------------------------------------------- skeleton */

function ReportSkeleton() {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 10 }).map((_, index) => (
          <Skeleton key={index} className="h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-16 rounded-xl" />
      <div className="grid gap-5 lg:grid-cols-5">
        <Skeleton className="h-72 rounded-xl lg:col-span-3" />
        <Skeleton className="h-72 rounded-xl lg:col-span-2" />
      </div>
      <Skeleton className="h-64 rounded-xl" />
    </div>
  );
}
