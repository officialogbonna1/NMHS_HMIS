import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import { useToast } from "./Toaster.jsx";
import {
  Alert, Badge, Button, EmptyState, ErrorState, Field, Input, Modal, PageHeader, MetaStat, SearchInput,
  Select, Skeleton, Table, TableWrap, TabBar, Tab, Td, Th, THead, Tr, naira,
} from "./ui.jsx";

// The stock panels, shared by the two workspaces that need them.
//
// **Administration owns inventory; Pharmacy operates pharmacy stock.** Both
// are looking at the same ledger, so they use the same components rather than
// two implementations that drift: Administration → Inventory renders them
// across every location, and the Pharmacy counter renders the same panels with
// `lockedLocation` set to the dispensing shelf.
//
// `lockedLocation` is the whole difference. When it is set, the location
// selector disappears and every query, count and transfer is pinned to that
// location — a pharmacist counting "the shelf" cannot accidentally post the
// count against the Main Store, and a pharmacy transfer can only be *into*
// the pharmacy. Nothing about the API changes: the same endpoints, the same
// services, the same movements.

const currency = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 })
    .format(n ?? 0);

const REASON_LABEL = {
  received: "Received",
  transfer_out: "Transferred out",
  transfer_in: "Transferred in",
  prescription: "Dispensed",
  sale: "POS sale",
  adjustment: "Stock count",
  stock_count: "Physical stock count (CSV)",
  returned: "Customer return (quarantined)",
  expired_writeoff: "Expired write-off",
};

const REASON_TONE = {
  received: "success",
  transfer_in: "info",
  transfer_out: "info",
  prescription: "brand",
  sale: "brand",
  adjustment: "warning",
  stock_count: "warning",
  returned: "violet",
  expired_writeoff: "danger",
};

/* ------------------------------------------------------------------ queries */

export function useLocations() {
  return useQuery({
    queryKey: ["stock-locations"],
    queryFn: () => api.get("/stock-locations/").then((r) => r.data.results ?? r.data),
  });
}

export function useStockRecords(locationId, categoryId) {
  return useQuery({
    // The category narrows the list on the server, not in the browser: with
    // 500 rows to a page, filtering what arrived would hide whatever did not.
    queryKey: ["stock-records", locationId ?? "all", categoryId ?? "all"],
    queryFn: () => api.get("/stock-records/", {
      params: {
        page_size: 500,
        ...(locationId ? { location: locationId } : {}),
        ...(categoryId ? { batch__item__category: categoryId } : {}),
      },
    }).then((r) => r.data.results ?? r.data),
  });
}

