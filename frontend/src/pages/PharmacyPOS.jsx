import { useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import { useAuth } from "../auth/AuthContext.jsx";
import PatientPicker from "../components/PatientPicker.jsx";
import { PosReceiptSheet } from "../components/DepartmentDocuments.jsx";
import PosRegisterSummary from "../components/PosRegisterSummary.jsx";
import { productLabel } from "../components/prescriptionDirections.js";
import { canDiscount, judge, judgeCart, offeredTypes, readPolicy } from "../components/posDiscountPolicy.js";
import { useToast } from "../components/Toaster.jsx";
import { Icon } from "../components/icons.jsx";
import {
  Alert, Badge, Button, ErrorState, Field, IconButton, Input, MetaStat, Modal, Page, PageHeader,
  Select, Skeleton, Textarea, naira,
} from "../components/ui.jsx";

// The pharmacy's walk-in till.
//
// A second pharmacy *workflow*, never a second pharmacy. The products are the
// catalogue the doctor prescribes from, the stock is the dispensing shelf, and
// completing a sale runs the same FEFO rule dispensing uses and records an
// ordinary Payment. Prescription dispensing stays on /pharmacy, untouched.
//
// What the screen decides nothing about: prices (each batch's own, at
// completion), who may discount (the server refuses a pharmacist), what is on
// the shelf (re-checked under lock). It is fast because the server is strict —
// every figure below is a preview of what `sales/services.py` will charge.

export const PAYMENT_METHODS = [
  ["cash", "Cash"], ["card", "Card / POS terminal"], ["transfer", "Bank transfer"],
  ["insurance", "Insurance"],
];

const money = (value) => naira(Number(value ?? 0));
const round2 = (value) => Math.round(Number(value) * 100) / 100;

export function newToken() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

/** The discount a request would give on this amount — a preview; the server decides. */
export function discountOf(base, discount) {
  if (!discount) return 0;
  const value = Number(discount.value || 0);
  if (!(value > 0)) return 0;
  const amount = discount.type === "percent" ? Math.round(base * value) / 100 : value;
  return round2(Math.min(amount, Math.max(base, 0)));
}

/** A line's own discount, off that line's gross. */
export function lineDiscountOf(line) {
  return discountOf((line.price ?? 0) * line.quantity, line.discount);
}

/**
 * The cart's figures as the server will work them out: each line's own
 * discount comes off that line, a sale-wide discount comes off what is left,
 * and the sale can never be discounted to nothing.
 */
export function cartTotals(cart, saleDiscount) {
  const subtotal = round2(cart.reduce((sum, line) => sum + (line.price ?? 0) * line.quantity, 0));
  const lineDiscounts = round2(cart.reduce((sum, line) => sum + lineDiscountOf(line), 0));
  const onSale = discountOf(subtotal - lineDiscounts, saleDiscount);
  const totalDiscount = round2(lineDiscounts + onSale);
  const overLine = cart.filter((line) => line.discount?.type === "amount"
    && Number(line.discount.value) > (line.price ?? 0) * line.quantity);
  return {
    subtotal, lineDiscounts, saleDiscount: onSale, totalDiscount,
    total: Math.max(round2(subtotal - totalDiscount), 0),
    coversEverything: subtotal > 0 && totalDiscount >= subtotal,
    overLine,
    hasDiscount: Boolean(saleDiscount) || cart.some((line) => line.discount),
  };
}

export default function PharmacyPOS() {
  const { user } = useAuth();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["pos-register"],
    queryFn: () => api.get("/pos-registers/current/").then((r) => r.data),
  });

  if (isLoading) {
    return (
      <Page width="wide">
        <PageHeader icon="cash" title="Pharmacy POS" />
        <Skeleton className="h-64" />
      </Page>
    );
  }
  if (isError) {
    return (
      <Page width="wide">
        <PageHeader icon="cash" title="Pharmacy POS" />
        <ErrorState title="Could not reach the till." onRetry={refetch} />
      </Page>
    );
  }
  if (!data?.register) return <OpenRegister />;
  return <Till register={data.register} summary={data.summary} user={user} />;
}

/* ---------------------------------------------------------- open register */

function OpenRegister() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [opening, setOpening] = useState("");
  const open = useMutation({
    mutationFn: () => api.post("/pos-registers/open/", { opening_float: opening || 0 }),
    onSuccess: (response) => {
      queryClient.setQueryData(["pos-register"], response.data);
      showToast({ title: `Register ${response.data.register.reference} opened` });
    },
    onError: (error) => showToast({
      tone: "error", title: "Could not open the register", message: readError(error, "Please try again."),
    }),
  });

  return (
    <Page width="wide">
      <PageHeader
        icon="cash" title="Pharmacy POS"
        subtitle="Walk-in and registered-patient sales from the pharmacy shelf."
      />
      <section className="mx-auto max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <h2 className="text-lg font-semibold text-slate-900">Open your register</h2>
        <p className="mt-1 text-sm text-slate-600">
          Count the float in the drawer before your first sale. Every sale and return on this till is
          reconciled against it when you close.
        </p>
        <form
          className="mt-4 space-y-4"
          onSubmit={(event) => { event.preventDefault(); open.mutate(); }}
        >
          <Field label="Opening float (cash in the drawer)">
            <Input
              type="number" min="0" step="0.01" inputMode="decimal" placeholder="0.00"
              value={opening} onChange={(event) => setOpening(event.target.value)}
            />
          </Field>
          <Button type="submit" block disabled={open.isPending}>
            {open.isPending ? "Opening…" : "Open register"}
          </Button>
        </form>
      </section>
    </Page>
  );
}

/* ------------------------------------------------------------------- till */

