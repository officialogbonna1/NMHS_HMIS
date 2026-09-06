import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { patientNumber } from "../components/patientIdentity.js";
import { Page, PageHeader, MetaStat, Button, SearchInput } from "../components/ui.jsx";

// Who owes money, biggest first. The list is filtered in the database
// (`?owing=true`), so settling in full is what takes somebody off it —
// nothing has to be ticked or archived by hand, and a patient cannot linger
// here because the page only fetched the first 25 ledgers.

const currency = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 })
    .format(Number(n ?? 0));

export default function Outstanding() {
  const [search, setSearch] = useState("");

  const { data: owing, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["ledgers", "owing", search],
    queryFn: () => api
      .get("/ledgers/", { params: { owing: true, page_size: 200, ...(search.trim() ? { search: search.trim() } : {}) } })
      .then((r) => r.data.results ?? r.data),
  });

  const rows = owing ?? [];
  const total = rows.reduce((sum, l) => sum + Number(l.outstanding_balance), 0);

  return (
    <Page>
      <PageHeader
        icon="clock"
        title="Outstanding"
        subtitle="Patients who still owe. They drop off this list the moment their balance reaches zero."
        meta={
          <>
            <MetaStat value={rows.length} label={`patient${rows.length === 1 ? "" : "s"}`} />
            <MetaStat value={currency(total)} label="owed in total" tone="danger" />
          </>
        }
        actions={
          <Button variant="soft" onClick={refetch} loading={isFetching}>
            {isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        }
        toolbar={
          /* The debtors list is worked by phone, so search belongs with the
             header rather than floating above the rows. */
          <SearchInput
            value={search}
            onChange={setSearch}
            label="Search debtors"
            placeholder="Search by name or file number…"
            className="sm:max-w-sm"
          />
        }
      />

      {isLoading && <p className="text-sm text-slate-700">Loading…</p>}
      {isError && <p className="text-sm text-red-700">Could not load the list.</p>}

      {!isLoading && rows.length === 0 && (
        <div className="rounded-xl border bg-white p-10 text-center text-slate-700">
          <p className="text-3xl">✅</p>
          <p className="mt-2 font-medium">
            {search ? "Nobody owing matches that." : "Nobody owes anything"}
          </p>
          {!search && <p className="mt-1 text-sm">Every patient is settled up.</p>}
        </div>
      )}

      <div className="divide-y overflow-hidden rounded-xl border bg-white">
        {rows.map((ledger) => (
          <div key={ledger.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5">
            <div className="min-w-0">
              <Link
                to={`/patients/${ledger.patient}/billing`}
                className="inline-flex min-h-[32px] items-center rounded font-medium text-slate-800 underline-offset-2 transition hover:text-brand-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                {ledger.patient_name}
              </Link>
              <p className="text-sm text-slate-700">
                {patientNumber(ledger)}
                {/* The list is worked by phone — the number has to be on it. */}
                {ledger.patient_phone && ` · ${ledger.patient_phone}`}
              </p>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right">
                <p className="font-semibold text-red-700">{currency(ledger.outstanding_balance)}</p>
                <p className="text-sm text-slate-600">
                  {currency(ledger.total_payments)} paid of {currency(ledger.total_charges)}
                </p>
              </div>
              <Link
                to={`/billing?patient=${ledger.patient}`}
                className="shrink-0 rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
              >
                Take payment
              </Link>
            </div>
          </div>
        ))}
      </div>
    </Page>
  );
}
