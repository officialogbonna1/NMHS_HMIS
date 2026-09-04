import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import { useToast } from "./Toaster.jsx";
import LabReportSheet from "./LabReportSheet.jsx";

// Entering laboratory results.
//
// The rule this whole panel is built around: **the scientist fills in what
// they measured and nothing else.** Every parameter is optional, a blank
// stays blank, and Submit works with one value filled in out of fourteen.
// Nothing is validated into existence, nothing turns red for being empty,
// and the printed report carries only the lines that have a result.
//
// The parameters themselves come from the server (`/lab-tests/`), never from
// this file. Adding "Serum Magnesium" is a row the lab adds on the
// Laboratory Catalogue page — no code change, no deployment.

const FLAG_TONE = {
  low: "bg-amber-50 text-amber-800 ring-amber-200",
  high: "bg-amber-50 text-amber-800 ring-amber-200",
  normal: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  abnormal: "bg-red-50 text-red-700 ring-red-200",
  critical: "bg-red-100 text-red-800 ring-red-300",
  positive: "bg-red-50 text-red-700 ring-red-200",
  negative: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  reactive: "bg-red-50 text-red-700 ring-red-200",
  non_reactive: "bg-emerald-50 text-emerald-700 ring-emerald-200",
};

// Draft → Submitted → Verified/Released. The tone says how far along it is,
// so a scientist can see at a glance what has left the building.
const STATUS_TONE = {
  pending: "bg-slate-100 text-slate-600 ring-slate-200",
  draft: "bg-amber-50 text-amber-800 ring-amber-200",
  submitted: "bg-sky-50 text-sky-800 ring-sky-200",
  verified: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  cancelled: "bg-slate-100 text-slate-500 ring-slate-200",
};

// What the money says about this one test. Read from the charge the order
// raised — the laboratory never writes it, and it never blocks the work: a
// sample already drawn gets run and the desk chases the balance.
const PAYMENT_TONE = {
  paid: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  partial: "bg-amber-50 text-amber-800 ring-amber-200",
  unpaid: "bg-red-50 text-red-700 ring-red-200",
  deferred: "bg-sky-50 text-sky-800 ring-sky-200",
  waived: "bg-slate-100 text-slate-600 ring-slate-200",
  unbilled: "bg-slate-100 text-slate-600 ring-slate-200",
  cancelled: "bg-slate-100 text-slate-500 ring-slate-200",
};

const PAYMENT_LABEL = {
  paid: "Paid",
  partial: "Part paid",
  unpaid: "UNPAID",
  deferred: "Pay later approved",
  waived: "Waived",
  unbilled: "No price set",
  cancelled: "Charge cancelled",
};