export function Till({ register, summary, user }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const searchRef = useRef(null);

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [cart, setCart] = useState([]);
  const [customerType, setCustomerType] = useState("walk_in");
  const [patient, setPatient] = useState(null);
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [discount, setDiscount] = useState(null);             // sale-wide {type, value}
  const [discountReason, setDiscountReason] = useState("");   // one reason for every discount
  // Which lines "Discount selected" will act on. Selection is a way of
  // *reaching* the dialog — the discount it applies is still one per line, so
  // the cart the server receives is the same shape it has always been.
  const [selected, setSelected] = useState([]);

  const [heldSaleId, setHeldSaleId] = useState(null);
  const [dialog, setDialog] = useState(null);                 // "discount" | "pay" | … | {line}
  const [receipt, setReceipt] = useState(null);
  // What was being paid when the server asked for an authorisation, so the
  // approved retry is the same payment rather than a retyped one.
  const [lastPayment, setLastPayment] = useState(null);
  // One token per cart: a double-tapped "Complete sale" sends it twice and the
  // server completes the sale once.
  const [token, setToken] = useState(newToken);

  const mayDiscount = canDiscount(user);
  // The same query key `HospitalProvider` uses: the policy comes back with the
  // hospital's own settings, which the shell has already fetched, so the till
  // pays nothing for it. The server enforces every one of these rules again.
  const settings = useQuery({
    queryKey: ["hospital-settings"],
    queryFn: () => api.get("/hospital-settings/current/").then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });
  const policy = useMemo(() => readPolicy(settings.data), [settings.data]);

  const products = useQuery({
    queryKey: ["pos-products", search.trim(), category],
    queryFn: () => api.get("/sales/products/", {
      params: { ...(search.trim() ? { search: search.trim() } : {}), ...(category ? { category } : {}) },
    }).then((r) => r.data),
    placeholderData: keepPreviousData,
  });
  // The chips travel with the product search itself, and the server builds
  // them from the whole catalogue rather than from the sixty rows beside them
  // — so picking a category does not collapse the strip to the chip you
  // picked, and a category whose drugs sort past the sixtieth result is still
  // offered. `keepPreviousData` above is what holds the strip steady while a
  // filtered search is in flight.
  const categories = useMemo(
    () => (products.data?.categories ?? []).map((row) => [String(row.id), row.name]),
    [products.data]);
  const results = products.data?.results ?? [];

  function add(product) {
    if (!product.price || product.available <= 0) {
      showToast({
        tone: "error", title: `${product.name} is not on the pharmacy shelf`,
        message: "Transfer stock to the pharmacy before selling it.",
      });
      return;
    }
    setCart((lines) => {
      const found = lines.find((line) => line.item === product.id);
      if (found) {
        return lines.map((line) => (line.item === product.id
          ? { ...line, quantity: Math.min(line.quantity + 1, product.available) } : line));
      }
      return [...lines, {
        item: product.id, name: product.name, label: productLabel(product), sku: product.sku,
        unit_label: product.unit_label, price: Number(product.price), available: product.available,
        quantity: 1, discount: null,
      }];
    });
  }

  function setQuantity(item, value) {
    setCart((lines) => lines.map((line) => {
      if (line.item !== item) return line;
      const wanted = Math.max(1, Math.floor(Number(value) || 1));
      return { ...line, quantity: line.available ? Math.min(wanted, line.available) : wanted };
    }));
  }

  function setLineDiscount(item, value) {
    setCart((lines) => lines.map((line) => (line.item === item ? { ...line, discount: value } : line)));
  }

  function reset() {
    setCart([]); setDiscount(null); setDiscountReason(""); setHeldSaleId(null);
    setSelected([]);
    setCustomerType("walk_in"); setPatient(null); setCustomerName(""); setCustomerPhone("");
  }

  function setLinesDiscount(items, value) {
    const wanted = new Set(items);
    setCart((lines) => lines.map((line) => (wanted.has(line.item) ? { ...line, discount: value } : line)));
  }

  function toggleSelected(item) {
    setSelected((items) => (items.includes(item) ? items.filter((id) => id !== item) : [...items, item]));
  }

  const totals = cartTotals(cart, discount);
  // What the policy makes of this cart — judged line by line on the *amount*,
  // the way the server will. `needsApproval` is what puts the authorisation
  // box on the pay dialog; `refused` is what no supervisor can clear.
  const verdict = useMemo(() => judgeCart({
    policy,
    lines: cart.map((line) => ({
      type: line.discount?.type, amount: lineDiscountOf(line),
      base: (line.price ?? 0) * line.quantity, label: line.label || line.name,
    })),
    saleDiscount: discount ? { type: discount.type, amount: totals.saleDiscount } : null,
    saleBase: round2(totals.subtotal - totals.lineDiscounts),
  }), [policy, cart, discount, totals.saleDiscount, totals.subtotal, totals.lineDiscounts]);
  const customerReady = customerType === "walk_in" || Boolean(patient);
  const needsReason = totals.hasDiscount && !discountReason.trim();
  const discountProblem = totals.coversEverything
    ? "Discounts cannot cover the whole sale."
    : totals.overLine.length
      ? `A discount on ${totals.overLine[0].name} is more than the line.`
      : verdict.refused ? verdict.refused.message
        : needsReason ? "Give a reason for the discount." : null;

  // A scanner types the code and presses Enter faster than a search can land,
  // so Enter asks the server directly for an exact SKU or barcode.
  async function onSearchKey(event) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const term = search.trim();
    if (!term) return;
    try {
      const { data } = await api.get("/sales/products/", { params: { search: term } });
      const match = data.results.find((product) => product.id === data.exact_match)
        ?? (data.results.length === 1 ? data.results[0] : null);
      if (match) {
        add(match);
        setSearch("");
      } else if (!data.results.length) {
        showToast({ tone: "error", title: `Nothing matches “${term}”` });
      }
    } catch (error) {
      showToast({ tone: "error", title: "Search failed", message: readError(error, "Please try again.") });
    }
  }

  function payload(extra = {}) {
    return {
      lines: cart.map(({ item, quantity, discount: lineDiscount }) => ({
        item, quantity, ...(lineDiscount ? { discount: lineDiscount } : {}),
      })),
      customer_type: customerType,
      patient: customerType === "patient" ? patient?.id ?? null : null,
      customer_name: customerType === "walk_in" ? customerName : "",
      customer_phone: customerType === "walk_in" ? customerPhone : "",
      held_sale: heldSaleId,
      ...extra,
    };
  }

  function refresh() {
    for (const key of ["pos-register", "pos-products", "pos-held", "pos-sales", "pos-summary"]) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  }

  const hold = useMutation({
    mutationFn: () => api.post("/sales/hold/", payload({
      lines: cart.map(({ item, quantity }) => ({ item, quantity })),
    })),
    onSuccess: (response) => {
      showToast({ title: `Held as ${response.data.reference}`,
                  message: "Nothing was charged and no stock moved. Discounts are applied when it is completed." });
      reset();
      refresh();
    },
    onError: (error) => showToast({
      tone: "error", title: "Could not hold the sale", message: readError(error, "Please try again."),
    }),
  });

  const complete = useMutation({
    // `authorization` travels in the mutation's own variables, never from
    // state: an approval is granted and submitted in the same tick, and a
    // `setState` is not visible to a closure called beside it.
    mutationFn: ({ method, tendered, authorization }) => api.post("/sales/complete/", payload({
      payment_method: method,
      amount_tendered: method === "cash" && tendered !== "" ? tendered : null,
      discount: discount ? { type: discount.type, value: discount.value, reason: discountReason } : null,
      discount_reason: discountReason,
      // One authorisation covers the whole receipt. Sent only when the policy
      // asked for one, and held nowhere afterwards.
      ...(authorization ? { authorization } : {}),   // from the mutation's variables
      client_token: token,
    })),
    onSuccess: (response) => {
      const sale = response.data;
      setReceipt(sale);
      setDialog(null);
      reset();
      setToken(newToken());
      refresh();
      showToast({
        title: `Sale ${sale.reference} completed`,
        message: `${money(sale.total_amount)} by ${sale.payment_method_label}`
          + (Number(sale.change_due) > 0 ? ` · change ${money(sale.change_due)}` : ""),
      });
    },
    onError: (error) => {
      // The server is what decides, and it may refuse a discount the screen
      // thought was fine — a policy changed since the page loaded, or a price
      // that made a fixed sum a bigger percentage than it looked. When it asks
      // for an authorisation, ask for one rather than just saying no.
      const code = error.response?.data?.code;
      if (code === "discount_needs_approval" || code === "discount_approval_failed") {
        setDialog({ authorize: readError(error, "This discount has to be authorised.") });
        return;
      }
      showToast({
        tone: "error", title: "Sale not completed",
        message: readError(error, "Nothing was charged and no stock moved. Please try again."),
      });
    },
  });

  async function resume(sale) {
    try {
      const ids = sale.lines.map((line) => line.item).join(",");
      const { data } = await api.get("/sales/products/", { params: { ids } });
      const byId = new Map(data.results.map((product) => [product.id, product]));
      setCart(sale.lines.map((line) => {
        const product = byId.get(line.item) ?? {};
        return {
          item: line.item, name: line.item_name, label: productLabel(product, line.item_name), sku: line.sku,
          unit_label: line.unit_label, price: product.price ? Number(product.price) : null,
          available: product.available ?? 0, quantity: line.quantity, discount: null,
        };
      }));
      setHeldSaleId(sale.id);
      setCustomerType(sale.customer_type);
      setPatient(sale.patient ? { id: sale.patient, display: sale.patient_name,
                                  patient_number: sale.patient_number } : null);
      setCustomerName(sale.customer_name ?? "");
      setCustomerPhone(sale.customer_phone ?? "");
      setDiscount(null);
      setDiscountReason("");
      setSelected([]);
      setDialog(null);
    } catch (error) {
      showToast({ tone: "error", title: "Could not resume the sale", message: readError(error, "Please try again.") });
    }
  }

  const selectedLines = dialog?.lines
    ? cart.filter((line) => dialog.lines.includes(line.item)) : [];
  // Editing one line's discount pre-fills the dialog with what it already has.
  // With several selected there is no single "current" to show, so the dialog
  // opens empty and applies one decision to all of them.
  const editing = selectedLines.length === 1 ? selectedLines[0].discount : null;
  // Who the discount is for, shown at the top of the dialog so a cashier can
  // see they are discounting the right person's basket.
  const customerSummary = customerType === "patient" && patient
    ? { name: patient.display, number: patient.patient_number }
    : { name: customerName.trim() || "Walk-in customer", number: "" };

  return (
    <Page width="wide">
      <PageHeader
        icon="cash"
        title="Pharmacy POS"
        subtitle="Walk-in and registered-patient sales from the pharmacy shelf — the same stock dispensing uses."
        meta={
          <>
            <MetaStat value={register.reference} label="register" />
            <MetaStat value={summary?.sales_count ?? 0} label="sales this session" tone="brand" />
            <MetaStat value={money(summary?.net_takings)} label="net takings" />
          </>
        }
        actions={
          <>
            <Button variant="secondary" onClick={() => setDialog("held")}>Held sales</Button>
            <Button variant="dangerOutline" onClick={() => setDialog("close")}>Close register</Button>
          </>
        }
      />

      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_26rem]">
        <section aria-label="Products" className="min-w-0 space-y-3">
          <div className="relative">
            <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-500" />
            <input
              ref={searchRef}
              autoFocus
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={onSearchKey}
              aria-label="Search products"
              placeholder="Search a product — or scan a barcode / type a SKU and press Enter"
              className="w-full rounded-xl border border-slate-300 bg-white py-3 pl-11 pr-4 text-base text-slate-900 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
            />
          </div>

          {categories.length > 1 && (
            <div className="scroll-rail -mx-1 flex gap-2 px-1 pb-1" role="group" aria-label="Categories">
              {[["", "All"], ...categories].map(([id, name]) => (
                <button
                  key={id || "all"} type="button" onClick={() => setCategory(id)}
                  aria-pressed={category === id}
                  className={`min-h-[44px] shrink-0 rounded-full px-4 text-sm font-medium transition sm:min-h-[40px] ${
                    category === id ? "bg-brand-600 text-white"
                      : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}
                >
                  {name}
                </button>
              ))}
            </div>
          )}

          {products.isLoading && <Skeleton className="h-40" />}
          {products.isError && <ErrorState title="Could not load products." onRetry={products.refetch} />}
          {!products.isLoading && !products.isError && results.length === 0 && (
            <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-slate-600">
              No product matches. Check the spelling, or scan the barcode.
            </p>
          )}
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
            {results.map((product) => {
              const sellable = Boolean(product.price) && product.available > 0;
              const detail = [product.strength, product.dosage_form].filter(Boolean).join(" · ");
              return (
                <li key={product.id} className="min-w-0">
                  <button
                    type="button" onClick={() => add(product)} disabled={!sellable}
                    className="flex h-full min-h-[112px] w-full flex-col justify-between rounded-xl border border-slate-200 bg-white p-3 text-left shadow-sm transition hover:border-brand-300 hover:shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span className="line-clamp-2 font-medium text-slate-900">{product.name}</span>
                    {detail && <span className="truncate text-sm text-slate-700">{detail}</span>}
                    <span className="mt-0.5 truncate text-xs text-slate-600">
                      {product.category_name || "\u00a0"}
                    </span>
                    {product.sku && (
                      <span className="truncate text-xs text-slate-500">{product.sku}</span>
                    )}
                    <span className="mt-2 flex items-end justify-between gap-2">
                      <span className="font-semibold tabular-nums text-brand-700">
                        {product.price ? money(product.price) : "No price"}
                      </span>
                      <span className={`text-xs font-medium ${product.available > 0 ? "text-emerald-700" : "text-red-700"}`}>
                        {product.available > 0 ? `${product.available} ${product.unit_label}` : "Out of stock"}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>

        <aside aria-label="Sale" className="min-w-0 lg:sticky lg:top-20 lg:self-start">
          <div className="flex flex-col rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 p-4">
              <div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1" role="group" aria-label="Customer">
                {[["walk_in", "Walk-in customer"], ["patient", "Registered patient"]].map(([value, label]) => (
                  <button
                    key={value} type="button" aria-pressed={customerType === value}
                    onClick={() => setCustomerType(value)}
                    className={`min-h-[44px] rounded-md px-2 text-sm font-medium transition sm:min-h-[40px] ${
                      customerType === value ? "bg-white text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="mt-3">
                {customerType === "patient" ? (
                  <PatientPicker value={patient} onChange={setPatient} placeholder="Find the registered patient…" />
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    <Input aria-label="Customer name (optional)" placeholder="Name (optional)"
                           value={customerName} onChange={(event) => setCustomerName(event.target.value)} />
                    <Input aria-label="Customer phone (optional)" placeholder="Phone (optional)" inputMode="tel"
                           value={customerPhone} onChange={(event) => setCustomerPhone(event.target.value)} />
                  </div>
                )}
              </div>
              {heldSaleId && (
                <p className="mt-2 text-xs font-medium text-amber-800">Resumed held sale — completing it uses today’s shelf.</p>
              )}
            </div>

            {cart.length === 0 ? (
              <p className="px-4 py-10 text-center text-sm text-slate-600">
                The cart is empty. Tap a product or scan a barcode.
              </p>
            ) : (
              <>
              {/* Odoo-style selection header: one tap selects or clears the
                  whole cart, and the count is always visible. It is here
                  rather than beside each line so "select all" does not have to
                  be hunted for. */}
              {mayDiscount && (
                <div className="flex items-center justify-between gap-2 border-t border-slate-200 bg-slate-50 px-4 py-2">
                  <label className="flex min-h-[36px] items-center gap-2 text-sm font-medium text-slate-700">
                    <input
                      type="checkbox"
                      checked={selected.length === cart.length && cart.length > 0}
                      ref={(node) => { if (node) node.indeterminate = selected.length > 0 && selected.length < cart.length; }}
                      onChange={() => setSelected(selected.length === cart.length
                        ? [] : cart.map((line) => line.item))}
                      aria-label="Select all items"
                      className="h-5 w-5 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                    />
                    Select all
                  </label>
                  <span className="text-sm text-slate-600" aria-live="polite">
                    {selected.length > 0
                      ? `${selected.length} of ${cart.length} selected`
                      : `${cart.length} item${cart.length === 1 ? "" : "s"}`}
                  </span>
                </div>
              )}
              <ul aria-label="Cart" className="max-h-[50vh] divide-y divide-slate-100 overflow-y-auto">
                {cart.map((line) => {
                  const gross = line.price !== null ? line.price * line.quantity : null;
                  const off = lineDiscountOf(line);
                  const net = gross === null ? null : round2(gross - off);
                  const isSelected = selected.includes(line.item);
                  return (
                    <li key={line.item}
                        className={`px-4 py-2.5 transition ${isSelected ? "bg-brand-50 ring-1 ring-inset ring-brand-200" : ""}`}>
                      <div className="flex items-center gap-2">
                        {/* Always offered, whatever the cart holds. It used to
                            appear only on a cart of two or more, so a cashier
                            with one item on the screen had no way to select it
                            and no way to reach the discount at all. */}
                        {mayDiscount && (
                          <input
                            type="checkbox" checked={isSelected}
                            onChange={() => toggleSelected(line.item)}
                            aria-label={`Select ${line.name}`}
                            className="h-5 w-5 shrink-0 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                          />
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium text-slate-900">{line.label || line.name}</p>
                          <p className="text-xs text-slate-600">
                            {line.price !== null ? money(line.price) : "Price at completion"} each
                          </p>
                        </div>
                        <div className="flex items-center gap-1">
                          <IconButton label={`One fewer ${line.name}`} variant="secondary"
                                      onClick={() => setQuantity(line.item, line.quantity - 1)}>
                            <span aria-hidden="true" className="text-lg leading-none">−</span>
                          </IconButton>
                          <input
                            aria-label={`Quantity of ${line.name}`} inputMode="numeric"
                            value={line.quantity}
                            onChange={(event) => setQuantity(line.item, event.target.value)}
                            className="h-11 w-12 rounded-lg border border-slate-300 text-center text-base tabular-nums sm:h-9"
                          />
                          <IconButton label={`One more ${line.name}`} variant="secondary"
                                      onClick={() => setQuantity(line.item, line.quantity + 1)}>
                            <Icon name="plus" className="h-4 w-4" />
                          </IconButton>
                        </div>
                        <p className="w-24 text-right text-sm tabular-nums">
                          {gross === null ? <span className="text-slate-900">—</span> : off > 0 ? (
                            <>
                              <span className="block text-xs text-slate-500 line-through">{money(gross)}</span>
                              <span className="block font-semibold text-slate-900">{money(net)}</span>
                            </>
                          ) : <span className="font-medium text-slate-900">{money(gross)}</span>}
                        </p>
                        <IconButton label={`Remove ${line.name}`}
                                    onClick={() => {
                                      setCart((lines) => lines.filter((l) => l.item !== line.item));
                                      // A line that has left the cart cannot stay
                                      // selected, or "Discount 2 items" would count
                                      // something that is no longer on the receipt.
                                      setSelected((items) => items.filter((id) => id !== line.item));
                                    }}>
                          <Icon name="close" className="h-4 w-4" />
                        </IconButton>
                      </div>
                      {line.discount && (
                        <div className="mt-1 flex items-center justify-between gap-2 text-sm">
                          <Badge tone="success">
                            {line.discount.type === "percent"
                              ? `${Number(line.discount.value)}% off` : "Discount"}
                            {" · "}<span className="tabular-nums">− {money(off)}</span>
                          </Badge>
                          {mayDiscount && (
                            <span className="flex items-center gap-1">
                              <Button variant="linkMuted" size="xs"
                                      onClick={() => setDialog({ lines: [line.item] })}>Edit</Button>
                              {/* Removing restores the line's own price; the
                                  original was never overwritten to begin with. */}
                              <Button variant="linkDanger" size="xs"
                                      onClick={() => setLineDiscount(line.item, null)}>Remove</Button>
                            </span>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
              </>
            )}

            <div className="space-y-1 border-t border-slate-200 p-4 text-sm">
              <p className="flex justify-between">
                <span className="text-slate-600">Subtotal</span>
                <span className="tabular-nums text-slate-900">{money(totals.subtotal)}</span>
              </p>
              {/* Always shown, at zero as well — the cashier should be able to
                  read why the total is what it is without working it out. */}
              <p className="flex justify-between">
                <span className="text-slate-600">Discount</span>
                <span className={`tabular-nums ${totals.totalDiscount > 0 ? "font-medium text-emerald-700" : "text-slate-900"}`}>
                  {totals.totalDiscount > 0 ? `− ${money(totals.totalDiscount)}` : money(0)}
                </span>
              </p>
              {totals.lineDiscounts > 0 && totals.saleDiscount > 0 && (
                <p className="flex justify-between text-xs text-slate-600">
                  <span>Item discounts {money(totals.lineDiscounts)} · whole sale {money(totals.saleDiscount)}</span>
                </p>
              )}
              {discount && (
                <p className="flex items-center justify-between gap-2">
                  <span className="text-slate-600">
                    Whole-sale discount {discount.type === "percent" ? `(${Number(discount.value)}%)` : ""}
                    <Button variant="linkDanger" size="xs" className="ml-1"
                            onClick={() => setDiscount(null)}>Remove</Button>
                  </span>
                </p>
              )}
              {totals.hasDiscount && (
                <p className="flex items-center justify-between gap-2 text-slate-700">
                  <span className="min-w-0 truncate">
                    Reason: {discountReason.trim() ? discountReason : <em className="text-amber-800">not given</em>}
                  </span>
                </p>
              )}
              <p className="flex justify-between border-t border-slate-200 pt-2 text-lg font-semibold">
                <span>Total</span><span className="tabular-nums">{money(totals.total)}</span>
              </p>
              {discountProblem && <p role="alert" className="text-sm font-medium text-amber-800">{discountProblem}</p>}
            </div>

            <div className="grid grid-cols-2 gap-2 p-4 pt-0">
              {/* The action the whole feature turns on, in the cart where the
                  cashier is standing — not a text link on a row, and not
                  behind the payment screen. It discounts the ticked lines. */}
              {mayDiscount && (
                <Button variant="soft" disabled={!cart.length || selected.length === 0}
                        onClick={() => setDialog({ lines: selected })}>
                  {selected.length > 0
                    ? `Discount ${selected.length} item${selected.length === 1 ? "" : "s"}`
                    : "Discount"}
                </Button>
              )}
              <Button variant="secondary" disabled={!cart.length || hold.isPending || !customerReady}
                      onClick={() => hold.mutate()}>
                Hold
              </Button>
              {mayDiscount && cart.length > 0 && selected.length === 0 && (
                <p className="col-span-2 -mt-1 text-xs text-slate-600">
                  Tick an item to discount it
                  {/* The whole-sale discount is a different thing the backend
                      has always supported, kept reachable without competing
                      with the per-item action beside it. */}
                  {" · "}
                  <Button variant="link" size="xs" onClick={() => setDialog("discount")}>
                    or discount the whole sale
                  </Button>
                </p>
              )}
              {!mayDiscount && cart.length > 0 && (
                <p className="col-span-2 -mt-1 text-xs text-slate-600">
                  Discounts are given by a cashier, an accountant, an administrator or staff
                  authorised for POS discounts.
                </p>
              )}
              <Button variant="ghost" disabled={!cart.length} onClick={reset}
                      className={mayDiscount ? "col-span-2" : ""}>
                Clear
              </Button>
              <Button size="lg" className="col-span-2"
                      disabled={!cart.length || !customerReady || complete.isPending || Boolean(discountProblem)}
                      onClick={() => setDialog("pay")}>
                {`Take Charge · ${money(totals.total)}`}
              </Button>
            </div>
          </div>
        </aside>
      </div>

      {dialog === "discount" && (
        <DiscountDialog
          policy={policy} customer={customerSummary}
          base={round2(totals.subtotal - totals.lineDiscounts)} current={discount} reason={discountReason}
          onClose={() => setDialog(null)}
          onApply={(value, reason) => { setDiscount(value); setDiscountReason(reason); setDialog(null); }}
        />
      )}
      {selectedLines.length > 0 && (
        <DiscountDialog
          policy={policy} customer={customerSummary}
          lines={selectedLines.map((line) => ({
            name: line.label || line.name,
            amount: round2((line.price ?? 0) * line.quantity),
          }))}
          base={round2(selectedLines.reduce((sum, line) => sum + (line.price ?? 0) * line.quantity, 0))}
          current={editing} reason={discountReason} allowFull
          title={selectedLines.length === 1
            ? `Discount ${selectedLines[0].label || selectedLines[0].name}`
            : `Discount ${selectedLines.length} selected items`}
          description={selectedLines.length === 1
            ? "Comes off this item only. The price on the shelf is not changed; the discount, its reason and your name are recorded on the sale."
            : "The same discount is applied to each selected item, off that item's own total. Shelf prices are not changed; the discount, its reason and your name are recorded on the sale."}
          onClose={() => setDialog(null)}
          onApply={(value, reason) => {
            setLinesDiscount(dialog.lines, value);
            setDiscountReason(reason);
            setSelected([]);
            setDialog(null);
          }}
        />
      )}
      {dialog?.authorize && (
        <AuthorizeDialog
          message={dialog.authorize} pending={complete.isPending}
          onClose={() => setDialog(null)}
          onAuthorize={(credentials) => {
            setDialog(null);
            // Straight back to the payment that was refused, now carrying the
            // authorisation — the cashier does not retype the tender.
            complete.mutate({ ...(lastPayment ?? { method: "cash", tendered: "" }),
                              authorization: credentials });
          }}
        />
      )}
      {dialog === "pay" && (
        <PayDialog total={totals.total} pending={complete.isPending}
                   needsApproval={verdict.needsApproval} approvalMessage={verdict.approvalMessage}
                   onClose={() => setDialog(null)}
                   onComplete={(values) => { setLastPayment(values); complete.mutate(values); }} />
      )}
      {dialog === "held" && <HeldSales onClose={() => setDialog(null)} onResume={resume} />}
      {dialog === "close" && <CloseRegister register={register} onClose={() => setDialog(null)} />}
      {receipt && (
        <PosReceiptSheet sale={receipt} onClose={() => { setReceipt(null); searchRef.current?.focus(); }} />
      )}
    </Page>
  );
}

/* --------------------------------------------------------------- dialogs */

/**
 * A discount on the whole sale, on one item, or on the items that were ticked.
 *
 * One component for the three because they are one decision — a type, a value
 * and a reason — and three copies of it would drift. What differs is only
 * what it comes off: `base` and the `items` it names.
 *
 * Everything it refuses, the server refuses again on the priced amount
 * (`sales/discount_policy.py`). What the dialog adds is telling the cashier
 * *before* they commit, and telling apart the two kinds of "no": one a
 * supervisor can clear, one nobody can.
 */
function DiscountDialog({ policy, customer, lines = [], base, current,
                          reason: currentReason, allowFull = false, title, description,
                          onClose, onApply }) {
  const types = offeredTypes(policy);
  const [type, setType] = useState(current?.type ?? types[0]?.[0] ?? "percent");
  const [value, setValue] = useState(current?.value ?? "");
  const [reason, setReason] = useState(currentReason ?? "");
  const typed = Number(value);
  const preview = discountOf(base, { type, value });
  const tooMuch = base > 0 && (type === "amount" ? typed > base : false);
  const coversSale = !allowFull && base > 0 && preview >= base;
  const subject = lines.length === 1 ? lines[0].name : lines.length ? "the selected items" : "this sale";
  // Judged on the amount, the way the server judges it — so a fixed sum is
  // measured as the percentage it really is.
  const ruling = judge({ policy, type, amount: preview, base, label: subject });
  const blocked = Boolean(ruling.code) && ruling.code !== "discount_needs_approval";
  const valid = typed > 0 && reason.trim() && !tooMuch && !coversSale && !blocked
    && (type !== "percent" || typed <= 100);

  return (
    <Modal
      open onClose={onClose}
      title={title ?? "Discount this sale"}
      description={description
        ?? "Comes off what is left after item discounts. Prices are not changed; the discount, its reason and your name are recorded on the sale."}
      footer={
        <>
          {current && (
            <Button variant="linkDanger" onClick={() => onApply(null, reason.trim())}>Remove discount</Button>
          )}
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button disabled={!valid} onClick={() => onApply({ type, value }, reason.trim())}>
            Apply discount
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <dl className="rounded-lg bg-slate-50 px-3 py-2 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-slate-600">Customer</dt>
            <dd className="min-w-0 truncate text-right font-medium text-slate-900">
              {customer?.name || "Walk-in customer"}
              {customer?.number && <span className="ml-1 font-normal text-slate-600">{customer.number}</span>}
            </dd>
          </div>
        </dl>

        {lines.length > 0 && (
          <div>
            <p className="mb-1 text-sm font-medium text-slate-700">
              {lines.length === 1 ? "Item" : `Selected items (${lines.length})`}
            </p>
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {lines.map((entry) => (
                <li key={entry.name} className="flex justify-between gap-3 px-3 py-1.5 text-sm">
                  <span className="min-w-0 truncate text-slate-800">{entry.name}</span>
                  <span className="shrink-0 tabular-nums text-slate-900">{money(entry.amount)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <dl className="text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-slate-600">
              {lines.length ? "Selected subtotal" : "Current subtotal"}
            </dt>
            <dd className="text-right font-medium tabular-nums text-slate-900">{money(base)}</dd>
          </div>
        </dl>

        {types.length > 1 ? (
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1" role="group" aria-label="Discount type">
            {types.map(([key, label]) => (
              <button key={key} type="button" aria-pressed={type === key} onClick={() => setType(key)}
                      className={`min-h-[44px] rounded-md text-sm font-medium sm:min-h-[40px] ${type === key ? "bg-white text-slate-900 shadow-sm" : "text-slate-600"}`}>
                {label}
              </button>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-700">
            {types[0]?.[1] ?? "No discount type"} — the only kind this till offers.
          </p>
        )}
        <Field label={type === "percent" ? "Percentage off" : "Amount off (₦)"}>
          <Input type="number" min="0" step="0.01" inputMode="decimal" value={value} autoFocus
                 onChange={(event) => setValue(event.target.value)} />
        </Field>
        {type === "percent" && policy.presets.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {/* The hospital's own presets, not five numbers written into this file. */}
            {policy.presets.map((percent) => (
              <Button key={percent} size="sm" variant="secondary"
                      onClick={() => setValue(String(percent))}>{percent}%</Button>
            ))}
          </div>
        )}
        <Field label="Reason" required hint="One reason covers every discount on this sale.">
          {policy.reasons.length > 0 && (
            <Select aria-label="Reason preset" className="mb-2" value=""
                    onChange={(event) => event.target.value && setReason(event.target.value)}>
              <option value="">Choose a reason…</option>
              {policy.reasons.map((preset) => <option key={preset} value={preset}>{preset}</option>)}
              <option value="" disabled>──────────</option>
            </Select>
          )}
          <Textarea rows={2} value={reason} onChange={(event) => setReason(event.target.value)}
                    placeholder="e.g. Staff purchase, loyal customer, damaged packaging" />
        </Field>

        <dl className="space-y-1 border-t border-slate-200 pt-3 text-sm">
          <div className="flex justify-between">
            <dt className="text-slate-600">Discount amount</dt>
            <dd className="tabular-nums font-medium text-slate-900">− {money(preview)}</dd>
          </div>
          <div className="flex justify-between text-base">
            <dt className="font-medium text-slate-900">New total</dt>
            <dd className="font-semibold tabular-nums text-slate-900">
              {money(Math.max(base - preview, 0))}
            </dd>
          </div>
        </dl>

        {tooMuch && <Alert tone="warning">A discount cannot be more than {money(base)}.</Alert>}
        {coversSale && <Alert tone="warning">A discount cannot cover the whole sale — something has to be paid.</Alert>}
        {type === "percent" && typed > 100 && <Alert tone="warning">A percentage cannot be more than 100%.</Alert>}
        {/* The two kinds of "no", kept apart: one a supervisor can clear. */}
        {blocked && <Alert tone="warning" title="Not allowed">{ruling.message}</Alert>}
        {ruling.needsApproval && (
          <Alert tone="info" title="This needs authorising">
            {ruling.message} You can apply it now — the till will ask for the authorisation when
            you take the payment.
          </Alert>
        )}
      </div>
    </Modal>
  );
}

/**
 * A supervisor authorising a discount above the cashier's limit.
 *
 * Their own username and password, typed at the till and sent with the sale —
 * the server authenticates them and checks the role
 * (`discount_policy.approver_for`). Nothing about the approver is asserted by
 * this screen: a user id in a request body would be an authorisation every
 * cashier could grant themselves.
 *
 * The credentials are never put in component state that outlives the request:
 * they go straight into the mutation's variables and the dialog unmounts.
 */
function AuthorizeDialog({ message, pending, onClose, onAuthorize }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const ready = username.trim() && password && !pending;

  return (
    <Modal
      open onClose={onClose} title="Authorise this discount"
      description="An accountant or an administrator has to approve a discount above your limit."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={pending}>Cancel</Button>
          <Button disabled={!ready}
                  onClick={() => onAuthorize({ username: username.trim(), password })}>
            {pending ? "Completing…" : "Authorise and complete"}
          </Button>
        </>
      }
    >
      <form className="space-y-4" onSubmit={(event) => {
        event.preventDefault();
        if (ready) onAuthorize({ username: username.trim(), password });
      }}>
        <Alert tone="warning">{message}</Alert>
        <Field label="Authorising staff username" required>
          <Input value={username} autoFocus autoComplete="off"
                 onChange={(event) => setUsername(event.target.value)} />
        </Field>
        <Field label="Password" required>
          <Input type="password" value={password} autoComplete="off"
                 onChange={(event) => setPassword(event.target.value)} />
        </Field>
        <p className="text-sm text-slate-600">
          Their name is recorded on the sale as the person who approved it. Your own name stays
          on it as the person who gave it.
        </p>
        <button type="submit" className="hidden" aria-hidden="true" />
      </form>
    </Modal>
  );
}

function PayDialog({ total, pending, needsApproval, approvalMessage, onClose, onComplete }) {
  const [method, setMethod] = useState("cash");
  const [tendered, setTendered] = useState("");
  const received = tendered === "" ? null : Number(tendered);
  const short = method === "cash" && received !== null && received < total;
  const change = method === "cash" && received !== null && !short ? received - total : 0;
  const quick = [...new Set([total, Math.ceil(total / 500) * 500, Math.ceil(total / 1000) * 1000,
                             Math.ceil(total / 5000) * 5000])].filter((amount) => amount >= total);

  return (
    <Modal
      open onClose={onClose} title={`Take payment — ${money(total)}`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Back to cart</Button>
          <Button variant="success" disabled={pending || short}
                  onClick={() => onComplete({ method, tendered })}>
            {pending ? "Completing…" : "Complete sale"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {needsApproval && (
          <Alert tone="info" title="A supervisor will be asked to authorise">
            {approvalMessage} Completing asks for an accountant's or an administrator's
            username and password.
          </Alert>
        )}
        <div className="grid grid-cols-2 gap-2" role="group" aria-label="Payment method">
          {PAYMENT_METHODS.map(([key, label]) => (
            <button key={key} type="button" aria-pressed={method === key} onClick={() => setMethod(key)}
                    className={`min-h-[48px] rounded-lg border px-3 text-sm font-medium transition ${
                      method === key ? "border-brand-600 bg-brand-50 text-brand-800" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
              {label}
            </button>
          ))}
        </div>
        {method === "cash" && (
          <>
            <Field label="Cash received" hint="Leave empty for the exact amount.">
              <Input type="number" min="0" step="0.01" inputMode="decimal" value={tendered}
                     onChange={(event) => setTendered(event.target.value)} autoFocus />
            </Field>
            <div className="flex flex-wrap gap-2">
              {quick.map((amount) => (
                <Button key={amount} size="sm" variant="secondary" onClick={() => setTendered(String(amount))}>
                  {money(amount)}
                </Button>
              ))}
            </div>
            <p className={`flex justify-between rounded-lg px-3 py-2 text-base font-semibold ${short ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>
              <span>{short ? "Short by" : "Change"}</span>
              <span className="tabular-nums">{money(short ? total - received : change)}</span>
            </p>
          </>
        )}
      </div>
    </Modal>
  );
}

function HeldSales({ onClose, onResume }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { data, isLoading } = useQuery({
    queryKey: ["pos-held"],
    queryFn: () => api.get("/sales/", { params: { status: "held", page_size: 50 } }).then((r) => r.data.results ?? r.data),
  });
  const discard = useMutation({
    mutationFn: (id) => api.post(`/sales/${id}/discard/`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pos-held"] }),
    onError: (error) => showToast({ tone: "error", title: "Could not discard", message: readError(error, "Please try again.") }),
  });
  const held = data ?? [];

  return (
    <Modal open onClose={onClose} title="Held sales"
           description="A held sale charged nothing and moved no stock. Resume it to complete it.">
      {isLoading && <p className="text-slate-600">Loading…</p>}
      {!isLoading && held.length === 0 && <p className="text-slate-600">No sales are on hold.</p>}
      <ul className="divide-y divide-slate-100">
        {held.map((sale) => (
          <li key={sale.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
            <div className="min-w-0">
              <p className="font-medium text-slate-900">{sale.reference} · {sale.customer_label}</p>
              <p className="text-sm text-slate-600">
                {sale.lines.map((line) => `${line.item_name} ×${line.quantity}`).join(", ")}
              </p>
              <p className="text-xs text-slate-500">{new Date(sale.created_at).toLocaleString()} · {sale.sold_by_name}</p>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => onResume(sale)}>Resume</Button>
              <Button size="sm" variant="linkDanger" onClick={() => discard.mutate(sale.id)}>Discard</Button>
            </div>
          </li>
        ))}
      </ul>
    </Modal>
  );
}

function CloseRegister({ register, onClose }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [counted, setCounted] = useState("");
  const [note, setNote] = useState("");
  const { data } = useQuery({
    queryKey: ["pos-register-summary", register.id],
    queryFn: () => api.get(`/pos-registers/${register.id}/summary/`).then((r) => r.data.summary),
  });
  const expected = Number(data?.expected_cash ?? 0);
  const variance = counted === "" ? null : Number(counted) - expected;
  const close = useMutation({
    mutationFn: () => api.post(`/pos-registers/${register.id}/close/`, { counted_cash: counted, note }),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ["pos-register"] });
      queryClient.invalidateQueries({ queryKey: ["pos-sales"] });
      const recorded = Number(response.data.register.variance);
      showToast({
        title: `Register ${register.reference} closed`,
        message: recorded === 0 ? "The drawer balanced." : `Variance of ${money(recorded)} recorded.`,
      });
      onClose();
    },
    onError: (error) => showToast({ tone: "error", title: "Could not close the register", message: readError(error, "Please try again.") }),
  });

  return (
    <Modal
      open onClose={onClose} size="lg" title={`Close register ${register.reference}`}
      description="Closing reconciles the money already taken on this register. It does not record any new revenue."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Keep selling</Button>
          <Button variant="danger" disabled={counted === "" || close.isPending} onClick={() => close.mutate()}>
            {close.isPending ? "Closing…" : "Close register"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <PosRegisterSummary summary={data} />
        <Field label="Cash counted in the drawer">
          <Input type="number" min="0" step="0.01" inputMode="decimal" value={counted}
                 onChange={(event) => setCounted(event.target.value)} />
        </Field>
        {variance !== null && (
          <p className={`flex justify-between rounded-lg px-3 py-2 font-semibold ${variance === 0 ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>
            <span>Variance</span><span className="tabular-nums">{money(variance)}</span>
          </p>
        )}
        <Field label="Note" hint={variance ? "Explain the variance for whoever reconciles this register." : undefined}>
          <Textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
