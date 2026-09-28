import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import { useConfirm } from "../components/ConfirmAlert.jsx";
import { useToast } from "../components/Toaster.jsx";
import { useLocations } from "../components/StockPanels.jsx";
import {
  Alert, Badge, Button, EmptyState, ErrorState, Field, MetaStat, Modal, Page, PageHeader,
  SearchInput, Select, Skeleton, Table, TableWrap, Td, Textarea, Th, THead, Tr,
} from "../components/ui.jsx";

// **Expired Items** — inventory administration's register of lots that are no
// longer usable. Batch-level, never product-level: Paracetamol with one lot past
// its date and one good for a year shows the first here and keeps selling the
// second. The product is not archived by it.
//
// Two ways a lot gets here, one definition (`inventory.models.expired_q`):
// its printed expiry date has passed on the hospital's calendar — nothing is
// run, it simply appears — or an administrator marked it expired, with a
// reason. Either way dispensing, the till and FEFO transfers stop drawing on
// it, and nothing is deleted: the batch keeps its number, dates, prices,
// supplier and stock until it is written off, and the write-off is a movement.

const STATUS = {
  expired: ["Past expiry date", "danger"],
  marked_expired: ["Marked expired", "warning"],
};

export default function ExpiredItems() {
  const [search, setSearch] = useState("");
  const [location, setLocation] = useState("");
  const [status, setStatus] = useState("");
  const [includeEmpty, setIncludeEmpty] = useState(false);
  const [marking, setMarking] = useState(false);
  const { data: locations } = useLocations();

  const params = {
    ...(search.trim() ? { search: search.trim() } : {}),
    ...(location ? { location } : {}),
    ...(status ? { status } : {}),
    ...(includeEmpty ? { include_empty: 1 } : {}),
  };
  const register = useQuery({
    queryKey: ["expired-items", params],
    queryFn: () => api.get("/stock-records/expired/", { params }).then((r) => r.data),
  });
  const rows = register.data?.results ?? [];

  return (
    <Page width="wide">
      <PageHeader
        icon="alert"
        title="Expired Items"
        subtitle="Batches past their expiry date or taken out of use, shelf by shelf. They are not dispensed or sold, and stay on file until written off."
        meta={register.data ? (
          <>
            <MetaStat value={register.data.count} label="lines" tone="danger" />
            <MetaStat value={register.data.units} label="units" />
          </>
        ) : null}
        actions={<Button onClick={() => setMarking(true)}>Mark a batch expired</Button>}
        toolbar={
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1 sm:max-w-xs">
              <SearchInput value={search} onChange={setSearch}
                           placeholder="Search product, batch or SKU…" />
            </div>
            <div className="w-full sm:w-44">
              <Select aria-label="Location" value={location}
                      onChange={(e) => setLocation(e.target.value)}>
                <option value="">All locations</option>
                {(locations ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </Select>
            </div>
            <div className="w-full sm:w-48">
              <Select aria-label="Why" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">Every reason</option>
                <option value="expired">Past expiry date</option>
                <option value="marked_expired">Marked expired</option>
              </Select>
            </div>
            <label className="flex min-h-[44px] items-center gap-2 text-sm text-slate-800 sm:min-h-[38px]">
              <input type="checkbox" checked={includeEmpty}
                     onChange={(e) => setIncludeEmpty(e.target.checked)}
                     className="h-4 w-4 rounded border-slate-300" />
              Include written off
            </label>
          </div>
        }
      />

      {register.isLoading && <Skeleton className="h-40" />}
      {register.isError && (
        <ErrorState title="Could not load expired items." onRetry={register.refetch} />
      )}
      {!register.isLoading && !register.isError && rows.length === 0 && (
        <EmptyState icon="check" title="Nothing expired"
                    description="No batch on these shelves is past its date or marked expired." />
      )}

      {rows.length > 0 && (
        <div className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white">
          <TableWrap>
            <Table className="min-w-[56rem]">
              <THead>
                <Tr>
                  <Th>Item</Th>
                  <Th>Batch</Th>
                  <Th>Location</Th>
                  <Th className="text-right">Qty</Th>
                  <Th>Expiry date</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Actions</Th>
                </Tr>
              </THead>
              <tbody>
                {rows.map((row) => <ExpiredRow key={row.id} row={row} />)}
              </tbody>
            </Table>
          </TableWrap>
        </div>
      )}

      {marking && <MarkExpiredModal onClose={() => setMarking(false)} />}
    </Page>
  );
}

export function ExpiredRow({ row }) {
  const { ask } = useConfirm();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [returning, setReturning] = useState(false);
  const [label, tone] = row.quantity === 0 ? ["Written off", "neutral"]
    : STATUS[row.expiry_status] ?? ["Expired", "danger"];

  const writeOff = useMutation({
    mutationFn: () => api.post(`/batches/${row.batch}/write_off/`, { location: row.location }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["expired-items"] });
      queryClient.invalidateQueries({ queryKey: ["stock-records"] });
      showToast({ title: "Written off", message: `${row.item_name} batch ${row.batch_no} at ${row.location_name}` });
    },
    onError: (error) => showToast({
      title: "Not written off", message: readError(error, "Please try again."), tone: "error",
    }),
  });

  return (
    <>
      <Tr>
        <Td className="font-medium text-slate-900">
          {row.item_name}
          {row.item_sku && <span className="block text-xs font-normal text-slate-600">{row.item_sku}</span>}
        </Td>
        <Td className="text-slate-800">{row.batch_no}</Td>
        <Td className="text-slate-800">{row.location_name}</Td>
        <Td className="text-right font-medium tabular-nums text-slate-900">{row.quantity}</Td>
        <Td className="text-slate-800">{row.expiry_date}</Td>
        <Td><Badge tone={tone}>{label}</Badge></Td>
        <Td className="text-right">
          <div className="flex flex-wrap justify-end gap-1">
            <Button variant="link" size="xs" aria-expanded={open}
                    onClick={() => setOpen((v) => !v)}>
              {open ? "Hide" : "Details"}
            </Button>
            {row.expiry_status === "marked_expired" && (
              <Button variant="linkMuted" size="xs" onClick={() => setReturning(true)}>
                Return to use
              </Button>
            )}
            {row.quantity > 0 && (
              <Button
                variant="linkDanger" size="xs" disabled={writeOff.isPending}
                onClick={async () => {
                  if (await ask({
                    title: "Write off this expired stock?",
                    message: `${row.quantity} unit(s) of ${row.item_name} batch ${row.batch_no} at `
                      + `${row.location_name} will be taken off the shelf. The batch and its `
                      + "history stay on file; the write-off is recorded as a movement.",
                    confirmLabel: "Write it off",
                  })) writeOff.mutate();
                }}
              >
                Write off
              </Button>
            )}
          </div>
        </Td>
      </Tr>
      {open && (
        <Tr>
          <Td colSpan={7} className="bg-slate-50">
            <BatchDetails row={row} />
          </Td>
        </Tr>
      )}
      {returning && <ReturnToUseModal row={row} onClose={() => setReturning(false)} />}
    </>
  );
}