function PaymentBadge({ billing }) {
  if (!billing) return null;
  const status = billing.status ?? "unbilled";
  const owing = Number(billing.outstanding ?? 0);
  return (
    <span
      title={billing.billed
        ? `₦${billing.amount} charged · ₦${billing.paid} paid · ₦${billing.outstanding} outstanding`
        : "This test has no price in the catalogue, so no charge was raised."}
      className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${
        PAYMENT_TONE[status] ?? PAYMENT_TONE.unbilled}`}
    >
      {PAYMENT_LABEL[status] ?? status}
      {owing > 0 && status !== "unbilled" && ` · ₦${owing.toLocaleString()}`}
    </span>
  );
}

// What a scientist may flag by hand, on top of the automatic low/normal/high.
const MANUAL_FLAGS = [
  { value: "", label: "Auto" },
  { value: "normal", label: "Normal" },
  { value: "low", label: "Low" },
  { value: "high", label: "High" },
  { value: "abnormal", label: "Abnormal" },
  { value: "critical", label: "Critical" },
  { value: "positive", label: "Positive" },
  { value: "negative", label: "Negative" },
  { value: "reactive", label: "Reactive" },
  { value: "non_reactive", label: "Non-reactive" },
];

export default function LabResultEntry({ routeId, patientId, onDone }) {
  const queryClient = useQueryClient();
  const [orderId, setOrderId] = useState(null);
  const [error, setError] = useState(null);
  // Who the result goes back to. Empty means "leave it as it is" — the
  // doctor who referred — which is the answer almost every time.
  const [recipient, setRecipient] = useState("");

  // Only doctors, and only for the redirect: a result is addressed to one
  // named person, never broadcast to a role.
  const { data: doctors } = useQuery({
    queryKey: ["doctors"],
    queryFn: () => api.get("/users/", { params: { role: "doctor", page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
    staleTime: 300000,
  });

  // A doctor's referral is already the request, so the order is opened from
  // it rather than asking the bench to raise a second one.
  const open = useMutation({
    mutationFn: () => api.post("/lab-orders/for-route/", { route: routeId }).then((r) => r.data),
    onSuccess: (data) => setOrderId(data.id),
    onError: (err) => setError(readError(err, "Could not open the laboratory order.")),
  });

  useEffect(() => {
    if (routeId && !orderId && !open.isPending) open.mutate();
    // Opening is idempotent server-side — the same route always answers with
    // the same order.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeId]);

  const { data: order, isLoading } = useQuery({
    queryKey: ["lab-order", orderId],
    queryFn: () => api.get(`/lab-orders/${orderId}/`).then((r) => r.data),
    enabled: Boolean(orderId),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["lab-order", orderId] });
    queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
    if (patientId) {
      queryClient.invalidateQueries({ queryKey: ["patient-overview", String(patientId)] });
    }
  };

  if (error) {
    return (
      <section className="rounded-xl border border-red-200 bg-red-50 p-4">
        <p className="text-sm text-red-700">{error}</p>
      </section>
    );
  }
  if (isLoading || !order) {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <p className="text-sm text-slate-600">Opening the laboratory order…</p>
      </section>
    );
  }

  return (
    <div className="space-y-4">
      <OrderHeader order={order} onRefresh={refresh} />
      <TestPicker order={order} onRefresh={refresh} />

      {order.items.filter((i) => i.status !== "cancelled").map((item) => (
        <TestPanel key={item.id} order={order} item={item} onRefresh={refresh}
                   recipient={recipient} />
      ))}

      {order.items.length === 0 && (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-600">
          No tests on this order yet. Add what the doctor asked for above.
        </p>
      )}

      <VerifyPanel
        order={order}
        onRefresh={refresh}
        onDone={onDone}
        recipient={recipient}
        setRecipient={setRecipient}
        doctors={doctors}
      />
    </div>
  );
}

function OrderHeader({ order, onRefresh }) {
  const [specimen, setSpecimen] = useState(order.specimen_id || "");
  const { showToast } = useToast();

  const collect = useMutation({
    mutationFn: () => api.post(`/lab-orders/${order.id}/collect-sample/`,
      { specimen_id: specimen }),
    onSuccess: () => {
      onRefresh();
      showToast({ title: "Sample logged" });
    },
  });

  const billing = order.billing ?? {};

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-slate-900">
            Laboratory order {order.order_number}
          </h2>
          <p className="mt-0.5 text-sm text-slate-600">
            {order.status_label}
            {order.report_to_name && ` · result goes to ${order.report_to_name}`}
            {order.entered_by_name && ` · last entered by ${order.entered_by_name}`}
          </p>
        </div>
        {/* The order's money, summed from its tests' own charges. Ordering
            raised them; the counter settles them. The lab reads this and
            never writes it — and it never blocks work, because a sample
            already drawn gets run and the desk chases the balance. */}
        {billing.billed ? (
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset ${
            billing.settled
              ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
              : billing.deferred
                ? "bg-sky-50 text-sky-800 ring-sky-200"
                : "bg-red-50 text-red-700 ring-red-200"}`}>
            {billing.settled
              ? "Paid in full"
              : billing.deferred
                ? `Pay later · ₦${Number(billing.outstanding).toLocaleString()} owing`
                : `UNPAID · ₦${Number(billing.outstanding).toLocaleString()}`}
          </span>
        ) : (
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
            No priced tests on this order
          </span>
        )}
      </div>

      {billing.billed && !billing.settled && (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {billing.deferred
            ? `Pay later approved${billing.deferred_by ? ` by ${billing.deferred_by}` : ""} — ₦${Number(billing.outstanding).toLocaleString()} is still owed. Send the patient to the desk when they are done.`
            : `₦${Number(billing.outstanding).toLocaleString()} outstanding. The patient settles it at Reception or the cash desk — this does not stop you running the test.`}
        </p>
      )}

      {order.clinical_notes && (
        <p className="mt-3 rounded-lg bg-brand-50/70 px-3 py-2 text-sm text-slate-800">
          <span className="font-medium">What was asked for:</span> {order.clinical_notes}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <div className="min-w-[12rem] flex-1">
          <label className="mb-1 block text-sm font-medium text-slate-700">
            Specimen / sample ID
          </label>
          <input
            value={specimen}
            onChange={(e) => setSpecimen(e.target.value)}
            placeholder="e.g. S-0142"
            className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-base text-slate-900 sm:py-2 sm:text-sm"
          />
        </div>
        <button
          type="button"
          onClick={() => collect.mutate()}
          disabled={collect.isPending}
          className="rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 disabled:opacity-50 sm:py-2"
        >
          {order.specimen_collected_at ? "Update sample" : "Log sample collected"}
        </button>
      </div>
      {order.specimen_collected_at && (
        <p className="mt-2 text-sm text-slate-600">
          Collected {new Date(order.specimen_collected_at).toLocaleString()}
          {order.collected_by_name && ` by ${order.collected_by_name}`}
        </p>
      )}
    </section>
  );
}

