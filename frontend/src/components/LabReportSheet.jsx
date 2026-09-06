import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { patientNumber } from "./patientIdentity.js";
import PrintSheet, { SheetHeader, Field } from "./PrintSheet.jsx";

// The laboratory report, printed the way every other document here is —
// browser printing into `#print-area`, black on white, no library.
//
// The one rule that shapes it: **only parameters that have a result appear.**
// The server's `/lab-orders/<id>/report/` returns exactly those rows, so a
// Full Blood Count where three indices were run prints three lines. A page
// of empty parameters is how a reader stops trusting the page.

export default function LabReportSheet({ orderId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["lab-report", orderId],
    queryFn: () => api.get(`/lab-orders/${orderId}/report/`).then((r) => r.data),
  });

  if (isLoading || isError || !data) {
    return (
      <PrintSheet title="Laboratory report" onClose={onClose}>
        <p className="text-slate-700">
          {isError ? "Could not load this report." : "Loading the report…"}
        </p>
      </PrintSheet>
    );
  }

  const { order, patient, sections } = data;
  const released = Boolean(order.verified_at);

  return (
    <PrintSheet title={`Laboratory report — ${order.order_number}`} onClose={onClose}>
      <SheetHeader
        documentTitle="Laboratory Report"
        reference={order.order_number}
        date={new Date(order.created_at).toLocaleString()}
      />

      {/* An unverified report is a draft, and it must say so on the paper —
          a printout that leaves the building without this is a result nobody
          has signed for. */}
      {!released && (
        <p className="mb-5 border-2 border-slate-800 px-4 py-2 text-center text-sm font-bold uppercase tracking-[0.2em] text-slate-900">
          Provisional — not yet verified
        </p>
      )}

      <section className="mb-5 grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
        <Field label="Patient" value={patient.name} strong />
        <Field label="Patient No." value={patientNumber(patient)} strong />
        <Field label="Age" value={patient.age || "—"} />
        <Field label="Sex" value={patient.sex || "—"} />
        <Field label="Encounter" value={order.visit ? `Visit #${order.visit}` : "—"} />
        <Field label="Order number" value={order.order_number} />
        <Field label="Specimen ID" value={order.specimen_id || "—"} />
        <Field label="Priority" value={order.priority} />
        <Field label="Requested by" value={order.requested_by || "—"} />
        <Field
          label="Collected"
          value={order.specimen_collected_at
            ? new Date(order.specimen_collected_at).toLocaleString() : "—"}
        />
      </section>

      {order.clinical_notes && (
        <p className="mb-5 border-l-2 border-slate-800 pl-3 text-sm text-slate-900">
          <span className="font-semibold">Clinical details: </span>{order.clinical_notes}
        </p>
      )}

      {sections.length === 0 && (
        <p className="py-8 text-center text-slate-700">
          No results have been entered against this order yet.
        </p>
      )}

      {sections.map((section) => (
        <section key={section.test_code} className="mb-6 break-inside-avoid">
          <h2 className="mb-2 border-b-2 border-slate-800 pb-1 text-sm font-bold uppercase tracking-wide text-slate-900">
            {section.test_name}
            {section.specimen_type && (
              <span className="ml-2 font-normal normal-case tracking-normal text-slate-700">
                · {section.specimen_type}
              </span>
            )}
          </h2>

          <ResultTable values={section.values} />

          {section.comments && (
            <p className="mt-2 text-sm text-slate-900">
              <span className="font-semibold">Comment: </span>{section.comments}
            </p>
          )}
          {section.performed_by && (
            <p className="mt-1 text-xs text-slate-700">
              Tested by {section.performed_by}
              {section.performed_at && ` · ${new Date(section.performed_at).toLocaleString()}`}
            </p>
          )}
        </section>
      ))}

      {order.lab_comments && (
        <section className="mb-6 border border-slate-400 px-4 py-3">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-700">
            Laboratory comments
          </p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-slate-900">{order.lab_comments}</p>
        </section>
      )}

      <section className="mt-8 grid grid-cols-2 gap-8 border-t border-slate-400 pt-4 text-sm">
        <div>
          <p className="text-slate-700">Result entered</p>
          <p className="font-semibold text-slate-900">{order.entered_by || "—"}</p>
          <p className="text-slate-700">
            {order.entered_at ? new Date(order.entered_at).toLocaleString() : "—"}
          </p>
        </div>
        <div>
          <p className="text-slate-700">Verified</p>
          <p className="font-semibold text-slate-900">{order.verified_by || "Not yet verified"}</p>
          <p className="text-slate-700">
            {order.verified_at ? new Date(order.verified_at).toLocaleString() : "—"}
          </p>
        </div>
      </section>

      <footer className="mt-8 border-t border-slate-300 pt-4 text-sm text-slate-700">
        <p>
          Report status: <span className="font-semibold text-slate-900">{order.status}</span>.
          Laboratory results support a clinical judgement; they do not make one. Interpret
          alongside the patient's history and examination.
        </p>
        <div className="mt-8 flex justify-between gap-8">
          <div className="flex-1 border-t border-slate-500 pt-1">Laboratory Scientist</div>
          <div className="flex-1 border-t border-slate-500 pt-1">Verified by</div>
        </div>
      </footer>
    </PrintSheet>
  );
}

function ResultTable({ values }) {
  // Grouped headings (Physical / Chemical / Microscopy) only where the
  // catalogue set them — a test with no groups prints as one plain table.
  const groups = [];
  for (const value of values) {
    const key = value.group || "";
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.rows.push(value);
    else groups.push({ key, rows: [value] });
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-slate-400 text-left text-xs uppercase tracking-wide text-slate-700">
          <th className="py-1 pr-3 font-semibold">Parameter</th>
          <th className="py-1 pr-3 font-semibold">Result</th>
          <th className="py-1 pr-3 font-semibold">Unit</th>
          <th className="py-1 font-semibold">Reference range</th>
        </tr>
      </thead>
      <tbody>
        {groups.map((group, index) => (
          <Fragment key={group.key || index}>
            {group.key && (
              <tr>
                <td colSpan={4} className="pb-1 pt-3 text-xs font-bold uppercase tracking-wide text-slate-900">
                  {group.key}
                </td>
              </tr>
            )}
            {group.rows.map((value) => (
              <tr key={value.parameter} className="border-b border-slate-200 last:border-0">
                <td className="py-1 pr-3 text-slate-900">{value.parameter}</td>
                <td className="py-1 pr-3 font-semibold text-slate-900">
                  {value.value}
                  {/* Printed as a word, not a colour: a coloured panel is
                      either dropped by the driver or burns toner. */}
                  {["low", "high", "abnormal", "critical"].includes(value.flag) && (
                    <span className="ml-2 text-xs font-bold uppercase">
                      ({value.flag_label})
                    </span>
                  )}
                  {value.comment && (
                    <span className="ml-2 text-xs font-normal text-slate-700">
                      {value.comment}
                    </span>
                  )}
                </td>
                <td className="py-1 pr-3 text-slate-900">{value.unit || "—"}</td>
                <td className="py-1 text-slate-900">{value.reference_range || "—"}</td>
              </tr>
            ))}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
