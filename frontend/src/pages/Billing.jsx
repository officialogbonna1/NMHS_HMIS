import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";
import { readError } from "../api/errors";
import { BILLING_CATEGORIES } from "./BillingItemsAdmin.jsx";
import { BillSheet, ReceiptSheet } from "../components/PrintDocuments.jsx";
import PatientPicker from "../components/PatientPicker.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useToast } from "../components/Toaster.jsx";

// The front desk's billing counter, built around what actually happens at
// it: find the patient, bill them for a card or a consultation, and settle
// it — in full, in part, or not yet. Everything else on the page is there to
// answer "who still owes us money".
const CAN_WAIVE_ROLES = ["admin", "hospital_admin", "cashier", "accountant"];

const currency = (n) => new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(n ?? 0);

// Splitting a bill at the counter is nearly always one of these, so offer
// the figure already worked out rather than asking for mental arithmetic.
const QUICK_SPLITS = [
  { label: "Quarter", fraction: 0.25 },
  { label: "Half", fraction: 0.5 },
  { label: "Three quarters", fraction: 0.75 },
  { label: "Everything owed", fraction: 1 },
];

const STATUS_TONE = {
  unpaid: "bg-red-100 text-red-700",
  partial: "bg-amber-100 text-amber-800",
  paid: "bg-emerald-100 text-emerald-700",
  waived: "bg-slate-100 text-slate-600",
  cancelled: "bg-slate-100 text-slate-400 line-through",
};

export default function Billing() {
  const { user } = useAuth();
  const canWaive = CAN_WAIVE_ROLES.includes(user?.role);
  const [patient, setPatient] = useState(null);

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-5 md:p-8">
      <header>
        <h1 className="text-2xl font-semibold">Billing</h1>
        <p className="mt-1 text-sm text-slate-700">
          {patient
            ? `Everything on this screen is ${patient.first_name}'s. Bill a service, then take payment in full, in part, or leave it to be paid later.`
            : "Find a patient to bill, or pick one from the lists below."}
        </p>
      </header>

      {patient ? (
        <PatientCounter patient={patient} onClear={() => setPatient(null)} canWaive={canWaive} />
      ) : (
        <>
          <ChooseWhoToBill onPick={setPatient} />
          {/* The worklists are how you find somebody. With a patient on the
              counter they are replaced by that patient's own figures. */}
          <OutstandingList onPick={setPatient} />
          <TodaysPayments />
        </>
      )}
    </div>
  );
}

function ChooseWhoToBill({ onPick }) {
  return (
    <section className="rounded-xl border bg-white p-5">
      <label className="mb-1 block text-sm font-medium">Who are you billing?</label>
      <PatientPicker
        value={null}
        onChange={(p) => p && onPick(p)}
        autoFocus
        placeholder="Search by name or file number, or pick from the list…"
        emptyMessage="No patient matches that."
      />
    </section>
  );
}

