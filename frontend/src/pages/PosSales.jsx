import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import { useAuth } from "../auth/AuthContext.jsx";
import { POS_RETURN_ROLES, hasRole } from "../auth/roles.js";
import { PosReceiptSheet } from "../components/DepartmentDocuments.jsx";
import PosRegisterSummary from "../components/PosRegisterSummary.jsx";
import { useToast } from "../components/Toaster.jsx";
import {
  Alert, Badge, Button, EmptyState, ErrorState, Field, Input, Modal, Page, PageHeader, Select,
  Skeleton, Tab, TabBar, Table, TableWrap, Td, Textarea, Th, THead, Tr, naira,
} from "../components/ui.jsx";
import { PAYMENT_METHODS } from "./PharmacyPOS.jsx";

// POS sales history, the returns register, and the registers they were taken on.
//
// Kept apart from prescription dispensing on purpose: dispensing history stays
// on the Pharmacy counter, exactly as it was. Every figure here is read off
// the server — the summary from the same Payment and Refund rows the finance
// report counts — and a return is taken here, against the sale, through the
// Refund workflow the rest of the hospital uses. The tab is in the URL so the
// navigation can open Returns directly.

const money = (value) => naira(Number(value ?? 0));
const STATUS_TONE = { completed: "success", held: "warning", cancelled: "neutral" };
const PRESETS = [["today", "Today"], ["week", "This week"], ["month", "This month"]];
const TABS = [["sales", "Sales"], ["returns", "Returns"], ["registers", "Registers"]];
const withoutBlanks = (params) => Object.fromEntries(Object.entries(params).filter(([, v]) => v !== ""));

export default function PosSales() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab = TABS.some(([key]) => key === requested) ? requested : "sales";
  const setTab = (key) => setParams(key === "sales" ? {} : { tab: key }, { replace: true });

  return (
    <Page width="wide">
      <PageHeader
        icon="receipt"
        title="POS Sales"
        subtitle="Walk-in and registered-patient sales at the pharmacy till, their returns, and the registers they were taken on. Prescription dispensing stays on the Pharmacy counter."
      />
      <TabBar label="POS sections">
        {TABS.map(([key, label]) => (
          <Tab key={key} active={tab === key} onClick={() => setTab(key)}>{label}</Tab>
        ))}
      </TabBar>
      {tab === "sales" && <SalesTab />}
      {tab === "returns" && <ReturnsTab />}
      {tab === "registers" && <RegistersTab />}
    </Page>
  );
}

/* ------------------------------------------------------------------ sales */