// One list of categories behind every picker on these panels, so the stock
// filter, the count-sheet export and the movement log all offer the same
// groups the catalogue actually has.
export function useItemCategories() {
  return useQuery({
    queryKey: ["item-categories", "picker"],
    queryFn: () => api.get("/item-categories/", { params: { page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });
}

export function useItems() {
  return useQuery({
    queryKey: ["items"],
    queryFn: () => api.get("/items/", { params: { page_size: 500 } })
      .then((r) => r.data.results ?? r.data),
  });
}

export function useRefresh() {
  const queryClient = useQueryClient();
  return () => {
    for (const key of ["items", "batches", "stock-records", "stock-movements",
                       "stock-locations", "stock-transfers", "count-sheet"]) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

/* ------------------------------------------------------------ stock on hand */

export function StockOnHand({ locations, lockedLocation }) {
  const [locationId, setLocationId] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [term, setTerm] = useState("");
  // Locked: this panel is one shelf, and the filter is not the user's to move.
  // The category is the user's either way — it narrows what is on the shelf,
  // it does not change which shelf.
  const active = lockedLocation ? lockedLocation.id : (locationId || null);
  const { data: records, isLoading } = useStockRecords(active, categoryId || null);
  const { data: items } = useItems();
  const categories = useItemCategories();

  const search = term.trim().toLowerCase();
  const visible = (records ?? []).filter((r) => !search || [
    r.item_name, r.item_sku, r.item_category_name, r.batch_no,
  ].some((field) => (field ?? "").toLowerCase().includes(search)));

  // The low-stock and expiry panels answer the same question the table does,
  // so the category narrows them too — "what Pain Relief is running out".
  const lowStock = (items ?? []).filter(
    (i) => i.is_low_stock && (!categoryId || String(i.category) === String(categoryId)));
  const expiringSoon = visible.filter(
    (r) => r.quantity > 0 && daysUntil(r.expiry_date) <= 30);

  if (isLoading) return <p className="text-slate-600">Loading…</p>;

  const categoryName = (categories.data ?? [])
    .find((c) => String(c.id) === String(categoryId))?.name;

  return (
    <div className="space-y-6">
      <div className="grid min-w-0 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {!lockedLocation && (
          <Field label="Location">
            <Select aria-label="Location" value={locationId}
                    onChange={(e) => setLocationId(e.target.value)}>
              <option value="">Everywhere in the hospital</option>
              {(locations ?? []).map((l) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </Select>
          </Field>
        )}
        <Field label="Category">
          <Select aria-label="Category" value={categoryId}
                  onChange={(e) => setCategoryId(e.target.value)}>
            <option value="">Every category</option>
            {(categories.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Search">
          <SearchInput value={term} onChange={setTerm}
                       placeholder="Product, SKU, category or batch…" />
        </Field>
      </div>

      {!lockedLocation && lowStock.length > 0 && (
        <section>
          <h2 className="mb-2 font-semibold text-slate-900">
            Low stock ({lowStock.length})
          </h2>
          <p className="mb-2 text-sm text-slate-600">
            Counted across every location — a drug is only low when the hospital as a whole is low.
          </p>
          <div className="grid gap-2">
            {lowStock.map((i) => (
              <div key={i.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-200 bg-red-50 p-3">
                <span className="font-medium text-slate-900">{i.name}</span>
                <span className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
                  {(i.by_location ?? []).map((l) => (
                    <Badge key={l.code} tone="neutral">{l.name}: {l.quantity}</Badge>
                  ))}
                  <span className="text-red-800">
                    {i.total_quantity} / threshold {i.reorder_threshold}
                  </span>
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {expiringSoon.length > 0 && (
        <section>
          <h2 className="mb-2 font-semibold text-slate-900">
            Expiring within 30 days ({expiringSoon.length})
          </h2>
          <p className="mb-2 text-sm text-slate-600">
            One line per shelf: the store and the counter are walked by different people.
          </p>
          <div className="grid gap-2">
            {expiringSoon.map((r) => (
              <div key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
                <span className="text-slate-900">
                  {r.item_name} — batch {r.batch_no}
                  <Badge tone="neutral" className="ml-2">{r.location_name}</Badge>
                </span>
                <span className="text-sm text-slate-700">{r.expiry_date} · qty {r.quantity}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="mb-1 font-semibold text-slate-900">
          {lockedLocation ? `Stock on the ${lockedLocation.name} shelf` : "Stock by batch and location"}
        </h2>
        <p className="mb-3 text-sm text-slate-600">
          {lockedLocation
            ? "Every batch standing here, with its expiry. This is what can be dispensed — "
              + "stock in the Main Store has to be transferred first."
            : "The same batch in two locations is two lines, and only what is in the Pharmacy can be "
              + "dispensed. Counting corrects one shelf and logs the difference."}
        </p>
        {visible.length === 0 ? (
          <EmptyState
            icon="📦"
            title={search || categoryId ? "Nothing matches that" : "Nothing on this shelf"}
            description={search || categoryId
              ? `No stock here${categoryName ? ` under ${categoryName}` : ""}`
                + `${search ? ` matching “${term.trim()}”` : ""}. Clear the filters to see everything.`
              : lockedLocation
                ? "Transfer stock from the Main Store to start dispensing."
                : "Receive a delivery into the Main Store, then transfer what the counter needs."}
          />
        ) : (
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <TableWrap>
              <Table className="min-w-[62rem]">
                <THead>
                  <Tr>
                    <Th>Product</Th>
                    <Th>Category</Th>
                    <Th>SKU</Th>
                    <Th>Unit</Th>
                    <Th>Location</Th>
                    <Th>Batch</Th>
                    <Th>Expiry</Th>
                    <Th className="text-right">Quantity</Th>
                    <Th>Status</Th>
                    <Th className="text-right">Actions</Th>
                  </Tr>
                </THead>
                <tbody>
                  {visible.map((record) => (
                    <StockRow key={record.id} record={record} />
                  ))}
                </tbody>
              </Table>
            </TableWrap>
          </div>
        )}
      </section>
    </div>
  );
}

/**
 * What this line is: expired, expiring, out, or fine. One reading of the two
 * facts a stock line carries — its expiry date and its quantity — so the table
 * says the same thing the expiry panel above it does.
 */
function StockStatus({ record }) {
  const days = daysUntil(record.expiry_date);
  if (record.is_expired) return <Badge tone="danger">Expired</Badge>;
  if (record.quantity === 0) return <Badge tone="neutral">Out of stock</Badge>;
  if (days <= 30) return <Badge tone="warning">Expires in {days}d</Badge>;
  return <Badge tone="success">In stock</Badge>;
}

function StockRow({ record }) {
  const { showToast } = useToast();
  const refresh = useRefresh();
  const [counting, setCounting] = useState(false);
  const [counted, setCounted] = useState("");

  const submitCount = useMutation({
    // The location travels with the count: an adjustment posted against the
    // wrong shelf invents stock in one place and destroys it in the other,
    // so the API refuses a count that does not name one.
    mutationFn: () => api.post(`/batches/${record.batch}/count/`, {
      counted_quantity: Number(counted), location: record.location,
    }),
    onSuccess: () => {
      refresh();
      setCounting(false);
      setCounted("");
      showToast({ title: "Stock count recorded", message: `${record.item_name} at ${record.location_name}` });
    },
    onError: (error) => showToast({
      title: "Could not record count", message: readError(error, "Please try again."), tone: "error",
    }),
  });

  const writeOff = useMutation({
    mutationFn: () => api.post(`/batches/${record.batch}/write_off/`, { location: record.location }),
    onSuccess: () => { refresh(); showToast({ title: "Expired stock written off" }); },
    onError: (error) => showToast({
      title: "Could not write off", message: readError(error, "Please try again."), tone: "error",
    }),
  });

  return (
    <>
      <Tr>
        <Td className="font-medium text-slate-900">{record.item_name}</Td>
        <Td className="text-slate-700">{record.item_category_name || "—"}</Td>
        <Td className="text-slate-700">{record.item_sku || "—"}</Td>
        <Td className="text-slate-700">{record.item_unit}</Td>
        <Td><Badge tone={record.location_code === "pharmacy" ? "brand" : "neutral"}>
          {record.location_name}
        </Badge></Td>
        <Td>{record.batch_no}</Td>
        <Td>{record.expiry_date}</Td>
        <Td className="text-right font-medium tabular-nums text-slate-900">{record.quantity}</Td>
        <Td><StockStatus record={record} /></Td>
        <Td className="text-right">
          <div className="flex justify-end gap-1">
            <Button variant="link" size="xs" onClick={() => setCounting((c) => !c)}>Count</Button>
            {record.is_expired && record.quantity > 0 && (
              <Button
                variant="linkDanger" size="xs"
                onClick={() => confirm(`Write off ${record.quantity} unit(s) of this expired batch at ${record.location_name}?`) && writeOff.mutate()}
              >
                Write off
              </Button>
            )}
          </div>
        </Td>
      </Tr>
      {counting && (
        <Tr>
          <Td colSpan={10} className="bg-slate-50">
            <form
              onSubmit={(e) => { e.preventDefault(); if (counted !== "") submitCount.mutate(); }}
              className="flex flex-wrap items-end gap-3"
            >
              <div className="w-40">
                <Field label={`Counted at ${record.location_name}`}>
                  <Input type="number" min="0" value={counted} autoFocus
                         onChange={(e) => setCounted(e.target.value)} />
                </Field>
              </div>
              <Button type="submit" size="sm" disabled={counted === "" || submitCount.isPending}>
                {submitCount.isPending ? "Saving…" : "Record count"}
              </Button>
              <Button variant="linkMuted" size="sm" type="button"
                      onClick={() => { setCounting(false); setCounted(""); }}>
                Cancel
              </Button>
              {counted !== "" && Number(counted) !== record.quantity && (
                <span className="text-sm text-amber-800">
                  Difference {Number(counted) - record.quantity > 0 ? "+" : ""}
                  {Number(counted) - record.quantity}
                </span>
              )}
            </form>
          </Td>
        </Tr>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- receiving */

export function ReceiveStock({ locations, store }) {
  const { showToast } = useToast();
  const refresh = useRefresh();
  const { data: items } = useItems();
  const [form, setForm] = useState({
    item: "", batch_no: "", quantity: "", cost_price: "", sale_price: "",
    expiry_date: "", supplier: "", location: "",
  });

  const receive = useMutation({
    mutationFn: () => api.post("/batches/", {
      ...form,
      item: Number(form.item),
      opening_quantity: Number(form.quantity),
      location: form.location ? Number(form.location) : undefined,
    }),
    onSuccess: () => {
      refresh();
      showToast({
        title: "Stock received",
        message: `${form.quantity} unit(s) into ${locationName(locations, form.location) ?? store?.name ?? "the store"}`,
      });
      setForm({ item: "", batch_no: "", quantity: "", cost_price: "", sale_price: "",
                expiry_date: "", supplier: "", location: form.location });
    },
    onError: (error) => showToast({
      title: "Could not receive stock",
      message: readError(error, "Check the fields and try again."),
      tone: "error",
    }),
  });

  const set = (field, value) => setForm((f) => ({ ...f, [field]: value }));
  const canSubmit = form.item && form.batch_no && form.quantity && form.cost_price
    && form.sale_price && form.expiry_date;

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (canSubmit) receive.mutate(); }}
      className="max-w-2xl space-y-4 rounded-xl border border-slate-200 bg-white p-5"
    >
      <div>
        <h2 className="font-semibold text-slate-900">Receive a delivery</h2>
        <p className="mt-1 text-sm text-slate-600">
          Each delivery is its own batch, so expiry and cost are tracked per lot. Goods arrive in
          the <strong>{store?.name ?? "Main Store"}</strong> and reach the Pharmacy by transfer —
          that is what keeps the two shelves honest.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="md:col-span-2">
          <Field label="Drug" required>
            <Select value={form.item} onChange={(e) => set("item", e.target.value)}>
              <option value="">Select drug</option>
              {(items ?? []).map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
            </Select>
          </Field>
        </div>
        <Field label="Batch number" required>
          <Input value={form.batch_no} onChange={(e) => set("batch_no", e.target.value)} />
        </Field>
        <Field label="Quantity" required>
          <Input type="number" min="1" value={form.quantity}
                 onChange={(e) => set("quantity", e.target.value)} />
        </Field>
        <Field label="Cost price" required>
          <Input type="number" min="0" step="0.01" value={form.cost_price}
                 onChange={(e) => set("cost_price", e.target.value)} />
        </Field>
        <Field label="Sale price" required>
          <Input type="number" min="0" step="0.01" value={form.sale_price}
                 onChange={(e) => set("sale_price", e.target.value)} />
        </Field>
        <Field label="Expiry date" required>
          <Input type="date" value={form.expiry_date}
                 onChange={(e) => set("expiry_date", e.target.value)} />
        </Field>
        <Field label="Supplier">
          <Input value={form.supplier} onChange={(e) => set("supplier", e.target.value)} />
        </Field>
        <div className="md:col-span-2">
          <Field
            label="Receive into"
            hint="Leave as the Main Store unless this delivery was taken straight to the counter."
          >
            <Select value={form.location} onChange={(e) => set("location", e.target.value)}>
              <option value="">{store?.name ?? "Main Store"} (default)</option>
              {(locations ?? []).filter((l) => l.id !== store?.id).map((l) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </Select>
          </Field>
        </div>
      </div>

      <Button type="submit" disabled={!canSubmit || receive.isPending}>
        {receive.isPending ? "Receiving…" : "Receive stock"}
      </Button>
    </form>
  );
}

/* ---------------------------------------------------------------- transfers */

export function TransferStock({ locations, store, counter, lockedLocation }) {
  const { showToast } = useToast();
  const refresh = useRefresh();
  const { data: items } = useItems();
  const [source, setSource] = useState("");
  const [destination, setDestination] = useState("");
  const [note, setNote] = useState("");
  const [lines, setLines] = useState([]);
  const [draft, setDraft] = useState({ batch: "", quantity: "" });

  // Locked: stock is being brought *into* this location, so the destination is
  // fixed and the source is anywhere else. That is the Main Store → Pharmacy
  // half of the workflow, which is the only transfer a counter makes.
  const sourceId = Number(source || (lockedLocation ? store?.id : store?.id) || 0);
  const destinationId = lockedLocation
    ? lockedLocation.id
    : Number(destination || counter?.id || 0);
  const { data: sourceStock } = useStockRecords(sourceId || null);

  // Only what is actually standing in the source, and not expired: a
  // transfer of expired stock just moves the problem to another shelf.
  const available = useMemo(
    () => (sourceStock ?? []).filter((r) => r.quantity > 0 && !r.is_expired),
    [sourceStock]);

  const { data: transfers } = useQuery({
    queryKey: ["stock-transfers"],
    queryFn: () => api.get("/stock-transfers/", { params: { page_size: 20 } })
      .then((r) => r.data.results ?? r.data),
  });

  const send = useMutation({
    mutationFn: () => api.post("/stock-transfers/", {
      source: sourceId,
      destination: destinationId,
      note,
      lines: lines.map((l) => ({ batch: l.batch, quantity: l.quantity })),
    }),
    onSuccess: (response) => {
      refresh();
      setLines([]);
      setNote("");
      showToast({
        title: `Transfer ${response.data.reference}`,
        message: `${response.data.total_units} unit(s) moved to ${response.data.destination_name}.`,
      });
    },
    onError: (error) => showToast({
      title: "Could not transfer", message: readError(error, "Please try again."), tone: "error",
    }),
  });

  function addLine() {
    const record = available.find((r) => String(r.id) === String(draft.batch));
    const quantity = Number(draft.quantity);
    if (!record || !quantity || quantity < 1) return;
    if (quantity > record.quantity) {
      showToast({
        title: "More than the shelf holds",
        message: `${record.item_name} batch ${record.batch_no}: only ${record.quantity} at ${record.location_name}.`,
        tone: "error",
      });
      return;
    }
    setLines((current) => [
      ...current.filter((l) => l.batch !== record.batch),
      {
        batch: record.batch, quantity,
        label: `${record.item_name} — batch ${record.batch_no}`,
        expiry: record.expiry_date,
      },
    ]);
    setDraft({ batch: "", quantity: "" });
  }

  return (
    <div className="space-y-6">
      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
        <div>
          <h2 className="font-semibold text-slate-900">
            {lockedLocation ? `Bring stock to ${lockedLocation.name}` : "Internal transfer"}
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            The only way stock moves between locations. It is applied as one document with its own
            reference number, and writes a movement at each end — never an edit to a quantity.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <Field label="From">
            <Select value={source || store?.id || ""} onChange={(e) => setSource(e.target.value)}>
              {(locations ?? [])
                .filter((l) => !lockedLocation || l.id !== lockedLocation.id)
                .map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </Select>
          </Field>
          <Field label="To">
            {lockedLocation ? (
              <Input value={lockedLocation.name} readOnly disabled />
            ) : (
              <Select value={destination || counter?.id || ""}
                      onChange={(e) => setDestination(e.target.value)}>
                {(locations ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </Select>
            )}
          </Field>
        </div>

        {sourceId === destinationId && (
          <Alert tone="warning">Choose two different locations.</Alert>
        )}

        <div className="grid gap-3 md:grid-cols-[2fr,1fr,auto] md:items-end">
          <Field label="Batch" hint="Earliest expiry first — the short-dated lot should reach the counter.">
            <Select value={draft.batch}
                    onChange={(e) => setDraft((d) => ({ ...d, batch: e.target.value }))}>
              <option value="">Select what to move</option>
              {available.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.item_name} — {r.batch_no} · exp {r.expiry_date} · {r.quantity} available
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Quantity">
            <Input type="number" min="1" value={draft.quantity}
                   onChange={(e) => setDraft((d) => ({ ...d, quantity: e.target.value }))} />
          </Field>
          <Button variant="secondary" onClick={addLine}
                  disabled={!draft.batch || !draft.quantity}>
            Add line
          </Button>
        </div>

        {lines.length > 0 && (
          <div className="overflow-hidden rounded-lg border border-slate-200">
            <TableWrap>
              <Table className="min-w-[30rem]">
                <THead>
                  <Tr><Th>Batch</Th><Th>Expiry</Th><Th className="text-right">Quantity</Th><Th /></Tr>
                </THead>
                <tbody>
                  {lines.map((line) => (
                    <Tr key={line.batch}>
                      <Td className="text-slate-900">{line.label}</Td>
                      <Td>{line.expiry}</Td>
                      <Td className="text-right tabular-nums">{line.quantity}</Td>
                      <Td className="text-right">
                        <Button variant="linkDanger" size="xs"
                                onClick={() => setLines((c) => c.filter((l) => l.batch !== line.batch))}>
                          Remove
                        </Button>
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </TableWrap>
          </div>
        )}

        <Field label="Note">
          <Input value={note} onChange={(e) => setNote(e.target.value)}
                 placeholder="e.g. Weekly top-up for the counter" />
        </Field>

        <Button
          onClick={() => send.mutate()}
          disabled={lines.length === 0 || sourceId === destinationId || send.isPending}
        >
          {send.isPending ? "Transferring…" : `Transfer ${lines.length || ""} line${lines.length === 1 ? "" : "s"}`}
        </Button>
      </section>

      <section>
        <h2 className="mb-2 font-semibold text-slate-900">Recent transfers</h2>
        {(transfers ?? []).length === 0 ? (
          <p className="text-sm text-slate-600">Nothing has been transferred yet.</p>
        ) : (
          <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
            {(transfers ?? []).map((transfer) => (
              <div key={transfer.id} className="px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-medium text-slate-900">
                    {transfer.reference}
                    <span className="ml-2 font-normal text-slate-700">
                      {transfer.source_name} → {transfer.destination_name}
                    </span>
                  </p>
                  <span className="text-sm text-slate-700">
                    {transfer.total_units} unit(s) · {new Date(transfer.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="mt-0.5 text-sm text-slate-600">
                  {transfer.lines.map((l) => `${l.item_name} ${l.batch_no} ×${l.quantity}`).join(", ")}
                  {transfer.transferred_by_name && ` · ${transfer.transferred_by_name}`}
                </p>
                {transfer.note && <p className="mt-0.5 text-sm text-slate-700">{transfer.note}</p>}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

/* --------------------------------------------------------- physical count */

export function PhysicalCount({ locations, store, lockedLocation }) {
  const { showToast } = useToast();
  const refresh = useRefresh();
  const [locationId, setLocationId] = useState("");
  const [counted, setCounted] = useState({});
  const [note, setNote] = useState("");

  const active = lockedLocation ? lockedLocation.id : Number(locationId || store?.id || 0);

  const { data: sheet, isLoading } = useQuery({
    queryKey: ["count-sheet", active],
    queryFn: () => api.get("/stock-counts/sheet/", { params: { location: active } })
      .then((r) => r.data),
    enabled: Boolean(active),
  });

  const post = useMutation({
    mutationFn: () => api.post("/stock-counts/", {
      location: active,
      note,
      lines: (sheet?.lines ?? [])
        .filter((line) => counted[line.batch] !== undefined && counted[line.batch] !== "")
        .map((line) => ({ batch: line.batch, counted_quantity: Number(counted[line.batch]) })),
    }),
    onSuccess: (response) => {
      refresh();
      setCounted({});
      setNote("");
      showToast({
        title: `Count ${response.data.reference} posted`,
        message: `${response.data.discrepancy_count} line(s) differed and were adjusted.`,
      });
    },
    onError: (error) => showToast({
      title: "Could not post the count", message: readError(error, "Please try again."), tone: "error",
    }),
  });

  const filled = (sheet?.lines ?? []).filter(
    (line) => counted[line.batch] !== undefined && counted[line.batch] !== "");

  return (
    <div className="space-y-4">
      {lockedLocation ? (
        <Alert tone="info">
          Counting <strong>{lockedLocation.name}</strong>. Each line that differs posts an
          adjustment against that batch, on this shelf only.
        </Alert>
      ) : (
        <div className="max-w-sm">
          <Field label="Location being counted"
                 hint="Counts are per location: the store and the counter are walked separately.">
            <Select value={locationId || store?.id || ""} onChange={(e) => {
              setLocationId(e.target.value);
              setCounted({});
            }}>
              {(locations ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </Select>
          </Field>
        </div>
      )}

      {isLoading && <p className="text-slate-600">Loading the count sheet…</p>}

      {sheet && sheet.lines.length === 0 && (
        <EmptyState icon="📋" title="Nothing on this shelf to count"
                    description="Receive or transfer stock here first." />
      )}

      {sheet && sheet.lines.length > 0 && (
        <>
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <TableWrap>
              <Table className="min-w-[50rem]">
                <THead>
                  <Tr>
                    <Th>Product</Th>
                    <Th>Batch</Th>
                    <Th>Location</Th>
                    <Th className="text-right">System qty</Th>
                    <Th className="text-right">Counted qty</Th>
                    <Th className="text-right">Difference</Th>
                  </Tr>
                </THead>
                <tbody>
                  {sheet.lines.map((line) => {
                    const value = counted[line.batch];
                    const difference = value === undefined || value === ""
                      ? null : Number(value) - line.system_quantity;
                    return (
                      <Tr key={line.batch}>
                        <Td className="font-medium text-slate-900">{line.item_name}</Td>
                        <Td>
                          {line.batch_no}
                          <span className="block text-xs text-slate-600">exp {line.expiry_date}</span>
                        </Td>
                        <Td>{line.location_name}</Td>
                        <Td className="text-right tabular-nums">{line.system_quantity}</Td>
                        <Td className="text-right">
                          <Input
                            type="number" min="0" className="ml-auto w-28 text-right"
                            value={value ?? ""}
                            onChange={(e) => setCounted((c) => ({ ...c, [line.batch]: e.target.value }))}
                          />
                        </Td>
                        <Td className={`text-right tabular-nums ${
                          difference === null ? "text-slate-500"
                            : difference === 0 ? "text-emerald-700" : "text-amber-800 font-medium"}`}>
                          {difference === null ? "—" : difference > 0 ? `+${difference}` : difference}
                        </Td>
                      </Tr>
                    );
                  })}
                </tbody>
              </Table>
            </TableWrap>
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-0 flex-1">
              <Field label="Note">
                <Input value={note} onChange={(e) => setNote(e.target.value)}
                       placeholder="e.g. Month-end count, counted with the store keeper" />
              </Field>
            </div>
            <Button onClick={() => post.mutate()} disabled={filled.length === 0 || post.isPending}>
              {post.isPending ? "Posting…" : `Post count (${filled.length} line${filled.length === 1 ? "" : "s"})`}
            </Button>
          </div>
          <p className="text-sm text-slate-600">
            Every line you fill in is recorded, and each one that differs posts an adjustment
            against that batch at {sheet.location.name}. Lines left blank are simply not counted.
          </p>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- movement log */

export function MovementLog({ locations, lockedLocation }) {
  const [locationId, setLocationId] = useState("");
  const [reason, setReason] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const active = lockedLocation ? lockedLocation.id : locationId;
  const categories = useItemCategories();

  const { data: movements, isLoading } = useQuery({
    queryKey: ["stock-movements", active || "all", reason || "all", categoryId || "all"],
    queryFn: () => api.get("/stock-movements/", {
      params: {
        page_size: 200,
        ...(active ? { location: active } : {}),
        ...(reason ? { reason } : {}),
        ...(categoryId ? { batch__item__category: categoryId } : {}),
      },
    }).then((r) => r.data.results ?? r.data),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        {!lockedLocation && (
          <div className="w-56">
            <Field label="Location">
              <Select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
                <option value="">Every location</option>
                {(locations ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </Select>
            </Field>
          </div>
        )}
        <div className="w-56">
          <Field label="Kind">
            <Select value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="">Everything</option>
              {Object.entries(REASON_LABEL).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="w-56">
          <Field label="Category">
            <Select aria-label="Category" value={categoryId}
                    onChange={(e) => setCategoryId(e.target.value)}>
              <option value="">Every category</option>
              {(categories.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          </Field>
        </div>
      </div>

      {isLoading && <p className="text-slate-600">Loading…</p>}
      {!isLoading && !movements?.length && (
        <p className="text-sm text-slate-600">No stock movements match that.</p>
      )}

      {movements?.length > 0 && (
        <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
          {movements.map((m) => (
            <div key={m.id} className="flex items-center justify-between gap-4 px-4 py-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium text-slate-900">
                  {m.item_name}
                  <span className="font-normal text-slate-600"> · batch {m.batch_no}</span>
                  {m.item_category_name && (
                    <span className="font-normal text-slate-600"> · {m.item_category_name}</span>
                  )}
                </p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-slate-600">
                  <Badge tone={REASON_TONE[m.reason] ?? "neutral"}>
                    {REASON_LABEL[m.reason] ?? m.reason}
                  </Badge>
                  <span className="font-medium text-slate-700">{m.location_name}</span>
                  <span>{new Date(m.created_at).toLocaleString()}</span>
                  <span>{m.performed_by_name}</span>
                  {m.transfer_reference && <span>{m.transfer_reference}</span>}
                </p>
              </div>
              <span className={`shrink-0 font-semibold tabular-nums ${
                m.change < 0 ? "text-red-700" : "text-emerald-700"}`}>
                {m.change > 0 ? "+" : ""}{m.change}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function locationName(locations, id) {
  return (locations ?? []).find((l) => String(l.id) === String(id))?.name;
}

function daysUntil(dateStr) {
  return Math.ceil((new Date(dateStr) - new Date()) / (1000 * 60 * 60 * 24));
}

/* ------------------------------------------------ CSV count: export / import */

/** Hand the browser a file the server produced — the download half of the CSV count. */
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const IMPORT_STATUS = {
  previewed: ["Preview — nothing applied yet", "warning"],
  applied: ["Applied", "success"],
  discarded: ["Discarded", "neutral"],
};

/**
 * Stock-taking as a spreadsheet, on top of the count the inventory already has:
 *
 *   download the count sheet → count the shelves → fill in "Counted Qty" →
 *   upload → read the preview → confirm.
 *
 * Nothing moves until a person confirms a clean preview, and confirming posts
 * ordinary count documents (`inventory/count_csv.py`) — every difference is an
 * adjustment in the movement log, never an overwrite. `lockedLocation` pins the
 * export to that shelf like every other panel here; the server refuses any row
 * the person may not count, whatever the screen sends.
 */
export function CountImportExport({ locations, lockedLocation }) {
  const { showToast } = useToast();
  const refresh = useRefresh();
  const queryClient = useQueryClient();
  const [locationId, setLocationId] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [includeEmpty, setIncludeEmpty] = useState(false);
  const [file, setFile] = useState(null);
  const [fileKey, setFileKey] = useState(0);
  const [preview, setPreview] = useState(null);
  const [confirming, setConfirming] = useState(false);

  const categories = useItemCategories();
  const history = useQuery({
    queryKey: ["stock-count-imports"],
    queryFn: () => api.get("/stock-count-imports/", { params: { page_size: 10 } })
      .then((r) => r.data.results ?? r.data),
  });
  const recordImport = (data) => {
    setPreview(data);
    queryClient.invalidateQueries({ queryKey: ["stock-count-imports"] });
  };

  const download = useMutation({
    mutationFn: () => api.get("/stock-counts/export/", {
      params: {
        ...(lockedLocation ? { location: lockedLocation.id } : locationId ? { location: locationId } : {}),
        ...(categoryId ? { category: categoryId } : {}),
        ...(includeEmpty ? { include_empty: 1 } : {}),
      },
      responseType: "blob",
    }),
    onSuccess: (response) => {
      const disposition = response.headers?.["content-disposition"] ?? "";
      const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? "stock-count.csv";
      saveBlob(response.data, name);
      showToast({ title: "Count sheet downloaded",
                  message: "Fill in Counted Qty for what you count, then upload it here." });
    },
    onError: (error) => showToast({
      tone: "error", title: "Could not export the count sheet", message: readError(error, "Please try again."),
    }),
  });

  const upload = useMutation({
    mutationFn: () => {
      const body = new FormData();
      body.append("file", file);
      return api.post("/stock-count-imports/", body, { headers: { "Content-Type": "multipart/form-data" } });
    },
    onSuccess: (response) => recordImport(response.data),
    onError: (error) => showToast({
      tone: "error", title: "Could not read the file", message: readError(error, "Please try again."),
    }),
  });

  const apply = useMutation({
    mutationFn: () => api.post(`/stock-count-imports/${preview.id}/apply/`),
    onSuccess: (response) => {
      setConfirming(false);
      recordImport(response.data);
      refresh();
      const s = response.data.summary ?? {};
      showToast({
        title: `Stock count ${response.data.reference} applied`,
        message: `${(s.increase_lines ?? 0) + (s.decrease_lines ?? 0)} adjustment(s) posted: +${s.units_added ?? 0} / −${s.units_removed ?? 0} units.`,
      });
    },
    onError: (error) => {
      setConfirming(false);
      showToast({ tone: "error", title: "Nothing was applied", message: readError(error, "Please try again.") });
    },
  });

  const discard = useMutation({
    mutationFn: () => api.post(`/stock-count-imports/${preview.id}/discard/`),
    onSuccess: (response) => {
      recordImport(null);
      setFile(null);
      setFileKey((key) => key + 1);
      showToast({ title: `${response.data.reference} discarded`, message: "Nothing was applied." });
    },
    onError: (error) => showToast({
      tone: "error", title: "Could not discard", message: readError(error, "Please try again."),
    }),
  });

  return (
    <div className="space-y-5">
      <div className="grid min-w-0 gap-4 lg:grid-cols-2">
        <section aria-labelledby="count-export-title" className="min-w-0 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <h2 id="count-export-title" className="font-semibold text-slate-900">1. Download the count sheet</h2>
          <p className="mt-1 text-sm text-slate-600">
            One row per batch per location with what the system holds. Fill in <strong>Counted Qty</strong> for
            each line you count and leave a line blank to leave it alone. Do not edit the other columns.
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {lockedLocation ? (
              <Field label="Location">
                <Input aria-label="Location" value={lockedLocation.name} readOnly disabled />
              </Field>
            ) : (
              <Field label="Location">
                <Select aria-label="Location" value={locationId} onChange={(e) => setLocationId(e.target.value)}>
                  <option value="">Every location</option>
                  {(locations ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </Select>
              </Field>
            )}
            <Field label="Category">
              <Select aria-label="Category" value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
                <option value="">Every category</option>
                {(categories.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </Field>
          </div>
          <label className="mt-2 flex min-h-[44px] items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" className="h-4 w-4" checked={includeEmpty}
                   onChange={(e) => setIncludeEmpty(e.target.checked)} />
            Include lines standing at zero
          </label>
          <Button className="mt-2" onClick={() => download.mutate()} disabled={download.isPending}>
            {download.isPending ? "Preparing…" : "Download CSV"}
          </Button>
        </section>

        <section aria-labelledby="count-import-title" className="min-w-0 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <h2 id="count-import-title" className="font-semibold text-slate-900">2. Upload the counted sheet</h2>
          <p className="mt-1 text-sm text-slate-600">
            Uploading only checks the file. You see every line it would change — and every problem — before
            anything is applied, and a file with a single problem applies nothing.
          </p>
          <div className="mt-4">
            <Field label="Counted CSV file" hint="If your spreadsheet asks, save as “CSV UTF-8”.">
              <input
                key={fileKey} type="file" accept=".csv,text/csv" aria-label="Counted CSV file"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full min-w-0 text-base text-slate-700 file:mr-3 file:min-h-[44px] file:rounded-lg file:border-0 file:bg-brand-50 file:px-4 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
              />
            </Field>
          </div>
          <Button className="mt-3" onClick={() => upload.mutate()} disabled={!file || upload.isPending}>
            {upload.isPending ? "Checking the file…" : "Upload and preview"}
          </Button>
        </section>
      </div>

      {preview && (
        <ImportPreview
          preview={preview} busy={apply.isPending || discard.isPending}
          onApply={() => setConfirming(true)} onDiscard={() => discard.mutate()}
        />
      )}

      <ImportHistory history={history} current={preview?.id} onOpen={setPreview} />

      {confirming && preview && (
        <Modal
          open onClose={() => setConfirming(false)} title={`Apply stock count ${preview.reference}?`}
          description="Each line that differs posts an adjustment against that batch, in that location. It is undone only by another count."
          footer={
            <>
              <Button variant="secondary" onClick={() => setConfirming(false)}>Cancel</Button>
              <Button onClick={() => apply.mutate()} disabled={apply.isPending}>
                {apply.isPending ? "Applying…" : "Apply count"}
              </Button>
            </>
          }
        >
          <ul className="space-y-1.5 text-sm text-slate-800">
            <li><strong>{preview.summary?.increase_lines ?? 0}</strong> line(s) go up by <strong>{preview.summary?.units_added ?? 0}</strong> unit(s)</li>
            <li><strong>{preview.summary?.decrease_lines ?? 0}</strong> line(s) go down by <strong>{preview.summary?.units_removed ?? 0}</strong> unit(s)</li>
            <li><strong>{preview.summary?.unchanged ?? 0}</strong> counted line(s) already match</li>
            <li>Locations: {(preview.summary?.locations ?? []).join(", ") || "—"}</li>
            <li>Categories: {(preview.summary?.categories ?? []).join(", ") || "—"}</li>
          </ul>
        </Modal>
      )}
    </div>
  );
}

function ImportPreview({ preview, busy, onApply, onDiscard }) {
  const [onlyChanges, setOnlyChanges] = useState(false);
  const summary = preview.summary ?? {};
  const errors = preview.errors ?? [];
  const rows = (preview.rows ?? []).filter((row) => !onlyChanges || row.difference !== 0);
  const unknownCategories = summary.unknown_categories ?? [];
  const [statusLabel, statusTone] = IMPORT_STATUS[preview.status] ?? [preview.status, "neutral"];
  const figures = [
    ["Rows read", summary.rows_read ?? 0, "text-slate-900"],
    ["Counted", summary.counted ?? 0, "text-slate-900"],
    ["Left blank", summary.not_counted ?? 0, "text-slate-900"],
    ["Unchanged", summary.unchanged ?? 0, "text-slate-900"],
    [`Increases (${summary.increase_lines ?? 0} lines)`, `+${summary.units_added ?? 0}`, "text-emerald-700"],
    [`Decreases (${summary.decrease_lines ?? 0} lines)`, `−${summary.units_removed ?? 0}`, "text-amber-800"],
  ];

  return (
    <section aria-label="Import preview" className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 p-4 sm:p-5">
        <div className="min-w-0">
          <h2 className="truncate font-semibold text-slate-900">{preview.reference} · {preview.filename || "count.csv"}</h2>
          <p className="text-sm text-slate-600">
            Uploaded by {preview.uploaded_by_name} · {new Date(preview.created_at).toLocaleString()}
            {preview.applied_at && ` · applied by ${preview.applied_by_name}, ${new Date(preview.applied_at).toLocaleString()}`}
          </p>
        </div>
        <Badge tone={statusTone}>{statusLabel}</Badge>
      </div>

      <dl className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 sm:p-5 xl:grid-cols-6">
        {figures.map(([label, value, tone]) => (
          <div key={label} className="min-w-0 rounded-lg bg-slate-50 px-3 py-2">
            <dt className="truncate text-xs text-slate-600">{label}</dt>
            <dd className={`mt-0.5 text-lg font-semibold tabular-nums ${tone}`}>{value}</dd>
          </div>
        ))}
      </dl>

      {unknownCategories.length > 0 && (
        <div className="px-4 pb-1 sm:px-5">
          <Alert tone="warning"
                 title={`${unknownCategories.length} category name${unknownCategories.length === 1 ? " was" : "s were"} not recognised`}>
            {unknownCategories.join(", ")} — importing a count never creates a category, so nothing
            was added. Correct the spelling in the sheet, or add the category under Administration →
            Product categories first.
          </Alert>
        </div>
      )}

      {errors.length > 0 && (
        <div className="space-y-3 px-4 pb-4 sm:px-5">
          <Alert tone="warning" title={`${errors.length} problem${errors.length === 1 ? "" : "s"} — nothing can be applied from this file`}>
            Correct the spreadsheet and upload it again. Rows are numbered the way your spreadsheet numbers them.
          </Alert>
          <TableWrap>
            <Table className="min-w-[36rem]">
              <THead><Tr><Th>Row</Th><Th>Column</Th><Th>Problem</Th></Tr></THead>
              <tbody>
                {errors.map((error, index) => (
                  <Tr key={`${error.row}-${error.column}-${index}`}>
                    <Td className="tabular-nums">{error.row ?? "File"}</Td>
                    <Td>{error.column ?? "—"}</Td>
                    <Td className="text-slate-800">{error.message}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrap>
        </div>
      )}

      {(preview.rows ?? []).length > 0 && (
        <div className="space-y-2 px-4 pb-4 sm:px-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-medium text-slate-900">Valid counted lines</h3>
            <label className="flex min-h-[44px] items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" className="h-4 w-4" checked={onlyChanges}
                     onChange={(e) => setOnlyChanges(e.target.checked)} />
              Only lines that change
            </label>
          </div>
          <TableWrap>
            <Table className="min-w-[44rem]">
              <THead>
                <Tr>
                  <Th>Row</Th><Th>Product</Th><Th>Category</Th><Th>Batch</Th><Th>Location</Th>
                  <Th className="text-right">System</Th><Th className="text-right">Counted</Th>
                  <Th className="text-right">Difference</Th>
                </Tr>
              </THead>
              <tbody>
                {rows.map((row) => (
                  <Tr key={row.line}>
                    <Td className="tabular-nums text-slate-600">{row.row}</Td>
                    <Td className="font-medium text-slate-900">
                      {row.item_name}
                      {row.sku && <span className="block text-xs font-normal text-slate-500">{row.sku}</span>}
                    </Td>
                    <Td className="text-slate-700">{row.category_name || "—"}</Td>
                    <Td>{row.batch_no}<span className="block text-xs text-slate-500">exp {row.expiry}</span></Td>
                    <Td>{row.location_name}</Td>
                    <Td className="text-right tabular-nums">{row.system_quantity}</Td>
                    <Td className="text-right tabular-nums">{row.counted_quantity}</Td>
                    <Td className={`text-right font-medium tabular-nums ${
                      row.difference === 0 ? "text-slate-500" : row.difference > 0 ? "text-emerald-700" : "text-amber-800"}`}>
                      {row.difference > 0 ? `+${row.difference}` : row.difference}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrap>
        </div>
      )}

      {preview.status === "applied" && (preview.counts ?? []).length > 0 && (
        <p className="border-t border-slate-100 px-4 py-3 text-sm text-slate-700 sm:px-5">
          Posted as {preview.counts.map((count) => count.reference).join(", ")} — the adjustments are in the movement log.
        </p>
      )}

      {preview.status === "previewed" && (
        <div className="flex flex-wrap justify-end gap-2 border-t border-slate-200 p-4 sm:px-5">
          <Button variant="secondary" onClick={onDiscard} disabled={busy}>Discard</Button>
          <Button onClick={onApply} disabled={busy || !preview.is_applicable}>Apply count…</Button>
        </div>
      )}
    </section>
  );
}

function ImportHistory({ history, current, onOpen }) {
  if (history.isLoading) return <Skeleton className="h-24" />;
  if (history.isError) return <ErrorState title="Could not load earlier imports." onRetry={history.refetch} />;
  const rows = history.data ?? [];
  if (!rows.length) return null;
  return (
    <section aria-labelledby="count-history-title" className="min-w-0">
      <h2 id="count-history-title" className="mb-2 font-semibold text-slate-900">Recent imports</h2>
      <TableWrap>
        <Table className="min-w-[40rem]">
          <THead>
            <Tr>
              <Th>Import</Th><Th>File</Th><Th>Status</Th><Th>Uploaded</Th>
              <Th className="text-right">Counted</Th><Th className="text-right">Problems</Th><Th />
            </Tr>
          </THead>
          <tbody>
            {rows.map((row) => {
              const [label, tone] = IMPORT_STATUS[row.status] ?? [row.status, "neutral"];
              return (
                <Tr key={row.id} className={row.id === current ? "bg-brand-50/60" : ""}>
                  <Td className="font-medium text-slate-900">{row.reference}</Td>
                  <Td className="max-w-[14rem] truncate">{row.filename || "—"}</Td>
                  <Td><Badge tone={tone}>{label}</Badge></Td>
                  <Td>{row.uploaded_by_name}<span className="block text-xs text-slate-500">{new Date(row.created_at).toLocaleString()}</span></Td>
                  <Td className="text-right tabular-nums">{row.summary?.counted ?? 0}</Td>
                  <Td className="text-right tabular-nums">{(row.errors ?? []).length}</Td>
                  <Td className="text-right">
                    <Button variant="link" size="xs" onClick={() => onOpen(row)}>Open</Button>
                  </Td>
                </Tr>
              );
            })}
          </tbody>
        </Table>
      </TableWrap>
    </section>
  );
}