function PatientCounter({ patient, onClear, canWaive }) {
  // Which sheet is on screen, and the payment a receipt would be for. The
  // receipt is offered the moment money is taken — chasing the patient down
  // the corridor afterwards is not a workflow.
  const [printing, setPrinting] = useState(null);
  const [lastPayment, setLastPayment] = useState(null);
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [discountingAll, setDiscountingAll] = useState(false);

  const { data: ledgerResults } = useQuery({
    queryKey: ["ledger", patient.id],
    queryFn: () => api.get("/ledgers/", { params: { patient: patient.id } }).then((r) => r.data.results ?? r.data),
  });
  const outstanding = Number(ledgerResults?.[0]?.outstanding_balance ?? 0);

  const { data: charges } = useQuery({
    queryKey: ["charges", "patient", patient.id],
    queryFn: () => api.get("/charges/", { params: { patient: patient.id } }).then((r) => r.data.results ?? r.data),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["ledger", patient.id] });
    queryClient.invalidateQueries({ queryKey: ["charges"] });
    queryClient.invalidateQueries({ queryKey: ["payments"] });
    queryClient.invalidateQueries({ queryKey: ["ledgers", "outstanding"] });
  };

  const open = (charges ?? []).filter((c) => ["unpaid", "partial"].includes(c.status));

  return (
    <section className="rounded-xl border bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
        <div>
          <p className="font-semibold">{patient.last_name}, {patient.first_name}</p>
          <p className="text-xs text-slate-600">{patient.file_number}</p>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <p className="text-xs text-slate-500">Owes</p>
            <p className={`text-xl font-bold ${outstanding > 0 ? "text-red-700" : "text-emerald-700"}`}>
              {currency(outstanding)}
            </p>
          </div>
          <button
            onClick={() => setPrinting("bill")}
            className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-slate-50"
          >
            🖨 Bill
          </button>
          <button onClick={onClear} className="rounded-lg border px-3 py-2 text-sm hover:bg-slate-50">
            Different patient
          </button>
        </div>
      </div>

      {printing === "bill" && (
        <BillSheet patient={patient} charges={open} onClose={() => setPrinting(null)} />
      )}
      {printing === "receipt" && lastPayment && (
        <ReceiptSheet
          patient={patient}
          payment={lastPayment}
          balanceAfter={outstanding}
          onClose={() => setPrinting(null)}
        />
      )}

      <div className="space-y-6 p-5">
        <BillAndSettle
          patient={patient}
          outstanding={outstanding}
          onDone={refresh}
          showToast={showToast}
          onPaid={(payment) => { setLastPayment(payment); setPrinting("receipt"); }}
        />

        {canWaive && outstanding > 0 && (
          <div className="rounded-lg border p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-700">Discount this patient's whole bill</h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  Applies the same percentage to every one of their {open.length} open charge{open.length === 1 ? "" : "s"}.
                </p>
              </div>
              <button onClick={() => setDiscountingAll((d) => !d)} className="rounded-lg border px-3 py-2 text-sm hover:bg-slate-50">
                {discountingAll ? "Close" : "Discount bill"}
              </button>
            </div>
            {discountingAll && (
              <DiscountPanel
                label={`Take a percentage off everything ${patient.first_name} owes`}
                amount={outstanding}
                endpoint="/charges/discount-balance/"
                body={{ patient: patient.id }}
                onDone={() => { setDiscountingAll(false); refresh(); }}
              />
            )}
          </div>
        )}

        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">
            Open charges {open.length > 0 && <span className="text-slate-600">({open.length})</span>}
          </h3>
          {open.length === 0 && <p className="text-sm text-slate-600">Nothing outstanding for this patient.</p>}
          <div className="divide-y rounded-lg border">
            {open.map((c) => (
              <ChargeRow key={c.id} charge={c} canWaive={canWaive} onDone={refresh} />
            ))}
          </div>
          <Link to="/transactions" className="mt-2 inline-block text-sm text-brand-600 hover:underline">
            Full transaction history →
          </Link>
        </div>

        <PatientPayments patient={patient} />
      </div>
    </section>
  );
}

