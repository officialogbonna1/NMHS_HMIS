import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { Button } from "../components/ui.jsx";
import LabReportSheet from "../components/LabReportSheet.jsx";

// The lab's answers, on the patient's chart.
//
// The doctor is notified when a result lands, but a notification is a nudge,
// not a record — this is where they come back to read it a week later. Each
// order shows its tests, every parameter that has a value, the unit and the
// reference range **as they stood when the test was run**, and the flag.
//
// Nothing here is editable: a result belongs to the bench, and a correction
// goes through their amendment flow, which names who changed it and why.

const FLAG_TONE = {
  low: "bg-amber-50 text-amber-800 ring-amber-200",
  high: "bg-amber-50 text-amber-800 ring-amber-200",
  critical: "bg-red-100 text-red-800 ring-red-300",
  abnormal: "bg-red-50 text-red-700 ring-red-200",
  positive: "bg-red-50 text-red-700 ring-red-200",
  reactive: "bg-red-50 text-red-700 ring-red-200",
  normal: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  negative: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  non_reactive: "bg-emerald-50 text-emerald-700 ring-emerald-200",
};

export default function LabResultsTab({ patientId }) {
  const [printing, setPrinting] = useState(null);
  const [showAll, setShowAll] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["lab-orders", String(patientId)],
    // `detail=1` brings the values with the list — the counter asking the
    // same endpoint gets the summary, which has none.
    queryFn: () => api.get("/lab-orders/", {
      params: { patient: patientId, detail: 1, page_size: 50 },
    }).then((r) => r.data.results ?? r.data),
  });

  if (isLoading) return <p className="text-slate-600">Loading laboratory results…</p>;
  if (isError) return <p className="text-red-600">Could not load laboratory results.</p>;

  const orders = (data ?? []).filter((o) => o.status !== "cancelled");
  // A request with nothing filed yet is still worth showing — it says the
  // test is running — but it is not what the doctor came for.
  const withResults = orders.filter((o) =>
    o.items?.some((i) => i.values?.length || i.comments));
  const visible = showAll ? orders : withResults;

  if (orders.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center">
        <p className="text-3xl">🧫</p>
        <p className="mt-2 font-medium text-slate-800">No laboratory work for this patient</p>
        <p className="mt-1 text-sm text-slate-600">
          Send them with <strong>Refer Patient</strong> and the result comes back here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-700">
          {withResults.length} of {orders.length} order
          {orders.length === 1 ? "" : "s"} reported.
        </p>
        {orders.length > withResults.length && (
          <Button variant="link" size="xs" onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Show only reported" : `Show the ${orders.length - withResults.length} still running`}
          </Button>
        )}
      </div>

      {visible.map((order) => (
        <LabOrderCard key={order.id} order={order} onPrint={() => setPrinting(order.id)} />
      ))}

      {printing && <LabReportSheet orderId={printing} onClose={() => setPrinting(null)} />}
    </div>
  );
}

function LabOrderCard({ order, onPrint }) {
  const reported = order.items?.filter((i) => i.values?.length || i.comments) ?? [];
  const running = order.items?.filter(
    (i) => i.status !== "cancelled" && !i.values?.length && !i.comments) ?? [];

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 pb-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-slate-900">
            {order.order_number}
            <span className="ml-2 text-sm font-normal text-slate-600">{order.status_label}</span>
          </h3>
          <p className="mt-0.5 text-sm text-slate-600">
            Requested {new Date(order.created_at).toLocaleString()}
            {order.requested_by_name && ` by ${order.requested_by_name}`}
            {order.verified_by_name && ` · released by ${order.verified_by_name}`}
          </p>
          {order.clinical_notes && (
            <p className="mt-1 text-sm text-slate-700">Asked for: {order.clinical_notes}</p>
          )}
        </div>
        {reported.length > 0 && (
          <button
            onClick={onPrint}
            className="shrink-0 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
          >
            🖨 Report
          </button>
        )}
      </div>

      {reported.map((item) => (
        <div key={item.id} className="mt-4">
          <p className="flex flex-wrap items-center gap-2 font-medium text-slate-900">
            {item.test_name}
            {/* An unverified result is a figure, not a report. Say so — a
                doctor acting on a provisional value should know it is one. */}
            {item.status === "verified" ? (
              <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
                Released{item.verified_by_name && ` · ${item.verified_by_name}`}
              </span>
            ) : (
              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200">
                Provisional — not yet verified
              </span>
            )}
          </p>

          {item.values?.length > 0 && (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[24rem] text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                    <th className="py-1 pr-3 font-semibold">Parameter</th>
                    <th className="py-1 pr-3 font-semibold">Result</th>
                    <th className="py-1 font-semibold">Reference range</th>
                  </tr>
                </thead>
                <tbody>
                  {[...item.values]
                    .sort((a, b) => a.display_order - b.display_order)
                    .map((value) => (
                      <tr key={value.id} className="border-b border-slate-100 last:border-0">
                        <td className="py-1.5 pr-3 text-slate-700">
                          {value.parameter_name}
                          {value.parameter_group && (
                            <span className="ml-1 text-xs text-slate-500">
                              ({value.parameter_group})
                            </span>
                          )}
                        </td>
                        <td className="py-1.5 pr-3">
                          <span className="font-semibold text-slate-900">
                            {value.value}{value.unit && ` ${value.unit}`}
                          </span>
                          {value.flag && (
                            <span className={`ml-2 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${
                              FLAG_TONE[value.flag] ?? "bg-slate-100 text-slate-600 ring-slate-200"}`}>
                              {value.flag_label}
                            </span>
                          )}
                          {value.comment && (
                            <span className="ml-2 text-xs text-slate-600">{value.comment}</span>
                          )}
                        </td>
                        <td className="py-1.5 text-xs text-slate-600">
                          {value.reference_range || "—"}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}

          {item.comments && (
            <p className="mt-1 text-sm text-slate-700">
              <span className="font-medium">Comment:</span> {item.comments}
            </p>
          )}
          {item.amendments?.length > 0 && (
            <p className="mt-1 text-xs text-amber-800">
              Amended {item.amendments.length} time{item.amendments.length === 1 ? "" : "s"} —
              latest: {item.amendments[0].parameter_name} {item.amendments[0].previous_value} →{" "}
              {item.amendments[0].new_value} ({item.amendments[0].amended_by_name})
            </p>
          )}
        </div>
      ))}

      {order.lab_comments && (
        <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-800">
          <span className="font-medium">Laboratory:</span> {order.lab_comments}
        </p>
      )}

      {running.length > 0 && (
        <p className="mt-3 text-sm text-slate-600">
          Still running: {running.map((i) => i.test_name).join(", ")}.
        </p>
      )}
    </section>
  );
}
