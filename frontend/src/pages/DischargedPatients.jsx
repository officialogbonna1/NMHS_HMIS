import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client.js";
import { errorMessage } from "../api/errors.js";
import { PrintButton } from "../components/printing.jsx";
import { AdmissionDetail, DischargeDetail } from "../components/AdmissionDetail.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge, Button, Card, EmptyState, ErrorState, Field, FilterBar, Input, MetaStat, Page,
  PageHeader, SearchInput, Section, Select, SkeletonRows, Table, TableWrap, Td, Th,
  THead, Tr,
} from "../components/ui.jsx";

/**
 * Discharged patients — Super Admin only, the register behind the workspace.
 *
 * Reads `/admin-discharges/`, which is the same `DischargeSummary` rows the
 * ward writes; there is no separate discharge record for administration.
 * Search, the date range and the ward filter are all applied **in the
 * database** by the viewset, so the list is not a page of rows narrowed in the
 * browser — the same rule the debtors list follows.
 *
 * The patient is addressed by UUID in the link and by hospital number on the
 * page (rule 32): no primary key is ever printed at a person.
 */
export default function DischargedPatients() {
  // `PrintButton` resolves which documents this role may print
  // (`components/printing.jsx`), and with no role it resolves *none* and
  // renders nothing at all — which is why the discharge letter was missing
  // from this page. The registry's `roles: ["admin"]` mirrors `IsSuperAdmin`
  // on the endpoint behind it and is what actually decides.
  const { user } = useAuth();
  const [search, setSearch] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [status, setStatus] = useState("");
  const [opened, setOpened] = useState(null);   // the discharge being read

  const params = useMemo(() => {
    const query = {};
    if (search.trim()) query.search = search.trim();
    if (from) query.discharged_from = from;
    if (to) query.discharged_to = to;
    if (status) query.admission__status = status;
    return query;
  }, [search, from, to, status]);

  const discharges = useQuery({
    queryKey: ["discharged", params],
    queryFn: async () => (await api.get("/admin-discharges/", { params })).data,
  });

  const rows = useMemo(() => {
    const data = discharges.data;
    return Array.isArray(data) ? data : data?.results ?? [];
  }, [discharges.data]);

  // The admission behind the opened discharge, for the stay and the bed
  // history. `/admissions/<id>/` is the existing endpoint the bed board reads;
  // an admin passes `RoleRequired(BED_BOARD_ROLES)` through the admin bypass,
  // so this adds no permission and no new route.
  const admission = useQuery({
    queryKey: ["admission", opened?.admission],
    queryFn: async () => (await api.get(`/admissions/${opened.admission}/`)).data,
    enabled: Boolean(opened?.admission),
  });

  // How many filters are narrowing the list, and what the date range says —
  // so the page can state what is applied and offer one control to undo it,
  // rather than leaving somebody to work out why a register reads empty.
  const activeCount = [search.trim(), from, to, status].filter(Boolean).length;
  const periodLabel = from && to ? `${shortDate(from)} – ${shortDate(to)}`
    : from ? `from ${shortDate(from)}`
      : to ? `to ${shortDate(to)}`
        : "";

  function clearFilters() {
    setSearch(""); setFrom(""); setTo(""); setStatus("");
  }

  return (
    <Page>
      <PageHeader
        icon="clipboard"
        title="Discharged patients"
        description="Every completed discharge, with its letter."
        // `meta` takes nodes, not data: a bare {label, value} object here
        // threw "Objects are not valid as a React child" and took the whole
        // page down on render, which is why the register could not be opened
        // at all. `MetaStat` is the primitive every other masthead uses.
        meta={
          <>
            <MetaStat value={rows.length}
                      label={`discharge${rows.length === 1 ? "" : "s"} shown`} />
            {periodLabel && <MetaStat value={periodLabel} label="period" />}
          </>
        }
        actions={
          <Button variant="soft" onClick={() => discharges.refetch()}
                  loading={discharges.isFetching}>
            {discharges.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {/* One filter toolbar rather than four loose controls. They were wedged
          into the masthead, where on anything narrower than a laptop they
          wrapped into a ragged column — a search box with no visible label
          sitting ~22px above two bare `mm/dd/yyyy` inputs and a select.
          `FilterBar` puts every label on one line and every control on the
          next, at one height across the row; `showLabel` is what brings the
          search box down to join them. */}
      <FilterBar
        title="Find a discharge"
        className="mb-4 sm:mb-5"
        actions={activeCount > 0 && (
          <Button variant="link" size="xs" onClick={clearFilters}>
            Clear {activeCount} filter{activeCount === 1 ? "" : "s"}
          </Button>
        )}
      >
        <SearchInput
          showLabel
          value={search}
          onChange={setSearch}
          label="Search"
          placeholder="Name, hospital no. or reference…"
        />
        {/* Two dates are one range: bounded against each other so "from"
            cannot be set past "to". */}
        <Field label="Discharged from">
          <Input type="date" value={from} max={to || undefined}
                 onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label="Discharged to">
          <Input type="date" value={to} min={from || undefined}
                 onChange={(e) => setTo(e.target.value)} />
        </Field>
        <Field label="Admission status">
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            <option value="discharged">Discharged</option>
            <option value="admitted">Still admitted</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        </Field>
      </FilterBar>

      <Section title="Completed discharges">
        {discharges.isLoading && <SkeletonRows rows={5} />}
        {discharges.isError && (
          <ErrorState
            title="Could not load discharges"
            description={errorMessage(discharges.error)}
            onRetry={() => discharges.refetch()}
          />
        )}
        {!discharges.isLoading && !discharges.isError && rows.length === 0 && (
          <EmptyState
            icon="clipboard"
            title={activeCount > 0 ? "No discharges match these filters" : "No discharges yet"}
            description={activeCount > 0
              ? "Widen the dates or clear the search — the register itself may not be empty."
              : "A discharge appears here the moment one is completed, with its letter."}
            action={activeCount > 0
              ? <Button variant="secondary" size="sm" onClick={clearFilters}>Clear filters</Button>
              : <Button variant="secondary" size="sm" to="/discharge">Discharge a patient</Button>}
          />
        )}
        {rows.length > 0 && (
          <Card>
            <TableWrap>
              <Table>
                <THead>
                  <Tr>
                    <Th>Patient</Th>
                    <Th>Hospital no.</Th>
                    <Th>Admission</Th>
                    <Th>Admitted</Th>
                    <Th>Discharged</Th>
                    <Th>Discharged by</Th>
                    <Th>Condition</Th>
                    <Th>Reference</Th>
                    <Th className="text-right">Actions</Th>
                  </Tr>
                </THead>
                <tbody>
                  {rows.map((row) => (
                    <Tr
                      key={row.id}
                      className={opened?.id === row.id ? "bg-brand-50/50" : undefined}
                    >
                      <Td className="font-medium text-slate-900">{row.patient_name}</Td>
                      <Td className="whitespace-nowrap text-slate-700">{row.patient_number}</Td>
                      <Td className="text-slate-700">
                        <div className="whitespace-nowrap font-medium text-slate-800">
                          {row.admission_reference}
                        </div>
                        <div className="text-xs text-slate-600">
                          {row.ward_name ? `${row.ward_name} · Bed ${row.bed_number}` : "—"}
                        </div>
                      </Td>
                      <Td className="whitespace-nowrap text-slate-700">{formatDate(row.admitted_at)}</Td>
                      <Td className="whitespace-nowrap text-slate-700">{formatDate(row.discharged_at)}</Td>
                      <Td className="text-slate-700">{row.completed_by_name || "—"}</Td>
                      <Td>
                        <Badge tone={row.admission_status === "discharged" ? "success" : "neutral"}>
                          {row.condition || titleCase(row.admission_status)}
                        </Badge>
                      </Td>
                      <Td className="whitespace-nowrap font-medium text-slate-800">{row.reference}</Td>
                      <Td>
                        <div className="flex justify-end gap-2">
                          <Button variant="secondary" size="xs" onClick={() => setOpened(row)}>
                            View
                          </Button>
                          <PrintButton
                            role={user?.role}
                            size="xs"
                            documents={["discharge_letter"]}
                            context={{ discharge: row, dischargeId: row.id }}
                            label="Discharge letter"
                          />
                        </div>
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </TableWrap>
          </Card>
        )}
      </Section>

      {/* The completed record, read back: the discharge itself, and the
          admission and bed history behind it. Read-only — a signed discharge
          is the evidence the letter is printed from, and there is no amendment
          trail on this model to edit it through. */}
      {opened && (
        <Section
          className="mt-6"
          title="Discharge record"
          description={`${opened.patient_name} · ${opened.reference}`}
          actions={
            <div className="flex flex-wrap gap-2">
              <PrintButton
                role={user?.role}
                size="xs"
                documents={["discharge_letter"]}
                context={{ discharge: opened, dischargeId: opened.id }}
                label="Print discharge letter"
              />
              <Button variant="secondary" size="xs" onClick={() => setOpened(null)}>
                Close
              </Button>
            </div>
          }
        >
          <div className="grid gap-4 xl:grid-cols-2">
            <DischargeDetail discharge={opened} />
            {admission.isLoading && <SkeletonRows rows={4} />}
            {admission.data && (
              <AdmissionDetail admission={admission.data} title="Admission history" />
            )}
          </div>
        </Section>
      )}
    </Page>
  );
}

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString() : "—";
}

function shortDate(value) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value
    : parsed.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function titleCase(value) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "—";
}
