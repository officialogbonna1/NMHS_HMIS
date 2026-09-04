import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { Button } from "../components/ui.jsx";

// What this patient has actually been given.
//
// A prescription is a request; only a dispensed one left the shelf. The
// difference matters at the bedside — "she is on metronidazole" is a
// different statement depending on whether the pharmacy ever filled it — so
// the two are separated here rather than listed together as "medications".
//
// Read-only. Prescribing is `/patients/<id>/prescribe`, dispensing is the
// pharmacy counter, and both move stock through `pharmacy/services.py`.

const STATUS_TONE = {
  dispensed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  pending: "bg-amber-50 text-amber-800 ring-amber-200",
  cancelled: "bg-slate-100 text-slate-500 ring-slate-200",
};

export default function PharmacyTab({ patientId, canPrescribe }) {
  const [showCancelled, setShowCancelled] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["prescriptions", String(patientId)],
    queryFn: () => api.get("/prescriptions/", {
      params: { patient: patientId, page_size: 200 },
    }).then((r) => r.data.results ?? r.data),
  });

  const { dispensed, pending, cancelled } = useMemo(() => {
    const rows = [...(data ?? [])].sort(
      (a, b) => new Date(b.created_at) - new Date(a.created_at));
    return {
      dispensed: rows.filter((p) => p.status === "dispensed"),
      pending: rows.filter((p) => p.status === "pending"),
      cancelled: rows.filter((p) => p.status === "cancelled"),
    };
  }, [data]);

  if (isLoading) return <p className="text-slate-600">Loading the pharmacy record…</p>;
  if (isError) return <p className="text-red-600">Could not load this patient's prescriptions.</p>;

  if ((data ?? []).length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center">
        <p className="text-3xl">💊</p>
        <p className="mt-2 font-medium text-slate-800">Nothing prescribed for this patient</p>
        {canPrescribe && (
          <Link
            to={`/patients/${patientId}/prescribe`}
            className="mt-3 inline-block rounded-lg bg-brand-600 px-5 py-2 text-sm font-semibold text-white hover:bg-brand-700"
          >
            + Prescribe
          </Link>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-700">
          <strong className="text-slate-900">{dispensed.length}</strong> dispensed
          {pending.length > 0 && (
            <> · <strong className="text-amber-800">{pending.length}</strong> waiting at the pharmacy</>
          )}
        </p>
        {canPrescribe && (
          <Link
            to={`/patients/${patientId}/prescribe`}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
          >
            + Prescribe
          </Link>
        )}
      </div>

      {/* Waiting first: it is the actionable half. A doctor asking "has she
          had it?" needs to see the answer is "not yet" straight away. */}
      {pending.length > 0 && (
        <Section
          title="Written, not yet dispensed"
          subtitle="The pharmacy has these; nothing has left the shelf."
          rows={pending}
        />
      )}

      <Section
        title="Dispensed"
        subtitle="Actually handed to the patient, with who released it."
        rows={dispensed}
        empty="Nothing has been dispensed yet."
      />

      {cancelled.length > 0 && (
        <div>
          <Button variant="link" size="xs" onClick={() => setShowCancelled((v) => !v)}>
            {showCancelled ? "Hide" : `Show ${cancelled.length}`} cancelled
          </Button>
          {showCancelled && <Section title="Cancelled" rows={cancelled} />}
        </div>
      )}
    </div>
  );
}

function Section({ title, subtitle, rows, empty }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
      <h3 className="font-semibold text-slate-900">{title}</h3>
      {subtitle && <p className="mt-0.5 text-sm text-slate-600">{subtitle}</p>}

      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-slate-600">{empty}</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[34rem] text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="py-1 pr-3 font-semibold">Drug</th>
                <th className="py-1 pr-3 font-semibold">Qty</th>
                <th className="py-1 pr-3 font-semibold">Directions</th>
                <th className="py-1 pr-3 font-semibold">Prescribed</th>
                <th className="py-1 font-semibold">Dispensed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id} className="border-b border-slate-100 last:border-0">
                  <td className="py-2 pr-3">
                    <span className={`font-medium ${
                      p.status === "cancelled" ? "text-slate-500 line-through" : "text-slate-900"}`}>
                      {p.item_name}
                    </span>
                    <span className={`ml-2 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${
                      STATUS_TONE[p.status] ?? "bg-slate-100 text-slate-600 ring-slate-200"}`}>
                      {p.status}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-slate-800">
                    {p.quantity}{p.item_unit ? ` ${p.item_unit}` : ""}
                  </td>
                  <td className="py-2 pr-3 text-slate-700">{p.dosage_instructions || "—"}</td>
                  <td className="py-2 pr-3 text-slate-700">
                    {new Date(p.created_at).toLocaleDateString()}
                    {p.doctor_name && <span className="block text-xs text-slate-600">{p.doctor_name}</span>}
                  </td>
                  <td className="py-2 text-slate-700">
                    {p.dispensed_at ? (
                      <>
                        {new Date(p.dispensed_at).toLocaleDateString()}
                        {p.dispensed_by_name && (
                          <span className="block text-xs text-slate-600">{p.dispensed_by_name}</span>
                        )}
                      </>
                    ) : p.status === "cancelled" ? (
                      <span className="text-xs text-slate-600">{p.cancelled_reason || "—"}</span>
                    ) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