// What is on this order, and the only way to add to it.
//
// The order starts as **exactly what the clinician asked for** — nothing is
// pre-loaded from the catalogue. That sounds obvious; it was not always true
// here. Panels ("Antenatal Booking Profile", "Malaria Screen") used to sit
// as always-visible chips right under this heading, and one click on one of
// them silently put twelve tests on an order that was raised for a malaria
// film. They now live behind "Add a test", and a panel asks before it adds,
// listing what it is about to put on the bill.
//
// That last part is the real reason for the confirmation: every test added
// here raises a charge on the patient, so adding one is a financial act, not
// a display preference.
function TestPicker({ order, onRefresh }) {
  const [term, setTerm] = useState("");
  const [openList, setOpenList] = useState(false);
  const [confirming, setConfirming] = useState(null);
  const { showToast } = useToast();

  const { data: tests } = useQuery({
    queryKey: ["lab-tests", term],
    queryFn: () => api.get("/lab-tests/", {
      params: { ...(term.trim() ? { search: term.trim() } : {}), page_size: 300 },
    }).then((r) => r.data.results ?? r.data),
    enabled: openList,
    staleTime: 60000,
  });

  const { data: panels } = useQuery({
    queryKey: ["lab-panels"],
    queryFn: () => api.get("/lab-panels/").then((r) => r.data.results ?? r.data),
    enabled: openList,
    staleTime: 300000,
  });

  const add = useMutation({
    mutationFn: (body) => api.post(`/lab-orders/${order.id}/add-tests/`, body),
    onSuccess: (response, body) => {
      onRefresh();
      setTerm("");
      setConfirming(null);
      showToast({
        title: "Added to the order",
        message: body.panels ? "The bill has been raised for each test." : undefined,
      });
    },
  });

  const already = new Set(order.items.map((i) => i.test));
  const grouped = useMemo(() => {
    const groups = new Map();
    for (const test of tests ?? []) {
      if (!groups.has(test.category_label)) groups.set(test.category_label, []);
      groups.get(test.category_label).push(test);
    }
    return [...groups.entries()];
  }, [tests]);

  const requested = order.items.filter((i) => i.source === "requested" && i.status !== "cancelled");
  const addedByLab = order.items.filter((i) => i.source === "laboratory" && i.status !== "cancelled");

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-slate-900">Tests on this order</h2>
          <p className="mt-0.5 text-sm text-slate-600">
            {requested.length > 0
              ? `${requested.length} requested by ${order.requested_by_name ?? "the doctor"}`
              : "Nothing requested yet"}
            {addedByLab.length > 0 && ` · ${addedByLab.length} added here`}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpenList((v) => !v)}
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-brand-400 hover:bg-brand-50"
        >
          {openList ? "Close" : "+ Add a test"}
        </button>
      </div>

      {openList && (
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/60 p-3">
          <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
            Anything added here is charged to the patient at the catalogue price, the same as a
            test the doctor ordered.
          </p>

          <input
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="Search the catalogue — FBC, urinalysis, LFT…"
            autoFocus
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-base text-slate-900 sm:py-2 sm:text-sm"
          />

          {/* Panels are a shortcut into the same catalogue, not a filter —
              which is why they ask first and say what they will add. */}
          {(panels?.length ?? 0) > 0 && (
            <div className="mt-3">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Panels — add several at once
              </p>
              <div className="flex flex-wrap gap-2">
                {panels.map((panel) => (
                  <button
                    key={panel.id}
                    type="button"
                    onClick={() => setConfirming(panel)}
                    className="rounded-full border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-brand-400 hover:bg-brand-50"
                  >
                    {panel.name} ({panel.test_details?.length ?? 0})
                  </button>
                ))}
              </div>
            </div>
          )}

          {confirming && (
            <PanelConfirmation
              panel={confirming}
              already={already}
              pending={add.isPending}
              onCancel={() => setConfirming(null)}
              onConfirm={() => add.mutate({ panels: [confirming.code] })}
            />
          )}

          <div className="mt-3 max-h-80 overflow-y-auto pr-1">
            {grouped.map(([category, rows]) => (
              <div key={category} className="mb-3">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {category}
                </p>
                <div className="flex flex-wrap gap-2">
                  {rows.map((test) => (
                    <button
                      key={test.id}
                      type="button"
                      disabled={already.has(test.id) || add.isPending}
                      onClick={() => add.mutate({ tests: [test.id] })}
                      className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                        already.has(test.id)
                          ? "cursor-not-allowed border-slate-200 bg-slate-100 text-slate-400"
                          : "border-slate-300 bg-white text-slate-800 hover:border-brand-400 hover:bg-brand-50"
                      }`}
                    >
                      {test.name}
                      {already.has(test.id)
                        ? " ✓"
                        : Number(test.charge_amount) > 0
                          && ` · ₦${Number(test.charge_amount).toLocaleString()}`}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            {grouped.length === 0 && (
              <p className="py-4 text-center text-sm text-slate-600">
                Nothing in the catalogue matches that.
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function PanelConfirmation({ panel, already, pending, onCancel, onConfirm }) {
  const adding = (panel.test_details ?? []).filter((t) => !already.has(t.id));
  const total = adding.reduce((sum, t) => sum + Number(t.charge_amount ?? 0), 0);

  return (
    <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-3">
      <p className="font-medium text-slate-900">
        Add all {adding.length} test{adding.length === 1 ? "" : "s"} in “{panel.name}”?
      </p>
      {panel.description && <p className="mt-0.5 text-sm text-slate-700">{panel.description}</p>}

      <ul className="mt-2 flex flex-wrap gap-1.5">
        {adding.map((t) => (
          <li key={t.id} className="rounded bg-white px-2 py-0.5 text-xs text-slate-800">
            {t.name}
          </li>
        ))}
      </ul>
      {adding.length === 0 && (
        <p className="mt-2 text-sm text-slate-700">Every test in it is already on this order.</p>
      )}

      <p className="mt-2 text-sm font-semibold text-slate-900">
        This will add ₦{total.toLocaleString()} to the patient's bill.
      </p>

      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          onClick={onConfirm}
          disabled={pending || adding.length === 0}
          className="w-full rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50 sm:w-auto"
        >
          {pending ? "Adding…" : `Add ${adding.length} test${adding.length === 1 ? "" : "s"}`}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="w-full rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 sm:w-auto"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function TestPanel({ order, item, onRefresh, recipient }) {
  const { showToast } = useToast();
  const [error, setError] = useState(null);
  const [reason, setReason] = useState("");
  const [collapsed, setCollapsed] = useState(
    item.status === "submitted" || item.status === "verified");

  // The form's own copy of the values, keyed by parameter id. A parameter
  // the bench has not touched simply has no entry here, and an entry that is
  // emptied is sent as "" — which the server reads as "clear this", not as
  // an error.
  const [draft, setDraft] = useState(() => seed(item));
  // Keyed on what is actually *stored*, not on the array identity: a
  // background refetch returns an equal-but-new array, and resetting on that
  // would wipe whatever the scientist is halfway through typing.
  const stored = useMemo(
    () => (item.values ?? []).map((v) => `${v.parameter}:${v.value}:${v.flag}`).join("|")
      + `|${item.comments ?? ""}`,
    [item.values, item.comments],
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setDraft(seed(item)), [item.id, stored]);

  const save = useMutation({
    mutationFn: (submit) => api.post(`/lab-orders/${order.id}/save-results/`, {
      order_test: item.id,
      submit,
      reason: reason.trim(),
      // Submitting is what tells the doctor, so the chosen recipient has to
      // travel with it — not only with the later verification.
      ...(submit && recipient ? { notify_doctor: recipient } : {}),
      comments: draft.comments,
      // Every parameter the form knows about is sent, blanks included: a
      // blank is how the bench says "not this one", and the server stores
      // nothing for it.
      values: item.parameters.map((p) => ({
        parameter: p.id,
        value: draft.values[p.id]?.value ?? "",
        flag: draft.values[p.id]?.flag ?? "",
        comment: draft.values[p.id]?.comment ?? "",
      })),
    }),
    onSuccess: (_r, submit) => {
      setError(null);
      setReason("");
      onRefresh();
      showToast({
        title: submit ? "Result submitted" : "Draft saved",
        message: submit
          ? "The doctor who asked has been told."
          : "Nothing has gone out yet — come back and finish it.",
      });
      if (submit) setCollapsed(true);
    },
    onError: (err) => setError(readError(err, "Could not save this result.")),
  });

  const filled = item.parameters.filter((p) => (draft.values[p.id]?.value ?? "").trim()).length;
  const groups = useMemo(() => groupParameters(item.parameters), [item.parameters]);
  const released = item.status === "verified";
  const submitted = item.status === "submitted" || released;

  function set(parameterId, patch) {
    setDraft((current) => ({
      ...current,
      values: {
        ...current.values,
        [parameterId]: { ...(current.values[parameterId] ?? {}), ...patch },
      },
    }));
  }

  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 text-left transition hover:bg-slate-50 sm:px-5"
      >
        <div className="min-w-0">
          <h3 className="font-semibold text-slate-900">{item.test_name}</h3>
          <p className="mt-0.5 text-sm text-slate-600">
            {filled} of {item.parameters.length} filled in
            {item.specimen_type && ` · ${item.specimen_type}`}
            {item.source === "laboratory" && " · added here"}
            {item.performed_by_name && ` · by ${item.performed_by_name}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <PaymentBadge billing={item.billing} />
          <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${
            STATUS_TONE[item.status] ?? "bg-slate-100 text-slate-600 ring-slate-200"}`}>
            {item.status_label}
          </span>
          <span aria-hidden="true" className="text-slate-500">{collapsed ? "▸" : "▾"}</span>
        </div>
      </button>

      {!collapsed && (
        <div className="p-4 sm:p-5">
          <p className="mb-4 text-sm text-slate-600">
            Fill in only what you ran. Anything left blank is left off the report — it is not
            an error and it will not stop you submitting.
          </p>

          {groups.map(([group, parameters]) => (
            <div key={group || "_"} className="mb-5 last:mb-0">
              {group && (
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {group}
                </p>
              )}

              {/* A table on a desk, stacked cards on a phone — the bench works
                  on both, and a 5-column grid on a 360px screen is unusable. */}
              <div className="hidden sm:block">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                      <th className="pb-2 pr-3 font-semibold">Parameter</th>
                      <th className="pb-2 pr-3 font-semibold">Result</th>
                      <th className="pb-2 pr-3 font-semibold">Unit</th>
                      <th className="pb-2 pr-3 font-semibold">Reference range</th>
                      <th className="pb-2 font-semibold">Flag</th>
                    </tr>
                  </thead>
                  <tbody>
                    {parameters.map((p) => (
                      <tr key={p.id} className="border-b border-slate-100 last:border-0">
                        <td className="py-2 pr-3 align-middle text-slate-800">{p.name}</td>
                        <td className="py-2 pr-3 align-middle">
                          <ValueInput
                            parameter={p}
                            value={draft.values[p.id]?.value ?? ""}
                            onChange={(value) => set(p.id, { value })}
                          />
                        </td>
                        <td className="py-2 pr-3 align-middle text-slate-600">{p.unit || "—"}</td>
                        <td className="py-2 pr-3 align-middle text-slate-600">
                          {p.reference_range || p.normal_value || "—"}
                        </td>
                        <td className="py-2 align-middle">
                          <FlagCell
                            parameter={p}
                            entry={draft.values[p.id]}
                            onChange={(flag) => set(p.id, { flag })}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="space-y-3 sm:hidden">
                {parameters.map((p) => (
                  <div key={p.id} className="rounded-lg border border-slate-200 p-3">
                    <div className="mb-1 flex items-baseline justify-between gap-2">
                      <span className="font-medium text-slate-800">{p.name}</span>
                      {p.unit && <span className="text-xs text-slate-600">{p.unit}</span>}
                    </div>
                    <ValueInput
                      parameter={p}
                      value={draft.values[p.id]?.value ?? ""}
                      onChange={(value) => set(p.id, { value })}
                    />
                    <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                      <span className="text-xs text-slate-600">
                        {p.reference_range || p.normal_value || "No reference range"}
                      </span>
                      <FlagCell
                        parameter={p}
                        entry={draft.values[p.id]}
                        onChange={(flag) => set(p.id, { flag })}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          <label className="mb-1 mt-4 block text-sm font-medium text-slate-700">
            Comments / observations
          </label>
          <textarea
            rows={3}
            value={draft.comments}
            onChange={(e) => setDraft((c) => ({ ...c, comments: e.target.value }))}
            placeholder="e.g. Sample slightly haemolysed. No growth after 48 hours."
            className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-base text-slate-900 sm:py-2 sm:text-sm"
          />

          {released && (
            <div className="mt-3">
              <label className="mb-1 block text-sm font-medium text-slate-700">
                Reason for the correction
              </label>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="This result has gone out — say what is being corrected and why."
                className="w-full rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-base text-slate-900 sm:py-2 sm:text-sm"
              />
              <p className="mt-1 text-sm text-amber-800">
                This result has been released to the doctor. A correction is recorded as an
                amendment against your name, and the server will not take it without a reason.
              </p>
            </div>
          )}

          {item.amendments?.length > 0 && (
            <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Amendments
              </p>
              {item.amendments.map((a) => (
                <p key={a.id} className="mt-1 text-sm text-slate-700">
                  {a.parameter_name}: {a.previous_value || "—"} → {a.new_value || "cleared"}
                  {a.reason && ` · ${a.reason}`} · {a.amended_by_name} ·{" "}
                  {new Date(a.created_at).toLocaleString()}
                </p>
              ))}
            </div>
          )}

          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
            <button
              type="button"
              onClick={() => save.mutate(false)}
              disabled={save.isPending}
              className="w-full rounded-lg border border-slate-300 px-5 py-2.5 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 disabled:opacity-50 sm:w-auto"
            >
              {save.isPending ? "Saving…" : "Save draft"}
            </button>
            <button
              type="button"
              onClick={() => save.mutate(true)}
              disabled={save.isPending || (released && !reason.trim())}
              title={released && !reason.trim() ? "Give a reason for the correction" : undefined}
              className="w-full rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50 sm:w-auto"
            >
              {released ? "Save correction" : submitted ? "Re-submit" : "Submit result"}
            </button>
            <span className="text-sm text-slate-600">
              {filled === 0
                ? "Nothing entered yet — a comment on its own is a valid result too."
                : `${filled} value${filled === 1 ? "" : "s"} will be reported.`}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

function ValueInput({ parameter, value, onChange }) {
  const box = "w-full rounded-lg border border-slate-300 px-3 py-2.5 text-base text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-100 sm:py-1.5 sm:text-sm";

  if (parameter.choices?.length) {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={box}>
        {/* The empty option is the default and stays available: choosing it
            back is how a scientist un-enters something. */}
        <option value="">—</option>
        {parameter.choices.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    );
  }

  return (
    <input
      // Not type="number": a bench writes "< 5", "Nil", "3+" and "Trace" into
      // numeric fields, and a number input silently discards all of them.
      inputMode={parameter.result_type === "numeric" ? "decimal" : "text"}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={parameter.result_type === "numeric" ? parameter.unit || "Value" : "Finding"}
      className={box}
    />
  );
}

function FlagCell({ parameter, entry, onChange }) {
  const value = entry?.value ?? "";
  const manual = entry?.flag ?? "";
  const auto = manual ? "" : autoFlag(parameter, value);
  const shown = manual || auto;

  return (
    <div className="flex items-center gap-2">
      {shown && (
        <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${
          FLAG_TONE[shown] ?? "bg-slate-100 text-slate-600 ring-slate-200"}`}>
          {MANUAL_FLAGS.find((f) => f.value === shown)?.label ?? shown}
        </span>
      )}
      <select
        value={manual}
        onChange={(e) => onChange(e.target.value)}
        aria-label={`Flag for ${parameter.name}`}
        className="rounded-md border border-slate-300 bg-white px-1.5 py-1 text-xs text-slate-700"
      >
        {MANUAL_FLAGS.map((f) => (
          <option key={f.value} value={f.value}>{f.label}</option>
        ))}
      </select>
    </div>
  );
}

function VerifyPanel({ order, onRefresh, onDone, recipient, setRecipient, doctors }) {
  const { showToast } = useToast();
  const [comments, setComments] = useState(order.lab_comments || "");
  const [error, setError] = useState(null);
  const [printing, setPrinting] = useState(false);

  const verify = useMutation({
    mutationFn: () => api.post(`/lab-orders/${order.id}/verify/`, {
      lab_comments: comments,
      ...(recipient ? { notify_doctor: recipient } : {}),
    }),
    onSuccess: () => {
      setError(null);
      onRefresh();
      showToast({
        title: "Report released",
        message: "It is on the patient's chart and the doctor has been told.",
      });
      onDone?.();
    },
    onError: (err) => setError(readError(err, "Could not release this report.")),
  });

  const resulted = order.items.filter((i) => i.status === "resulted").length;
  const outstanding = order.items.filter(
    (i) => i.status !== "resulted" && i.status !== "cancelled").length;

  return (
    <section className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4 sm:p-5">
      <h2 className="font-semibold text-slate-900">Release the report</h2>
      <p className="mt-1 text-sm text-slate-700">
        Verifying signs the report off in your name, closes the referral and puts the result on
        the patient's chart. Tests still open are simply left off it.
      </p>

      {/* One named doctor, never a broadcast. The referrer is the default
          and needs no thought; naming somebody else is for when that doctor
          is off and a colleague is covering the patient. */}
      <label className="mb-1 mt-4 block text-sm font-medium text-slate-700">
        Send the result to
      </label>
      <select
        value={recipient}
        onChange={(e) => setRecipient(e.target.value)}
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-base text-slate-900 sm:py-2 sm:text-sm"
      >
        <option value="">
          {order.report_to_name
            ? `${order.report_to_name} — the doctor who asked`
            : "The doctor who asked"}
        </option>
        {(doctors ?? []).map((doctor) => (
          <option key={doctor.id} value={doctor.id}>
            {doctorName(doctor)}
          </option>
        ))}
      </select>
      <p className="mt-1 text-sm text-slate-600">
        Only this doctor is notified. Change it if they are off and someone else is covering.
      </p>

      <label className="mb-1 mt-4 block text-sm font-medium text-slate-700">
        Laboratory comments (printed on the report)
      </label>
      <textarea
        rows={2}
        value={comments}
        onChange={(e) => setComments(e.target.value)}
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-base text-slate-900 sm:py-2 sm:text-sm"
      />

      {order.verified_at && (
        <p className="mt-2 text-sm text-emerald-800">
          Verified by {order.verified_by_name} on {new Date(order.verified_at).toLocaleString()}.
        </p>
      )}
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <button
          type="button"
          onClick={() => verify.mutate()}
          disabled={verify.isPending || resulted === 0}
          title={resulted === 0 ? "Submit at least one test first" : undefined}
          className="w-full rounded-lg bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
        >
          {verify.isPending ? "Releasing…" : order.verified_at ? "Re-verify" : "Verify & release"}
        </button>
        <button
          type="button"
          onClick={() => setPrinting(true)}
          disabled={resulted === 0}
          className="w-full rounded-lg border border-slate-300 bg-white px-5 py-2.5 text-sm font-medium text-slate-700 transition hover:border-slate-400 disabled:opacity-50 sm:w-auto"
        >
          🖨 Print report
        </button>
        <span className="text-sm text-slate-700">
          {resulted} submitted{outstanding > 0 && `, ${outstanding} still open`}.
        </span>
      </div>

      {/* Printing before verification is allowed, and the sheet stamps it
          "provisional" — a ward sometimes needs the figure now. */}
      {printing && <LabReportSheet orderId={order.id} onClose={() => setPrinting(false)} />}
    </section>
  );
}

// --- helpers --------------------------------------------------------------

// The same formatting Send to Doctor uses, so a doctor is named the same way
// wherever they are picked.
function doctorName(doctor) {
  return [doctor.first_name, doctor.last_name].filter(Boolean).join(" ") || doctor.username;
}

function seed(item) {
  const values = {};
  for (const value of item.values ?? []) {
    values[value.parameter] = {
      value: value.value,
      flag: value.flag_is_manual ? value.flag : "",
      comment: value.comment ?? "",
    };
  }
  return { values, comments: item.comments ?? "" };
}

function groupParameters(parameters) {
  const groups = new Map();
  for (const parameter of parameters) {
    const key = parameter.group || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(parameter);
  }
  return [...groups.entries()];
}

// The same arithmetic the server does, so the flag appears as you type
// rather than after a round trip. The server's answer is what is stored.
function autoFlag(parameter, value) {
  if (parameter.result_type !== "numeric") return "";
  if (parameter.ref_low == null && parameter.ref_high == null) return "";
  const text = String(value).trim().replace(/,/g, "");
  if (!text || "<>≤≥".includes(text[0])) return "";
  const number = Number(text);
  if (Number.isNaN(number)) return "";
  if (parameter.ref_low != null && number < Number(parameter.ref_low)) return "low";
  if (parameter.ref_high != null && number > Number(parameter.ref_high)) return "high";
  return "normal";
}