function SalesTab() {
  const [preset, setPreset] = useState("today");
  const [filters, setFilters] = useState({
    search: "", status: "completed", customer_type: "", payment_method: "", sold_by: "",
    date_from: "", date_to: "",
  });
  const [selected, setSelected] = useState(null);
  const set = (key) => (event) => setFilters((current) => ({ ...current, [key]: event.target.value }));

  const summary = useQuery({
    queryKey: ["pos-summary", preset],
    queryFn: () => api.get("/sales/summary/", { params: { preset } }).then((r) => r.data),
  });
  const staff = useQuery({
    queryKey: ["users", "directory"],
    queryFn: () => api.get("/users/", { params: { page_size: 200 } }).then((r) => r.data.results ?? r.data),
    staleTime: 5 * 60 * 1000,
  });
  const sales = useQuery({
    queryKey: ["pos-sales", filters],
    queryFn: () => api.get("/sales/", { params: withoutBlanks({ ...filters, page_size: 100 }) })
      .then((r) => r.data.results ?? r.data),
    placeholderData: keepPreviousData,
  });

  const s = summary.data;
  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold text-slate-900">Takings</h2>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map(([key, label]) => (
              <Button key={key} size="sm" variant={preset === key ? "primary" : "secondary"}
                      aria-pressed={preset === key} onClick={() => setPreset(key)}>
                {label}
              </Button>
            ))}
          </div>
        </div>
        {summary.isError && <ErrorState className="mt-3" title="Could not load the takings." onRetry={summary.refetch} />}
        {!s && !summary.isError && <Skeleton className="mt-3 h-20" />}
        {s && (
          <>
            <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
              <Stat label="Completed sales" value={s.sales.count} />
              <Stat label="Gross" value={money(s.sales.gross)} />
              <Stat label="Discounts" value={money(s.sales.discounts)} />
              <Stat label="Paid" value={money(s.sales.total)} />
              <Stat label="Returned" value={money(s.refunds.amount)} />
            </dl>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-700">
              {s.walk_in && s.registered && (
                <span>Walk-in {money(s.walk_in.paid)} · Registered patients {money(s.registered.paid)}</span>
              )}
              {s.methods.length > 0 && (
                <span>{s.methods.map((row) => `${row.label} ${money(row.amount)}`).join(" · ")}</span>
              )}
              {s.reconciles === true && <Badge tone="success">Reconciles with payments</Badge>}
              {s.reconciles === false && <Badge tone="danger">Does not reconcile — report it</Badge>}
            </div>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[28rem] text-sm">
                <caption className="mb-1 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
                  Pharmacy department takings — reconciles to the finance report
                </caption>
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-600">
                    <th className="py-1 pr-3 font-semibold">Source</th>
                    <th className="py-1 pr-3 text-right font-semibold">Received</th>
                    <th className="py-1 pr-3 text-right font-semibold">Refunded</th>
                    <th className="py-1 text-right font-semibold">Net</th>
                  </tr>
                </thead>
                <tbody>
                  {[...s.pharmacy.channels, s.pharmacy.total].map((row) => (
                    <tr key={row.key} className={`border-b border-slate-100 ${row.key === "total" ? "font-semibold" : ""}`}>
                      <td className="py-1.5 pr-3 text-slate-800">{row.label}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{money(row.received)}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{money(row.refunded)}</td>
                      <td className="py-1.5 text-right tabular-nums">{money(row.net)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {Number(s.discounts?.total) > 0 && (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[30rem] text-sm">
                  <caption className="mb-1 flex flex-wrap items-center gap-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
                    Discounts given — {money(s.discounts.total)} in total
                    {Number(s.discounts.approved?.amount) > 0 && (
                      <Badge tone="warning">
                        {money(s.discounts.approved.amount)} needed authorising
                        ({s.discounts.approved.count})
                      </Badge>
                    )}
                    {s.discounts.reconciles === false && (
                      <Badge tone="danger">Does not reconcile — report it</Badge>
                    )}
                  </caption>
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-600">
                      <th className="py-1 pr-3 font-semibold">Reason</th>
                      <th className="py-1 pr-3 font-semibold">Given by</th>
                      <th className="py-1 pr-3 text-right font-semibold">Sales</th>
                      <th className="py-1 text-right font-semibold">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {/* Two groupings of the same figure, side by side: the
                        rows line up by position only, so each column is read
                        down its own. */}
                    {s.discounts.by_reason.map((row, index) => {
                      const cashier = s.discounts.by_cashier[index];
                      return (
                        <tr key={row.key} className="border-b border-slate-100">
                          <td className="py-1.5 pr-3 text-slate-800">{row.label}</td>
                          <td className="py-1.5 pr-3 text-slate-700">
                            {cashier ? `${cashier.label} — ${money(cashier.amount)}` : ""}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{row.count}</td>
                          <td className="py-1.5 text-right tabular-nums">{money(row.amount)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {(s.categories ?? []).length > 0 && (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[30rem] text-sm">
                  <caption className="mb-1 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
                    What sold, by category — the same money above, split by what it was
                  </caption>
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-600">
                      <th className="py-1 pr-3 font-semibold">Category</th>
                      <th className="py-1 pr-3 text-right font-semibold">Units</th>
                      <th className="py-1 pr-3 text-right font-semibold">Gross</th>
                      <th className="py-1 pr-3 text-right font-semibold">Discounts</th>
                      <th className="py-1 text-right font-semibold">Net</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.categories.map((row) => (
                      <tr key={row.category ?? "none"} className="border-b border-slate-100">
                        <td className="py-1.5 pr-3 text-slate-800">{row.label}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{row.units}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{money(row.gross)}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{money(row.discounts)}</td>
                        <td className="py-1.5 text-right tabular-nums">{money(row.net)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <Input aria-label="Search sales" placeholder="Transaction, customer, patient or product…"
               value={filters.search} onChange={set("search")} className="lg:col-span-2" />
        <Select aria-label="Status" value={filters.status} onChange={set("status")}>
          <option value="">All statuses</option>
          <option value="completed">Completed</option>
          <option value="held">Held</option>
          <option value="cancelled">Discarded</option>
        </Select>
        <Select aria-label="Customer type" value={filters.customer_type} onChange={set("customer_type")}>
          <option value="">Walk-in and registered</option>
          <option value="walk_in">Walk-in customers</option>
          <option value="patient">Registered patients</option>
        </Select>
        <Select aria-label="Payment method" value={filters.payment_method} onChange={set("payment_method")}>
          <option value="">Any payment method</option>
          {PAYMENT_METHODS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </Select>
        <Select aria-label="Served by" value={filters.sold_by} onChange={set("sold_by")}>
          <option value="">Anyone at the till</option>
          {(staff.data ?? []).map((person) => (
            <option key={person.id} value={person.id}>
              {person.full_name || [person.first_name, person.last_name].filter(Boolean).join(" ") || person.username}
            </option>
          ))}
        </Select>
        <Input type="date" aria-label="From date" value={filters.date_from} onChange={set("date_from")} />
        <Input type="date" aria-label="To date" value={filters.date_to} onChange={set("date_to")} />
      </div>

      {sales.isError && <ErrorState title="Could not load sales." onRetry={sales.refetch} />}
      {sales.isLoading && <Skeleton className="h-40" />}
      {sales.data && sales.data.length === 0 && (
        <EmptyState icon="receipt" title="No sales match" description="Change the filters or the dates." />
      )}
      {sales.data && sales.data.length > 0 && (
        <TableWrap>
          <Table className="min-w-[48rem]">
            <THead>
              <Tr>
                <Th>Transaction</Th><Th>Date</Th><Th>Customer</Th><Th>Served by</Th>
                <Th>Method</Th><Th className="text-right">Total</Th><Th className="text-right">Returned</Th><Th>Status</Th>
              </Tr>
            </THead>
            <tbody>
              {sales.data.map((sale) => (
                <Tr key={sale.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setSelected(sale)}>
                  <Td>
                    <button type="button" className="font-medium text-brand-700 hover:underline"
                            onClick={(event) => { event.stopPropagation(); setSelected(sale); }}>
                      {sale.reference}
                    </button>
                  </Td>
                  <Td>{new Date(sale.completed_at ?? sale.created_at).toLocaleString()}</Td>
                  <Td>
                    {sale.customer_label}
                    <span className="block text-xs text-slate-500">{sale.customer_type_label}</span>
                  </Td>
                  <Td>{sale.sold_by_name}</Td>
                  <Td>{sale.payment_method_label || "—"}</Td>
                  <Td className="text-right tabular-nums">{money(sale.total_amount)}</Td>
                  <Td className="text-right tabular-nums">{Number(sale.amount_returned) ? money(sale.amount_returned) : "—"}</Td>
                  <Td><Badge tone={STATUS_TONE[sale.status] ?? "neutral"}>{sale.status_label}</Badge></Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </TableWrap>
      )}

      {selected && <SaleDetail sale={selected} onClose={() => setSelected(null)} onChanged={setSelected} />}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="min-w-0 rounded-lg bg-slate-50 px-3 py-2">
      <dt className="text-xs text-slate-600">{label}</dt>
      <dd className="mt-0.5 truncate font-semibold tabular-nums text-slate-900">{value}</dd>
    </div>
  );
}

function SaleDetail({ sale, onClose, onChanged }) {
  const { user } = useAuth();
  const [printing, setPrinting] = useState(false);
  const [returning, setReturning] = useState(false);
  const mayReturn = hasRole(user, POS_RETURN_ROLES) && sale.status === "completed"
    && sale.lines.some((line) => line.pieces.some((piece) => piece.returnable_quantity > 0));
  const saleDiscount = Number(sale.sale_discount_amount ?? sale.discount_amount);

  return (
    <>
      <Modal
        open onClose={onClose} size="lg" title={`${sale.reference} — ${sale.customer_label}`}
        description={`${sale.customer_type_label} · served by ${sale.sold_by_name} · register ${sale.register_reference ?? "—"}`}
        footer={
          <>
            {mayReturn && !returning && (
              <Button variant="dangerOutline" onClick={() => setReturning(true)}>Return items</Button>
            )}
            {sale.status === "completed" && (
              <Button variant="secondary" onClick={() => setPrinting(true)}>Print receipt</Button>
            )}
            <Button variant="ghost" onClick={onClose}>Close</Button>
          </>
        }
      >
        {returning ? (
          <ReturnForm sale={sale} onCancel={() => setReturning(false)}
                      onDone={(updated) => { setReturning(false); onChanged(updated); }} />
        ) : (
          <div className="space-y-4 text-sm">
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {sale.lines.map((line) => (
                <li key={line.id} className="px-3 py-2">
                  <div className="flex justify-between gap-3">
                    <span className="font-medium text-slate-900">{line.item_name} ×{line.quantity}</span>
                    <span className="tabular-nums">{money(line.gross)}</span>
                  </div>
                  {Number(line.line_discount) > 0 && (
                    <p className="flex justify-between gap-3 text-emerald-800">
                      <span>
                        Item discount{line.line_discount_type === "percent" ? ` (${Number(line.line_discount_value)}%)` : ""}
                      </span>
                      <span className="tabular-nums">− {money(line.line_discount)}</span>
                    </p>
                  )}
                  <p className="text-xs text-slate-600">
                    {line.pieces.map((piece) => `batch ${piece.batch_no} (exp ${piece.expiry_date}) ×${piece.quantity} @ ${money(piece.unit_price)}`
                      + (piece.returned_quantity ? `, ${piece.returned_quantity} returned` : "")).join(" · ")}
                  </p>
                </li>
              ))}
            </ul>
            <dl className="ml-auto grid max-w-xs grid-cols-2 gap-x-4 gap-y-1">
              <dt className="text-slate-600">Subtotal</dt><dd className="text-right tabular-nums">{money(sale.subtotal)}</dd>
              <dt className="text-slate-600">Discounts</dt><dd className="text-right tabular-nums">− {money(sale.discount_amount)}</dd>
              {saleDiscount > 0 && Number(sale.discount_amount) !== saleDiscount && (
                <>
                  <dt className="pl-3 text-xs text-slate-500">of which sale-wide</dt>
                  <dd className="text-right text-xs tabular-nums text-slate-500">{money(saleDiscount)}</dd>
                </>
              )}
              <dt className="font-semibold">Total</dt><dd className="text-right font-semibold tabular-nums">{money(sale.total_amount)}</dd>
              <dt className="text-slate-600">{sale.payment_method_label || "Payment"}</dt><dd className="text-right tabular-nums">{money(sale.amount_tendered ?? sale.total_amount)}</dd>
              <dt className="text-slate-600">Change</dt><dd className="text-right tabular-nums">{money(sale.change_due)}</dd>
            </dl>
            {sale.discount_reason && (
              <p className="text-slate-700">
                Discount: {sale.discount_reason} — by {sale.discount_by_name}
                {sale.discount_approved_by_name && (
                  <span className="text-amber-800">
                    {" · authorised by "}{sale.discount_approved_by_name}
                  </span>
                )}
              </p>
            )}
            {sale.returns.length > 0 && (
              <div>
                <h3 className="font-semibold text-slate-900">Returns</h3>
                <ul className="mt-1 space-y-1">
                  {sale.returns.map((ret) => (
                    <li key={ret.id} className="text-slate-700">
                      {ret.reference} · {money(ret.amount)} by {ret.refund_method_label} · {ret.processed_by_name} · {ret.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Modal>
      {printing && <PosReceiptSheet sale={sale} onClose={() => setPrinting(false)} />}
    </>
  );
}

function ReturnForm({ sale, onCancel, onDone }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [quantities, setQuantities] = useState({});
  const [reason, setReason] = useState("");
  const [method, setMethod] = useState(sale.payment_method || "cash");
  const pieces = sale.lines.flatMap((line) => line.pieces.map((piece) => ({ ...piece, item_name: line.item_name })));
  const chosen = pieces.filter((piece) => Number(quantities[piece.id]) > 0);

  const submit = useMutation({
    mutationFn: () => api.post(`/sales/${sale.id}/return/`, {
      lines: chosen.map((piece) => ({ sale_item: piece.id, quantity: Number(quantities[piece.id]) })),
      reason, refund_method: method,
    }),
    onSuccess: (response) => {
      for (const key of ["pos-sales", "pos-summary", "pos-register", "pos-returns", "finance-report"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      showToast({
        title: `Return ${response.data.return.reference} taken`,
        message: `${money(response.data.return.amount)} refunded. The medicine went to returns quarantine.`,
      });
      onDone(response.data.sale);
    },
    onError: (error) => showToast({ tone: "error", title: "Return not taken", message: readError(error, "Please try again.") }),
  });

  return (
    <div className="space-y-4 text-sm">
      <Alert tone="info">
        Returned medicine goes into <strong>returns quarantine</strong>, not back on the shelf. The original
        sale and payment stay on record; the refund is recorded against the payment and paid out of your open register.
      </Alert>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
        {pieces.map((piece) => (
          <li key={piece.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
            <div className="min-w-0">
              <p className="font-medium text-slate-900">{piece.item_name}</p>
              <p className="text-xs text-slate-600">batch {piece.batch_no} · {piece.returnable_quantity} of {piece.quantity} returnable</p>
            </div>
            <Input
              type="number" min="0" max={piece.returnable_quantity} inputMode="numeric"
              aria-label={`Return how many ${piece.item_name} from batch ${piece.batch_no}`}
              className="w-24 text-right" disabled={piece.returnable_quantity === 0}
              value={quantities[piece.id] ?? ""}
              onChange={(event) => setQuantities((current) => ({
                ...current, [piece.id]: Math.min(Math.max(0, Number(event.target.value) || 0), piece.returnable_quantity),
              }))}
            />
          </li>
        ))}
      </ul>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Refund by">
          <Select value={method} onChange={(event) => setMethod(event.target.value)}>
            {PAYMENT_METHODS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </Select>
        </Field>
        <Field label="Reason" required>
          <Textarea rows={2} value={reason} onChange={(event) => setReason(event.target.value)} />
        </Field>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
        <Button variant="danger" disabled={!chosen.length || !reason.trim() || submit.isPending}
                onClick={() => submit.mutate()}>
          {submit.isPending ? "Taking return…" : "Take return and refund"}
        </Button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- returns */

function ReturnsTab() {
  const [filters, setFilters] = useState({ search: "", refund_method: "", date_from: "", date_to: "" });
  const set = (key) => (event) => setFilters((current) => ({ ...current, [key]: event.target.value }));
  const returns = useQuery({
    queryKey: ["pos-returns", filters],
    queryFn: () => api.get("/sales/returns/", { params: withoutBlanks({ ...filters, page_size: 100 }) })
      .then((r) => r.data.results ?? r.data),
    placeholderData: keepPreviousData,
  });
  const rows = returns.data ?? [];
  const refunded = rows.reduce((sum, row) => sum + Number(row.amount), 0);

  return (
    <div className="space-y-4">
      <Alert tone="info">
        A POS return is taken against its sale: open the sale under <strong>Sales</strong> and choose{" "}
        <strong>Return items</strong>. Returned medicine goes to returns quarantine, never back on the shelf.
        Refunds for hospital services stay on the Refunds desk.
      </Alert>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <Input aria-label="Search returns" placeholder="Return, sale, customer or reason…"
               value={filters.search} onChange={set("search")} />
        <Select aria-label="Refund method" value={filters.refund_method} onChange={set("refund_method")}>
          <option value="">Any refund method</option>
          {PAYMENT_METHODS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </Select>
        <Input type="date" aria-label="From date" value={filters.date_from} onChange={set("date_from")} />
        <Input type="date" aria-label="To date" value={filters.date_to} onChange={set("date_to")} />
      </div>

      {returns.isError && <ErrorState title="Could not load returns." onRetry={returns.refetch} />}
      {returns.isLoading && <Skeleton className="h-40" />}
      {returns.data && rows.length === 0 && (
        <EmptyState icon="refund" title="No returns" description="Nothing has been brought back in this range." />
      )}
      {rows.length > 0 && (
        <>
          <TableWrap>
            <Table className="min-w-[56rem]">
              <THead>
                <Tr>
                  <Th>Return</Th><Th>Date</Th><Th>Sale</Th><Th>Customer</Th><Th>Items</Th>
                  <Th>Refunded by</Th><Th className="text-right">Amount</Th><Th>Taken by</Th><Th>Reason</Th>
                </Tr>
              </THead>
              <tbody>
                {rows.map((row) => (
                  <Tr key={row.id}>
                    <Td className="font-medium text-slate-900">{row.reference}</Td>
                    <Td>{new Date(row.created_at).toLocaleString()}</Td>
                    <Td>{row.sale_reference}</Td>
                    <Td>{row.customer_label}</Td>
                    <Td>
                      {row.lines.map((line) => (
                        <span key={line.id} className="block">
                          {line.item_name} ×{line.quantity}
                          <span className="text-xs text-slate-500"> · batch {line.batch_no}</span>
                        </span>
                      ))}
                    </Td>
                    <Td>{row.refund_method_label}</Td>
                    <Td className="text-right font-medium tabular-nums">{money(row.amount)}</Td>
                    <Td>{row.processed_by_name}</Td>
                    <Td className="max-w-[16rem] text-slate-700">{row.reason}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrap>
          <p className="text-sm text-slate-700">
            {rows.length} return{rows.length === 1 ? "" : "s"} · <span className="font-medium tabular-nums">{money(refunded)}</span> refunded
          </p>
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- registers */

function RegistersTab() {
  const [selected, setSelected] = useState(null);
  const registers = useQuery({
    queryKey: ["pos-registers"],
    queryFn: () => api.get("/pos-registers/", { params: { page_size: 100 } }).then((r) => r.data.results ?? r.data),
  });
  const detail = useQuery({
    queryKey: ["pos-register-summary", selected?.id],
    queryFn: () => api.get(`/pos-registers/${selected.id}/summary/`).then((r) => r.data),
    enabled: Boolean(selected),
  });

  if (registers.isLoading) return <Skeleton className="h-40" />;
  if (registers.isError) return <ErrorState title="Could not load registers." onRetry={registers.refetch} />;
  if (!registers.data?.length) {
    return <EmptyState icon="cash" title="No registers yet" description="A register is opened from the Pharmacy POS." />;
  }

  return (
    <>
      <TableWrap>
        <Table className="min-w-[48rem]">
          <THead>
            <Tr>
              <Th>Register</Th><Th>Operator</Th><Th>Opened</Th><Th>Closed</Th>
              <Th className="text-right">Expected cash</Th><Th className="text-right">Counted</Th>
              <Th className="text-right">Variance</Th><Th>Status</Th>
            </Tr>
          </THead>
          <tbody>
            {registers.data.map((register) => (
              <Tr key={register.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setSelected(register)}>
                <Td className="font-medium text-brand-700">{register.reference}</Td>
                <Td>{register.opened_by_name}</Td>
                <Td>{new Date(register.created_at).toLocaleString()}</Td>
                <Td>{register.closed_at ? new Date(register.closed_at).toLocaleString() : "—"}</Td>
                <Td className="text-right tabular-nums">{register.expected_cash ? money(register.expected_cash) : "—"}</Td>
                <Td className="text-right tabular-nums">{register.counted_cash ? money(register.counted_cash) : "—"}</Td>
                <Td className={`text-right tabular-nums ${Number(register.variance) ? "font-semibold text-amber-800" : ""}`}>
                  {register.variance !== null ? money(register.variance) : "—"}
                </Td>
                <Td><Badge tone={register.status === "open" ? "warning" : "neutral"}>{register.status === "open" ? "Open" : "Closed"}</Badge></Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrap>
      {selected && (
        <Modal open onClose={() => setSelected(null)} size="lg" title={`Register ${selected.reference}`}
               description={`${selected.opened_by_name} · ${selected.status === "open" ? "still open" : `closed by ${selected.closed_by_name}`}`}>
          <PosRegisterSummary summary={detail.data?.summary} />
          {selected.closing_note && <p className="mt-3 text-sm text-slate-700">Note: {selected.closing_note}</p>}
        </Modal>
      )}
    </>
  );
}
