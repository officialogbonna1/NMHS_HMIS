import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { Button, TextLink } from "../components/ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { PrintButton } from "../components/printing.jsx";

// Ultrasound, the eye clinic and procedures, on the chart.
//
// One component for the three of them, driven by config, the same way
// `DepartmentStation` serves all three units: they are the same errand —
// the doctor refers, the unit does the work and writes back — so they read
// the same way here too. The laboratory has a tab of its own instead,
// because a lab result is structured parameters rather than a report.
//
// The data comes from the overview payload the chart already fetches
// (`visits[].routes[]`), so opening this tab costs no extra request and
// cannot show something the Overview disagrees with.

export const REFERRAL_TABS = {
  ultrasound: {
    purpose: "Ultrasound / Imaging",
    title: "Ultrasound",
    icon: "🩻",
    empty: "No scans have been requested for this patient.",
    resultLabel: "Report",
  },
  eye: {
    purpose: "Eye clinic",
    title: "Eye Clinic",
    icon: "👁",
    empty: "This patient has not been referred to the eye clinic.",
    resultLabel: "Findings",
  },
  procedure: {
    purpose: "Procedure",
    title: "Procedures",
    icon: "🩹",
    empty: "No procedures have been requested for this patient.",
    resultLabel: "Notes",
  },
};

const STATUS_TONE = {
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  in_progress: "bg-sky-50 text-sky-800 ring-sky-200",
  queued: "bg-amber-50 text-amber-800 ring-amber-200",
  cancelled: "bg-slate-100 text-slate-500 ring-slate-200",
};

export default function ReferralResultsTab({ patientId, kind }) {
  const config = REFERRAL_TABS[kind];
  const { user } = useAuth();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["patient-overview", String(patientId)],
    queryFn: () => api.get(`/patients/${patientId}/overview/`).then((r) => r.data),
  });

  // Flattened out of the visits, newest first, and tagged with which visit
  // they belong to — "when was that scan?" is usually the real question.
  const routes = useMemo(() => {
    const rows = [];
    for (const visit of data?.visits ?? []) {
      for (const route of visit.routes ?? []) {
        if (route.purpose === config.purpose) rows.push({ ...route, visit });
      }
    }
    return rows.sort((a, b) => new Date(b.visit.created_at) - new Date(a.visit.created_at));
  }, [data, config.purpose]);

  if (isLoading) return <p className="text-slate-600">Loading…</p>;
  // The overview is gated on holding the patient (patients/access.py), so a
  // doctor who is not their doctor gets a refusal here — the same one the
  // Overview tab gives, said the same way.
  if (isError) {
    return (
      <p className="text-red-600">
        Could not load this patient's referrals — you may not be holding this patient.
      </p>
    );
  }

  if (routes.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center">
        <p className="text-3xl">{config.icon}</p>
        <p className="mt-2 font-medium text-slate-800">{config.empty}</p>
        <p className="mt-1 text-sm text-slate-600">
          Send them with <TextLink to="/refer">Refer Patient</TextLink> and the answer comes back here.
        </p>
      </div>
    );
  }

  const answered = routes.filter((r) => r.result);

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-700">
        {answered.length} of {routes.length} answered.
      </p>

      {routes.map((route) => (
        <section key={route.id} className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="font-medium text-slate-900">
                {route.department}
                {route.assigned_to && (
                  <span className="ml-2 text-sm font-normal text-slate-600">
                    · {route.assigned_to}
                  </span>
                )}
              </h3>
              <p className="mt-0.5 text-sm text-slate-600">
                Visit of {new Date(route.visit.created_at).toLocaleDateString()}
                {route.visit.attending_doctor && ` · ${route.visit.attending_doctor}`}
                {route.priority !== "routine" && ` · ${route.priority}`}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${
                STATUS_TONE[route.status] ?? "bg-slate-100 text-slate-600 ring-slate-200"}`}>
                {route.status.replaceAll("_", " ")}
              </span>
              {/* Per referral, because a header button would have to guess
                  which scan you meant. The report once the unit has written
                  one; the request form until then. */}
              <PrintButton
                role={user?.role}
                size="sm"
                documents={route.result
                  ? ["referral_report", "referral_request"]
                  : ["referral_request"]}
                context={{ routeId: route.id }}
              />
            </div>
          </div>

          {route.notes && (
            <p className="mt-3 rounded-lg bg-brand-50/60 px-3 py-2 text-sm text-slate-800">
              <span className="font-medium">Asked for:</span> {route.notes}
            </p>
          )}

          {route.result ? (
            <div className="mt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                {config.resultLabel}
              </p>
              <p className="mt-1 whitespace-pre-wrap text-slate-900">{route.result}</p>
              <p className="mt-1 text-xs text-slate-600">
                {route.result_by && `${route.result_by} · `}
                {route.result_at && new Date(route.result_at).toLocaleString()}
              </p>
              {/* The uploaded report itself lives on the Health Record's
                  Tests & Diagnostics tile, filed there so it survives the
                  visit closing. */}
              <Button variant="link" size="xs" to={`/patients/${patientId}/record`} className="mt-1">
                Any uploaded report is on the Health Record →
              </Button>
            </div>
          ) : (
            <p className="mt-3 text-sm text-slate-600">
              {route.status === "cancelled"
                ? "Cancelled — no result was filed."
                : "No answer back yet."}
            </p>
          )}
        </section>
      ))}
    </div>
  );
}
