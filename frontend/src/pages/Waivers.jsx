import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";

// Money written off, and who approved it. Waivers, discounts and refunds
// are all Adjustments — the same ledger entry with a different kind — so
// they share one page with a filter rather than three near-identical ones.
//
// Read-only by design: this is the record of decisions already made. A new
// waiver is granted against the charge it forgives, on the billing counter,
// where the reason and the balance are in front of the person approving it.

const currency = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 })
    .format(Number(n ?? 0));

const KINDS = [
  ["waiver", "Waived", "border-amber-300 bg-amber-50 text-amber-800"],
  ["discount", "Discounts", "border-violet-300 bg-violet-50 text-violet-800"],
  ["refund", "Refunds", "border-slate-300 bg-slate-50 text-slate-700"],
];

export default function Waivers() {
  const [kind, setKind] = useState("waiver");
  const [search, setSearch] = useState("");

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["adjustments", kind, search],
    queryFn: () => api
      .get("/adjustments/", { params: { kind, page_size: 200, ...(search.trim() ? { search: search.trim() } : {}) } })
      .then((r) => r.data.results ?? r.data),
  });

  const rows = data ?? [];
  const total = rows.reduce((sum, a) => sum + Number(a.amount), 0);
  const today = new Date().toDateString();
  const todayTotal = rows
    .filter((a) => new Date(a.created_at).toDateString() === today)
    .reduce((sum, a) => sum + Number(a.amount), 0);
  const label = KINDS.find(([k]) => k === kind)?.[1] ?? kind;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-5 md:p-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Waived &amp; written off</h1>
          <p className="mt-1 text-sm text-slate-700">
            Money the hospital decided not to collect, and who approved each one.
          </p>
          <p className="mt-2 text-sm text-slate-700">
            <strong>{rows.length}</strong> {label.toLowerCase()} ·{" "}
            <strong>{currency(total)}</strong> in total · <strong>{currency(todayTotal)}</strong> today
          </p>
        </div>
        <button onClick={refetch} className="rounded-lg border px-4 py-2 text-sm font-medium hover:bg-slate-50">
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex overflow-hidden rounded-lg border text-sm">
          {KINDS.map(([value, text]) => (
            <button
              key={value}
              onClick={() => setKind(value)}
              className={`px-4 py-2 font-medium ${kind === value ? "bg-brand-600 text-white" : "text-slate-700 hover:bg-slate-50"}`}
            >
              {text}
            </button>
          ))}
        </div>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by patient, file number or reason…"
          className="min-w-[16rem] flex-1 rounded-md border border-slate-300 px-3 py-2"
        />
      </div>

      {isLoading && <p className="text-sm text-slate-700">Loading…</p>}
      {isError && <p className="text-sm text-red-700">Could not load these.</p>}

      {!isLoading && rows.length === 0 && (
        <div className="rounded-xl border bg-white p-10 text-center text-slate-700">
          <p className="text-3xl">🧾</p>
          <p className="mt-2 font-medium">
            {search ? "Nothing matches that." : `No ${label.toLowerCase()} recorded`}
          </p>
          {!search && (
            <p className="mt-1 text-sm">
              These are granted from the billing counter, against the charge they forgive.
            </p>
          )}
        </div>
      )}

      <div className="space-y-3">
        {rows.map((adjustment) => {
          const tone = KINDS.find(([k]) => k === adjustment.kind)?.[2] ?? KINDS[2][2];
          return (
            <article key={adjustment.id} className="rounded-xl border bg-white p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      to={`/patients/${adjustment.patient}/billing`}
                      className="font-medium text-slate-800 hover:text-brand-600"
                    >
                      {adjustment.patient_name}
                    </Link>
                    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>
                      {adjustment.kind_label ?? adjustment.kind}
                    </span>
                  </div>
                  {adjustment.charge_description && (
                    <p className="mt-0.5 text-sm text-slate-700">
                      Against: {adjustment.charge_description}
                    </p>
                  )}
                  <p className="text-sm text-slate-600">
                    {new Date(adjustment.created_at).toLocaleString()}
                    {adjustment.approved_by_name && ` · approved by ${adjustment.approved_by_name}`}
                  </p>
                </div>
                <p className="shrink-0 text-lg font-semibold text-slate-900">
                  {currency(adjustment.amount)}
                </p>
              </div>
              {/* The reason is the point of the record — a write-off nobody
                  can account for is the thing an audit asks about. */}
              <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-800">
                {adjustment.reason || "No reason recorded."}
              </p>
            </article>
          );
        })}
      </div>
    </div>
  );
}