function BatchDetails({ row }) {
  const history = useQuery({
    queryKey: ["stock-movements", "batch", row.batch],
    queryFn: () => api.get("/stock-movements/", { params: { batch: row.batch, page_size: 100 } })
      .then((r) => r.data.results ?? r.data),
  });
  const facts = [
    ["Supplier", row.supplier || "—"],
    ["Received", row.received_date || "—"],
    ["Cost price", row.cost_price],
    ["Sale price", row.sale_price],
  ];
  return (
    <div className="space-y-3">
      {row.marked_expired_at && (
        <Alert tone="warning" title="Marked expired">
          By {row.marked_expired_by_name ?? "—"} on {new Date(row.marked_expired_at).toLocaleString()}:
          {" "}{row.marked_expired_reason}
        </Alert>
      )}
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {facts.map(([term, value]) => (
          <div key={term} className="min-w-0">
            <dt className="text-xs text-slate-600">{term}</dt>
            <dd className="text-sm text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>
      <div>
        <h3 className="mb-1 text-sm font-semibold text-slate-800">Movement history (every location)</h3>
        {history.isLoading && <Skeleton className="h-16" />}
        {history.isError && <p className="text-sm text-red-700">Could not load the history.</p>}
        {history.data && history.data.length === 0 && (
          <p className="text-sm text-slate-700">No movements recorded.</p>
        )}
        {history.data && history.data.length > 0 && (
          <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
            {history.data.map((m) => (
              <li key={m.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2 text-sm">
                <span className="text-slate-600">{new Date(m.created_at).toLocaleString()}</span>
                <span className="font-medium text-slate-900">{m.reason_label}</span>
                <span className={`tabular-nums ${m.change < 0 ? "text-amber-800" : "text-emerald-700"}`}>
                  {m.change > 0 ? "+" : ""}{m.change}
                </span>
                <span className="text-slate-700">{m.location_name}</span>
                <span className="text-slate-600">{m.performed_by_name}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ReasonForm({ reason, setReason, label, hint }) {
  return (
    <Field label={label} hint={hint} required>
      <Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
    </Field>
  );
}

export function MarkExpiredModal({ onClose }) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [batch, setBatch] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState(null);

  // Lots still usable — the only ones there is anything to mark.
  const batches = useQuery({
    queryKey: ["batches", "usable"],
    queryFn: () => api.get("/batches/", { params: { usable: 1, page_size: 500 } })
      .then((r) => r.data.results ?? r.data),
  });
  const withStock = useMemo(
    () => (batches.data ?? []).filter((b) => b.total_quantity > 0),
    [batches.data],
  );

  const mark = useMutation({
    mutationFn: () => api.post(`/batches/${batch}/mark-expired/`, { reason: reason.trim() }),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ["expired-items"] });
      queryClient.invalidateQueries({ queryKey: ["batches"] });
      queryClient.invalidateQueries({ queryKey: ["stock-records"] });
      showToast({ title: "Marked expired",
                  message: `${response.data.item_name} batch ${response.data.batch_no}` });
      onClose();
    },
    onError: (err) => setError(readError(err, "Could not mark this batch.")),
  });

  return (
    <Modal
      open onClose={onClose} title="Mark a batch expired"
      description="The batch keeps its number, dates, prices and stock, and stops being dispensed or sold. It can be returned to use if this was a mistake."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => { setError(null); mark.mutate(); }}
                  disabled={!batch || !reason.trim() || mark.isPending}>
            {mark.isPending ? "Marking…" : "Mark expired"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Batch" required>
          <Select value={batch} onChange={(e) => setBatch(e.target.value)}>
            <option value="">{batches.isLoading ? "Loading…" : "Select a batch…"}</option>
            {withStock.map((b) => (
              <option key={b.id} value={b.id}>
                {b.item_name} — batch {b.batch_no} · expires {b.expiry_date} · {b.total_quantity} {b.item_unit}
              </option>
            ))}
          </Select>
        </Field>
        <ReasonForm reason={reason} setReason={setReason} label="Reason"
                    hint="Recorded in the audit log — e.g. recalled by the supplier, cold chain broken." />
        {error && <p className="text-sm text-red-700">{error}</p>}
      </div>
    </Modal>
  );
}

function ReturnToUseModal({ row, onClose }) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [error, setError] = useState(null);
  const back = useMutation({
    mutationFn: () => api.post(`/batches/${row.batch}/return-to-use/`, { reason: reason.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["expired-items"] });
      queryClient.invalidateQueries({ queryKey: ["batches"] });
      showToast({ title: "Returned to use", message: `${row.item_name} batch ${row.batch_no}` });
      onClose();
    },
    onError: (err) => setError(readError(err, "Could not return this batch to use.")),
  });
  return (
    <Modal
      open onClose={onClose} title={`Return batch ${row.batch_no} to use?`}
      description="Clears the manual mark. It does not change the printed expiry date."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => { setError(null); back.mutate(); }}
                  disabled={!reason.trim() || back.isPending}>
            {back.isPending ? "Saving…" : "Return to use"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-slate-700">Marked because: {row.marked_expired_reason}</p>
        <ReasonForm reason={reason} setReason={setReason} label="Why it is usable again" />
        {error && <p className="text-sm text-red-700">{error}</p>}
      </div>
    </Modal>
  );
}