// What this patient has paid, in place of the whole hospital's takings.
// A cashier with somebody at the window needs that person's figures and
// nobody else's — the wrong balance read aloud is the mistake this
// prevents.
function PatientPayments({ patient }) {
  const { data: payments, isLoading } = useQuery({
    queryKey: ["payments", "patient", patient.id],
    queryFn: () => api.get("/payments/", { params: { patient: patient.id, page_size: 100 } })
      .then((r) => r.data.results ?? r.data),
  });

  const rows = payments ?? [];
  const today = new Date().toDateString();
  const todaysTotal = rows
    .filter((p) => new Date(p.created_at).toDateString() === today)
    .reduce((sum, p) => sum + Number(p.amount), 0);
  const total = rows.reduce((sum, p) => sum + Number(p.amount), 0);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-700">
          Payments from {patient.first_name}
        </h3>
        <p className="text-sm text-slate-700">
          {currency(todaysTotal)} today · {currency(total)} all time
        </p>
      </div>

      {isLoading && <p className="text-sm text-slate-600">Loading…</p>}
      {!isLoading && rows.length === 0 && (
        <p className="text-sm text-slate-600">This patient has not paid anything yet.</p>
      )}

      {rows.length > 0 && (
        <div className="divide-y rounded-lg border">
          {rows.slice(0, 8).map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-3 px-3 py-2.5 text-sm">
              <div>
                <p className="font-medium text-slate-800">{currency(p.amount)}</p>
                <p className="text-sm text-slate-600">
                  {new Date(p.created_at).toLocaleString()} · {p.method}
                  {p.received_by_name && ` · ${p.received_by_name}`}
                </p>
              </div>
              {p.reference && <span className="text-sm text-slate-600">{p.reference}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Bill something, then say how it was settled — the two halves of one
// counter interaction, so reception never has to compute a half themselves
// or remember to come back and record the payment.
function BillAndSettle({ patient, outstanding, onDone, showToast, onPaid }) {
  const [category, setCategory] = useState("card");
  const [itemId, setItemId] = useState("");
  const [method, setMethod] = useState("cash");
  const [customAmount, setCustomAmount] = useState("");
  const [busy, setBusy] = useState(null);
  // The one-off nobody has priced: a medical report, an ambulance run.
  const [writeInName, setWriteInName] = useState("");
  const [writeInPrice, setWriteInPrice] = useState("");

  const { data: items } = useQuery({
    queryKey: ["billing-items", category],
    queryFn: () => api.get("/billing-items/", { params: { category, is_active: true, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });

  // Only "Other" is typed in; everything else is billed at the catalogue's
  // price so the same service costs the same at every window.
  const writeIn = category === "other";
  const chosen = (items ?? []).find((i) => String(i.id) === itemId);
  // Both paths end up as the same shape, so everything downstream — the
  // settle buttons, the toasts — works without knowing which it was.
  const item = writeIn
    ? (writeInName.trim() && Number(writeInPrice) > 0
        ? { name: writeInName.trim(), price: writeInPrice }
        : null)
    : chosen;
  const price = Number(item?.price ?? 0);

  async function billThenPay(mode) {
    setBusy(mode);
    try {
      // The charge is raised first either way: "pay later" is a real,
      // recorded debt, not a skipped step.
      const { data: charge } = await api.post("/charges/", {
        patient: patient.id, description: item.name, amount: item.price, source_type: category,
      });

      if (mode === "later") {
        // And it is an *authorisation*, so it gets a name and a time against
        // it rather than just being a charge nobody happened to collect.
        await api.post(`/charges/${charge.id}/defer/`, {
          reason: "Pay later approved at the counter.",
        });
        showToast({
          title: "Pay later approved",
          message: `${item.name} · ${currency(price)} still owed — recorded against your name.`,
        });
      } else {
        const amount = mode === "full" ? price : mode === "half" ? round2(price / 2) : Number(customAmount);
        const { data: payment } = await api.post("/payments/", { patient: patient.id, amount, method });
        onPaid?.(payment);
        showToast({
          title: mode === "full" ? "Paid in full" : "Part payment taken",
          message: `${item.name} · ${currency(amount)} of ${currency(price)}`,
        });
      }
      setItemId("");
      setCustomAmount("");
      onDone();
    } catch (error) {
      showToast({
        title: "Could not complete this",
        message: error.response?.data?.detail || "Please try again.",
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  }

  const settlePartial = useMutation({
    mutationFn: (amount) => api.post("/payments/", { patient: patient.id, amount, method }),
    onSuccess: (response) => {
      showToast({ title: "Payment taken", message: currency(response.data.amount) });
      setCustomAmount("");
      onPaid?.(response.data);
      onDone();
    },
    onError: (error) => showToast({
      title: "Could not record payment",
      message: error.response?.data?.detail || "Please try again.",
      tone: "error",
    }),
  });

  const customValid = customAmount !== "" && Number(customAmount) > 0 && Number(customAmount) <= outstanding;

  return (
    <div className="space-y-5">
      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Bill for</h3>
        <div className="flex flex-wrap gap-2">
          {BILLING_CATEGORIES.map(({ category: value, label }) => (
            <button
              key={value}
              onClick={() => { setCategory(value); setItemId(""); setWriteInName(""); setWriteInPrice(""); }}
              className={`rounded-full border px-4 py-1.5 text-sm ${
                category === value ? "border-brand-500 bg-brand-50 text-brand-700" : "hover:bg-slate-50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {(items ?? []).map((i) => (
            <button
              key={i.id}
              onClick={() => setItemId(String(i.id))}
              className={`flex items-center justify-between rounded-lg border px-3 py-2.5 text-left text-sm ${
                itemId === String(i.id) ? "border-brand-500 bg-brand-50" : "hover:bg-slate-50"
              }`}
            >
              <span>{i.name}</span>
              <span className="font-semibold">{currency(i.price)}</span>
            </button>
          ))}
          {!writeIn && items && items.length === 0 && (
            <p className="text-sm text-amber-700">
              Nothing priced under this yet — add it under Billing Catalog.
            </p>
          )}
        </div>

        {writeIn && (
          <div className="mt-3 grid gap-3 sm:grid-cols-[1fr,10rem]">
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">What is it for? *</label>
              <input
                value={writeInName}
                onChange={(e) => setWriteInName(e.target.value)}
                placeholder="e.g. Medical report, ambulance, dressing pack"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">Amount *</label>
              <input
                type="number" min="0" step="0.01"
                value={writeInPrice}
                onChange={(e) => setWriteInPrice(e.target.value)}
                placeholder="0.00"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <p className="text-sm text-slate-600 sm:col-span-2">
              What you type is what the patient sees on their statement.
            </p>
          </div>
        )}
      </div>

      {item && (
        <div className="rounded-lg border border-brand-200 bg-brand-50/50 p-4">
          <p className="text-sm">
            <span className="font-medium">{item.name}</span> — {currency(price)}. How is the patient settling it?
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <label className="text-xs text-slate-500">Method</label>
            <select value={method} onChange={(e) => setMethod(e.target.value)} className="rounded-md border px-2 py-1.5 text-sm">
              <option value="cash">Cash</option>
              <option value="card">Card</option>
              <option value="transfer">Transfer</option>
              <option value="insurance">Insurance</option>
            </select>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              onClick={() => billThenPay("full")}
              disabled={busy !== null}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              {busy === "full" ? "Recording…" : `Pay in full — ${currency(price)}`}
            </button>
            <button
              onClick={() => billThenPay("half")}
              disabled={busy !== null}
              className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-sm font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
            >
              {busy === "half" ? "Recording…" : `Pay half — ${currency(round2(price / 2))}`}
            </button>
            <button
              onClick={() => billThenPay("later")}
              disabled={busy !== null}
              className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-50"
            >
              {busy === "later" ? "Recording…" : "Pay later"}
            </button>
          </div>
          <p className="mt-2 text-xs text-slate-600">
            Every option raises the charge. "Pay later" is an authorisation recorded against your
            name — the money stays owed and stays on the outstanding list.
          </p>
        </div>
      )}

      {outstanding > 0 && (
        <div className="rounded-lg border p-4">
          <h3 className="text-sm font-semibold text-slate-700">Take a payment against the balance</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            Goes against the oldest open charge first. Owed now: {currency(outstanding)}.
          </p>

          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {QUICK_SPLITS.map(({ label, fraction }) => {
              const value = round2(outstanding * fraction);
              const active = customAmount !== "" && Number(customAmount) === value;
              return (
                <button
                  key={label}
                  type="button"
                  onClick={() => setCustomAmount(String(value))}
                  className={`rounded-lg border px-3 py-2.5 text-left transition ${
                    active
                      ? "border-brand-500 bg-brand-50 ring-1 ring-brand-200"
                      : "border-slate-200 hover:border-brand-300 hover:bg-brand-50/40"
                  }`}
                >
                  <span className="block text-xs font-medium text-slate-500">{label}</span>
                  <span className={`block text-sm font-semibold ${active ? "text-brand-700" : "text-slate-800"}`}>
                    {currency(value)}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="mt-3 flex flex-wrap items-end gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Or another amount</label>
              <input
                type="number" min="1" max={outstanding} step="0.01" value={customAmount}
                onChange={(e) => setCustomAmount(e.target.value)}
                placeholder="0.00"
                className="w-36 rounded-md border px-3 py-2 text-sm"
              />
            </div>
            <button
              onClick={() => settlePartial.mutate(Number(customAmount))}
              disabled={!customValid || settlePartial.isPending}
              className="rounded-md bg-brand-600 px-5 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              {settlePartial.isPending ? "Recording…" : `Take ${customValid ? currency(Number(customAmount)) : "payment"}`}
            </button>
            {customAmount !== "" && Number(customAmount) > outstanding && (
              <p className="text-xs text-amber-700">That is more than the {currency(outstanding)} owed.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// Original − discount − waiver = payable; payable − paid = outstanding.
// Every figure is shown rather than only the one that is left, because a
// bill somebody has to answer for has to say what happened to it.
function ChargeAmount({ charge }) {
  const amount = Number(charge.amount);
  const paid = Number(charge.amount_paid);
  const off = Number(charge.amount_discounted);
  const waived = Number(charge.amount_waived ?? 0);
  const balance = Number(charge.balance);
  const settled = balance <= 0;
  const untouched = paid === 0 && off === 0 && waived === 0;

  return (
    <div>
      <p className={`font-semibold ${settled ? "text-emerald-700" : ""}`}>
        {currency(settled ? amount : balance)}
        {!settled && !untouched && <span className="text-xs font-normal text-slate-600"> due</span>}
      </p>
      {!untouched && (
        <p className="text-xs text-slate-600">
          {[
            `${currency(amount)} charged`,
            off > 0 && `${currency(off)} discount`,
            waived > 0 && `${currency(waived)} waived`,
            paid > 0 && `${currency(paid)} paid`,
            !settled && `${currency(balance)} outstanding`,
          ].filter(Boolean).join(" · ")}
        </p>
      )}
    </div>
  );
}

function ChargeRow({ charge, canWaive, onDone }) {
  const [panel, setPanel] = useState(null);
  const { showToast } = useToast();
  const [error, setError] = useState(null);

  const defer = useMutation({
    mutationFn: (reason) => api.post(`/charges/${charge.id}/defer/`, { reason }),
    onSuccess: () => {
      setPanel(null);
      setError(null);
      onDone();
      showToast({
        title: "Pay later approved",
        message: "Still outstanding — it stays on the patient's balance.",
      });
    },
    onError: (err) => setError(readError(err, "Could not approve pay later.")),
  });

  const discounted = Number(charge.amount_discounted) > 0;
  const waived = Number(charge.amount_waived ?? 0) > 0;
  const status = charge.settlement_status ?? charge.status;
  const open = Number(charge.balance) > 0;

  return (
    <div className="px-3 py-2.5 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{charge.description}</span>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              SETTLEMENT_TONE[status] ?? STATUS_TONE[charge.status]}`}>
              {SETTLEMENT_LABEL[status] ?? charge.status_label ?? charge.status}
            </span>
            {discounted && (
              <span className="rounded-full bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-700">
                {currency(Number(charge.amount_discounted))} off
              </span>
            )}
            {waived && (
              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700">
                {currency(Number(charge.amount_waived))} waived
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-slate-600">{new Date(charge.created_at).toLocaleString()}</p>
          {/* Pay later is an authorisation, not a payment — so it says who
              gave it, and the balance stays exactly where it was. */}
          {charge.deferral && (
            <p className="mt-1 text-xs text-sky-800">
              Pay later approved by {charge.deferral.approved_by} on{" "}
              {new Date(charge.deferral.approved_at).toLocaleDateString()}
              {charge.deferral.reason && ` — ${charge.deferral.reason}`}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3 text-right">
          <ChargeAmount charge={charge} />
          {open && (
            <div className="flex shrink-0 flex-col items-end gap-0.5">
              {canWaive && (
                <>
                  <button onClick={() => setPanel(panel === "discount" ? null : "discount")}
                          className="text-xs text-brand-600 hover:underline">
                    {panel === "discount" ? "Close" : "Discount"}
                  </button>
                  <button onClick={() => setPanel(panel === "waive" ? null : "waive")}
                          className="text-xs text-slate-600 hover:text-red-600 hover:underline">
                    {panel === "waive" ? "Close" : "Waive"}
                  </button>
                </>
              )}
              {/* Reception can authorise this even though it cannot waive:
                  it is the desk the patient is standing at, and the money is
                  still owed afterwards. */}
              {!charge.deferral && (
                <button onClick={() => setPanel(panel === "defer" ? null : "defer")}
                        className="text-xs text-sky-700 hover:underline">
                  {panel === "defer" ? "Close" : "Pay later"}
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {panel === "discount" && (
        <DiscountPanel
          label={`Take money off ${charge.description}`}
          amount={Number(charge.balance)}
          faceValue={Number(charge.amount)}
          endpoint={`/charges/${charge.id}/discount/`}
          amountEndpoint={`/charges/${charge.id}/discount-amount/`}
          onDone={() => { setPanel(null); onDone(); }}
        />
      )}

      {panel === "waive" && (
        <WriteOffPanel
          charge={charge}
          onDone={() => { setPanel(null); onDone(); }}
        />
      )}

      {panel === "defer" && (
        <DeferPanel
          charge={charge}
          error={error}
          pending={defer.isPending}
          onConfirm={(reason) => defer.mutate(reason)}
          onCancel={() => { setPanel(null); setError(null); }}
        />
      )}
    </div>
  );
}

// What the money says, not what a stored flag says. "Deferred" is an
// authorised pay-later and is never a kind of paid.
const SETTLEMENT_TONE = {
  paid: "bg-emerald-100 text-emerald-700",
  partial: "bg-amber-100 text-amber-800",
  unpaid: "bg-red-100 text-red-700",
  deferred: "bg-sky-100 text-sky-800",
  waived: "bg-slate-200 text-slate-700",
  cancelled: "bg-slate-200 text-slate-500",
};
const SETTLEMENT_LABEL = {
  paid: "Paid", partial: "Partially paid", unpaid: "Unpaid",
  deferred: "Pay later — still owing", waived: "Waived", cancelled: "Cancelled",
};

// Writing money off, in part or in full. The original amount is never
// touched: what was waived and what it was waived from both have to survive
// an audit, so the panel says the arithmetic out loud before committing.
function WriteOffPanel({ charge, onDone }) {
  const balance = Number(charge.balance);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState(null);

  const waive = useMutation({
    mutationFn: () => api.post(`/charges/${charge.id}/waive/`, {
      reason, ...(amount.trim() ? { amount } : {}),
    }),
    onSuccess: onDone,
    onError: (err) => setError(readError(err, "Could not waive this charge.")),
  });

  const off = amount.trim() ? Math.min(Number(amount) || 0, balance) : balance;

  return (
    <div className="mt-3 rounded-lg border border-slate-300 bg-slate-50 p-3">
      <p className="text-sm font-medium text-slate-800">Write off {charge.description}</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs font-medium text-slate-700">
            Amount to waive
          </label>
          <input
            type="number" min="0" step="0.01" max={balance} value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder={`Everything owed (${currency(balance)})`}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-slate-700">Reason</label>
          <input
            value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Hardship, approved by matron"
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
        </div>
      </div>

      <p className="mt-2 text-sm text-slate-700">
        {currency(Number(charge.amount))} charged − {currency(off)} waived ={" "}
        <strong className="text-slate-900">{currency(balance - off)} still payable</strong>.
      </p>
      {error && <p className="mt-1 text-sm text-red-600">{error}</p>}

      <button
        onClick={() => waive.mutate()}
        disabled={waive.isPending || !reason.trim() || off <= 0}
        className="mt-2 rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-900 disabled:opacity-50"
      >
        {waive.isPending ? "Writing off…" : `Waive ${currency(off)}`}
      </button>
    </div>
  );
}

// Letting the patient have the service now and pay afterwards. This moves no
// money — it records who allowed it, so a test run unpaid has a name against
// it rather than looking like an oversight.
function DeferPanel({ charge, error, pending, onConfirm, onCancel }) {
  const [reason, setReason] = useState("");
  return (
    <div className="mt-3 rounded-lg border border-sky-300 bg-sky-50 p-3">
      <p className="text-sm font-medium text-slate-800">
        Let this patient pay later?
      </p>
      <p className="mt-0.5 text-sm text-slate-700">
        {currency(Number(charge.balance))} stays on their balance and on the outstanding list.
        This is permission to proceed, not a payment.
      </p>
      <input
        value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder="Why — e.g. 'Emergency, family returning Friday'"
        className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
      />
      {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          onClick={() => onConfirm(reason)}
          disabled={pending}
          className="rounded-md bg-sky-700 px-4 py-2 text-sm font-medium text-white hover:bg-sky-800 disabled:opacity-50"
        >
          {pending ? "Recording…" : "Approve pay later"}
        </button>
        <button onClick={onCancel}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700">
          Cancel
        </button>
      </div>
    </div>
  );
}

const QUICK_PERCENTS = [5, 10, 20, 25, 50, 100];

// One control for both "discount this charge" and "discount everything they
// owe". The figure it will take off is worked out as you pick, because a
// percentage of a bill is exactly the sum nobody wants to do in their head
// while a patient waits.
// A discount is either "10% off" or "₦500 off" — both are asked for at a
// counter, and turning one into the other in your head with a patient
// waiting is how the wrong figure gets typed. `amountEndpoint` enables the
// flat mode; without it the panel is percentage-only, as the whole-bill
// discount still is.
function DiscountPanel({ label, amount, faceValue, endpoint, amountEndpoint, body = {}, onDone }) {
  const { showToast } = useToast();
  const [mode, setMode] = useState("percent");
  const [percent, setPercent] = useState("");
  const [flat, setFlat] = useState("");
  const [reason, setReason] = useState("");

  const numeric = Number(percent);
  const byPercent = mode === "percent";
  const hasReason = reason.trim().length > 0;
  const valid = hasReason && (byPercent
    ? percent !== "" && numeric > 0 && numeric <= 100
    : flat !== "" && Number(flat) > 0);
  // The server takes the percentage off face value but caps at what is still
  // owed; mirror that here so the preview matches what actually happens.
  const off = !valid ? 0 : byPercent
    ? Math.min(round2((faceValue ?? amount) * numeric / 100), amount)
    : Math.min(round2(Number(flat)), amount);

  const apply = useMutation({
    mutationFn: () => (byPercent
      ? api.post(endpoint, { ...body, percent: numeric, reason: reason.trim() })
      : api.post(amountEndpoint, { ...body, amount: off, reason: reason.trim() })),
    onSuccess: () => {
      showToast({
        title: byPercent ? `${numeric}% discount applied` : "Discount applied",
        message: `${currency(off)} taken off.`,
      });
      setPercent("");
      setFlat("");
      setReason("");
      onDone();
    },
    onError: (error) => showToast({
      title: "Could not apply the discount",
      message: error.response?.data?.detail || "Please try again.",
      tone: "error",
    }),
  });

  return (
    <div className="mt-3 rounded-lg border border-violet-200 bg-violet-50/60 p-4">
      <p className="text-sm font-medium text-violet-900">{label}</p>

      {amountEndpoint && (
        <div className="mt-2 flex overflow-hidden rounded-lg border border-violet-200 text-sm">
          {[["percent", "By percentage"], ["flat", "By amount"]].map(([value, text]) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              className={`flex-1 px-3 py-1.5 font-medium transition ${
                mode === value ? "bg-violet-600 text-white" : "bg-white text-violet-700"}`}
            >
              {text}
            </button>
          ))}
        </div>
      )}

      {!byPercent ? (
        <div className="mt-3">
          <label className="mb-1 block text-xs font-medium text-violet-900">Amount off (₦)</label>
          <input
            type="number" min="0" step="0.01" max={amount} value={flat}
            onChange={(e) => setFlat(e.target.value)}
            placeholder={`Up to ${currency(amount)}`}
            className="w-full rounded-md border border-violet-200 px-3 py-2 text-sm sm:w-48"
          />
        </div>
      ) : (
      <div className="mt-3 flex flex-wrap gap-2">
        {QUICK_PERCENTS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setPercent(String(p))}
            className={`rounded-full border px-3.5 py-1.5 text-sm font-medium transition ${
              numeric === p
                ? "border-violet-500 bg-violet-600 text-white"
                : "border-violet-200 bg-white text-violet-700 hover:border-violet-400"
            }`}
          >
            {p}%
          </button>
        ))}
        <div className="flex items-center gap-1 rounded-full border border-violet-200 bg-white pl-3">
          <input
            type="number" min="1" max="100" value={percent}
            onChange={(e) => setPercent(e.target.value)}
            placeholder="Other"
            className="w-16 border-0 bg-transparent py-1.5 text-sm outline-none"
          />
          <span className="pr-3 text-sm text-violet-500">%</span>
        </div>
      </div>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-[12rem]">
          <label className="mb-1 block text-xs font-medium text-violet-900">Reason *</label>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. staff family, hardship, management approval"
            className="w-full rounded-md border border-violet-200 px-3 py-2 text-sm"
          />
        </div>
        <button
          onClick={() => apply.mutate()}
          disabled={!valid || apply.isPending}
          className="rounded-md bg-violet-600 px-5 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
        >
          {apply.isPending ? "Applying…" : off > 0 ? `Take ${currency(off)} off` : "Apply discount"}
        </button>
      </div>

      <p className="mt-2 text-xs text-violet-700">
        {valid
          ? `${currency(faceValue ?? amount)} charged − ${currency(off)} discount = ${currency(round2(amount - off))} to pay. The catalogue price does not change.`
          : `Choose ${byPercent ? "a percentage" : "an amount"} and give a reason. It is recorded against your name.`}
      </p>
    </div>
  );
}

function OutstandingList({ onPick }) {
  const { data: ledgers, isLoading } = useQuery({
    queryKey: ["ledgers", "outstanding"],
    queryFn: () => api.get("/ledgers/").then((r) => r.data.results ?? r.data),
  });

  const owing = (ledgers ?? [])
    .filter((l) => Number(l.outstanding_balance) > 0)
    .sort((a, b) => Number(b.outstanding_balance) - Number(a.outstanding_balance));

  const total = owing.reduce((sum, l) => sum + Number(l.outstanding_balance), 0);

  return (
    <section className="overflow-hidden rounded-xl border bg-white">
      <div className="flex items-center justify-between border-b px-5 py-4">
        <h2 className="font-semibold">Still owing</h2>
        <span className="text-sm font-semibold text-red-700">{currency(total)}</span>
      </div>
      {isLoading && <p className="p-5 text-sm text-slate-500">Loading…</p>}
      {!isLoading && owing.length === 0 && <p className="p-5 text-sm text-slate-600">Nobody has an outstanding balance.</p>}
      <div className="divide-y">
        {owing.slice(0, 15).map((l) => (
          <button
            key={l.id}
            onClick={() => onPick({ id: l.patient, last_name: l.patient_name?.split(",")[0] ?? "", first_name: (l.patient_name?.split(",")[1] ?? "").trim(), file_number: "" })}
            className="flex w-full items-center justify-between px-5 py-3 text-left text-sm hover:bg-slate-50"
          >
            <span className="font-medium">{l.patient_name}</span>
            <span className="font-semibold text-red-700">{currency(l.outstanding_balance)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function TodaysPayments() {
  const { data: payments } = useQuery({
    queryKey: ["payments"],
    queryFn: () => api.get("/payments/").then((r) => r.data.results ?? r.data),
  });

  const today = new Date().toDateString();
  const todays = (payments ?? []).filter((p) => new Date(p.created_at).toDateString() === today);
  const total = todays.reduce((sum, p) => sum + Number(p.amount), 0);

  return (
    <section className="overflow-hidden rounded-xl border bg-white">
      <div className="flex items-center justify-between border-b px-5 py-4">
        <h2 className="font-semibold">Taken today</h2>
        <span className="text-sm font-semibold text-emerald-700">{currency(total)}</span>
      </div>
      {todays.length === 0 && <p className="p-5 text-sm text-slate-600">No payments recorded yet today.</p>}
      <div className="divide-y">
        {todays.map((p) => (
          <div key={p.id} className="flex items-center justify-between px-5 py-3 text-sm">
            <div>
              <p className="font-medium">{p.patient_name}</p>
              <p className="text-xs text-slate-600">
                {new Date(p.created_at).toLocaleTimeString()} · {p.method}
                {p.channel === "pharmacy" && " · pharmacy counter"}
              </p>
            </div>
            <span className="font-semibold text-emerald-700">{currency(p.amount)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function round2(n) {
  return Math.round(n * 100) / 100;
}
