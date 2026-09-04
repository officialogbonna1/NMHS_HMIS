import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";

// Is this patient on a ward, and where.
//
// The one question a doctor opening a chart needs answered before anything
// else — you cannot go and see somebody without knowing which bed they are
// in — and until now it lived only on the Admissions board, which is
// organised by ward rather than by patient.
//
// Read-only here. Admitting, moving a bed and discharging all happen on
// `/admissions`, where the bed is held under `select_for_update` so two
// patients cannot be put in one.

export default function AdmissionTab({ patientId }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admissions", String(patientId)],
    queryFn: () => api.get("/admissions/", { params: { patient: patientId } })
      .then((r) => r.data.results ?? r.data),
  });

  if (isLoading) return <p className="text-slate-600">Loading admissions…</p>;
  if (isError) return <p className="text-red-600">Could not load this patient's admissions.</p>;

  const admissions = data ?? [];
  const current = admissions.find((a) => a.status === "admitted");
  const past = admissions.filter((a) => a !== current);

  return (
    <div className="space-y-4">
      {current ? (
        <section className="rounded-xl border-2 border-emerald-300 bg-emerald-50 p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-emerald-800">
            Currently admitted
          </p>
          {/* Ward and bed as the largest thing on the panel: it is what
              somebody reads before walking to the ward. */}
          <p className="mt-1 text-2xl font-bold tracking-tight text-emerald-900">
            {current.ward_name} · Bed {current.bed_number}
          </p>
          <div className="mt-3 grid gap-x-8 gap-y-1 text-sm sm:grid-cols-2">
            <Field label="Admitted" value={new Date(current.admitted_at).toLocaleString()} />
            <Field label="Under" value={current.attending_doctor_name || "—"} />
            <Field label="Days on the ward" value={daysSince(current.admitted_at)} />
            <Field label="Diagnosis" value={current.diagnosis || "—"} />
          </div>
          <Link
            to="/admissions"
            className="mt-4 inline-block rounded-lg border border-emerald-300 bg-white px-4 py-2 text-sm font-medium text-emerald-800 transition hover:bg-emerald-100"
          >
            Open the bed board →
          </Link>
        </section>
      ) : (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <p className="font-medium text-slate-800">Not currently admitted</p>
          <p className="mt-1 text-sm text-slate-600">
            {admissions.length > 0
              ? "This patient has been on a ward before — the history is below."
              : "This patient has never been admitted."}
          </p>
          <Link to="/admissions" className="mt-3 inline-block text-sm text-brand-600 hover:underline">
            Admit from the bed board →
          </Link>
        </section>
      )}

      {past.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <h3 className="mb-3 font-semibold text-slate-900">Previous admissions</h3>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[30rem] text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-1 pr-3 font-semibold">Ward / bed</th>
                  <th className="py-1 pr-3 font-semibold">Admitted</th>
                  <th className="py-1 pr-3 font-semibold">Discharged</th>
                  <th className="py-1 pr-3 font-semibold">Stay</th>
                  <th className="py-1 font-semibold">Diagnosis</th>
                </tr>
              </thead>
              <tbody>
                {past.map((a) => (
                  <tr key={a.id} className="border-b border-slate-100 last:border-0">
                    <td className="py-1.5 pr-3 text-slate-800">
                      {a.ward_name} · {a.bed_number}
                    </td>
                    <td className="py-1.5 pr-3 text-slate-700">
                      {new Date(a.admitted_at).toLocaleDateString()}
                    </td>
                    <td className="py-1.5 pr-3 text-slate-700">
                      {a.discharged_at
                        ? new Date(a.discharged_at).toLocaleDateString()
                        : a.status === "cancelled" ? "Cancelled" : "—"}
                    </td>
                    <td className="py-1.5 pr-3 text-slate-700">
                      {a.discharged_at ? stayLength(a.admitted_at, a.discharged_at) : "—"}
                    </td>
                    <td className="py-1.5 text-slate-700">{a.diagnosis || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function Field({ label, value }) {
  return (
    <p className="flex gap-2">
      <span className="w-32 shrink-0 text-slate-700">{label}:</span>
      <span className="font-medium text-slate-900">{value}</span>
    </p>
  );
}

function daysSince(from) {
  const days = Math.floor((Date.now() - new Date(from)) / 86400000);
  if (days < 1) return "Today";
  return `${days} day${days === 1 ? "" : "s"}`;
}

function stayLength(from, to) {
  const days = Math.max(0, Math.round((new Date(to) - new Date(from)) / 86400000));
  return `${days} day${days === 1 ? "" : "s"}`;
}
