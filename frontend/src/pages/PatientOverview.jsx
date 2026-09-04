import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";
import LabReportSheet from "../components/LabReportSheet.jsx";

// The doctor's single view of a patient: everything attached to them, in the
// order it gets read — allergies first because they change what can be
// prescribed, then the latest reading, then the history. Assembled by the
// backend (patients/overview.py) in one request so the chart never renders
// half-loaded.
const currency = (n) => new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(n ?? 0);

const PRESCRIPTION_TONE = {
  pending: "bg-amber-100 text-amber-800",
  dispensed: "bg-emerald-100 text-emerald-700",
  cancelled: "bg-slate-100 text-slate-400 line-through",
};

// The nine health-record tiles, in the order the reference manual reads
// them. One list drives both the summary counts and the cards below, so a
// tile can never appear in one and be missing from the other.
const COUNT_LABEL = {
  allergies: "Allergies",
  conditions: "Conditions",
  medications: "Medications",
  surgeries: "Surgeries",
  vaccinations: "Vaccinations",
  devices: "Devices",
  tests: "Tests",
  family_history: "Family history",
  social_history: "Social history",
};

const APPOINTMENT_TONE = {
  queued: "bg-amber-100 text-amber-800",
  accepted: "bg-blue-100 text-blue-700",
  in_progress: "bg-brand-100 text-brand-700",
  completed: "bg-emerald-100 text-emerald-700",
  cancelled: "bg-slate-100 text-slate-400 line-through",
};

export default function PatientOverview({ patientId }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["patient-overview", String(patientId)],
    queryFn: () => api.get(`/patients/${patientId}/overview/`).then((r) => r.data),
  });

  if (isLoading) return <p className="text-slate-600">Loading the chart…</p>;
  if (error) return <p className="text-red-600">Could not load this patient's overview.</p>;

  const { patient, alerts, counts, latest_vitals: latest, billing } = data;

  return (
    <div className="space-y-6">
      {alerts.length > 0 && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4">
          <h2 className="text-sm font-semibold text-red-800">⚠️ Allergies</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {alerts.map((a) => (
              <span
                key={a.label}
                className={`rounded-full px-3 py-1 text-sm ${a.is_dangerous ? "bg-red-600 text-white" : "bg-white text-red-700 border border-red-200"}`}
                title={a.detail}
              >
                {a.is_dangerous && "⚠ "}{a.label}
              </span>
            ))}
          </div>
        </section>
      )}

      <section className="grid gap-4 md:grid-cols-3">
        <Card title="Patient">
          <Row label="File number" value={patient.file_number} />
          <Row label="Sex" value={patient.sex} />
          <Row label="Age" value={ageLabel(patient)} />
          <Row label="Phone" value={patient.phone_number} />
          <Row label="Address" value={patient.address} />
          <Row label="Registered" value={patient.registered && new Date(patient.registered).toLocaleDateString()} />
          {patient.short_note && <p className="mt-2 border-t pt-2 text-sm text-slate-700">{patient.short_note}</p>}
          {patient.emergency_contact && (
            <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-800">
                Emergency contact
              </p>
              <p className="mt-1 font-medium text-slate-900">
                {patient.emergency_contact.name}
                {patient.emergency_contact.relationship && ` · ${patient.emergency_contact.relationship}`}
              </p>
              <p className="text-sm text-slate-800">
                {[patient.emergency_contact.phone, patient.emergency_contact.alt_phone]
                  .filter(Boolean).join(" · ") || "No number on file"}
              </p>
              {patient.emergency_contact.notes && (
                <p className="mt-1 text-sm text-slate-700">{patient.emergency_contact.notes}</p>
              )}
            </div>
          )}
        </Card>

        <Card
          title="Latest vitals"
          action={<Link to={`/patients/${patientId}/vitals`} className="text-xs text-brand-600 hover:underline">All readings →</Link>}
        >
          {latest ? (
            <>
              <p className="text-xs text-slate-500">
                {new Date(latest.visit_time).toLocaleString()}
                {latest.recorded_by && ` · ${latest.recorded_by}`}
              </p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {latest.readings.map((r) => (
                  <div key={r.label} className="rounded-lg bg-gray-50 px-3 py-2">
                    <p className="text-xs uppercase tracking-wide text-slate-500">{r.label}</p>
                    <p className="text-sm font-semibold">{r.value}<span className="ml-0.5 text-xs font-normal text-slate-500">{r.unit}</span></p>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="text-sm text-slate-500">No vitals recorded yet — nursing will add them.</p>
          )}
        </Card>

        <Card title="Record summary">
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(counts).map(([key, value]) => (
              <div key={key} className={`rounded-lg px-3 py-2 ${value ? "bg-gray-50" : "bg-gray-50/50"}`}>
                <p className="text-xs uppercase tracking-wide text-slate-500">{COUNT_LABEL[key] ?? key}</p>
                <p className={`text-sm font-semibold ${value ? "" : "text-slate-400"}`}>{value}</p>
              </div>
            ))}
          </div>
          {billing && (
            <div className="mt-3 border-t pt-3">
              <Row label="Outstanding" value={currency(billing.outstanding_balance)} />
              <Row label="Unpaid charges" value={billing.unpaid_count} />
            </div>
          )}
        </Card>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <Card title="Active conditions">
          <List
            items={data.conditions}
            empty="None recorded."
            render={(c) => (
              <div>
                <span className="font-medium">{c.name}</span>
                {c.date_diagnosed && <span className="text-slate-500"> · since {dateLabel(c.date_diagnosed)}</span>}
                {c.notes && <p className="text-sm text-slate-600">{c.notes}</p>}
              </div>
            )}
          />
        </Card>
        <Card title="Medications on record">
          <List
            items={data.medications}
            empty="None recorded."
            render={(m) => (
              <div>
                <span className="font-medium">{m.name}</span>
                <span className="text-slate-500"> {[m.strength, m.dose_frequency, m.dose_schedule].filter(Boolean).join(" · ")}</span>
                {m.as_needed && <span className="ml-1 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">as needed</span>}
                {m.notes && <p className="text-sm text-slate-600">{m.notes}</p>}
              </div>
            )}
          />
        </Card>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <Card
          title="Allergies"
          action={<Link to={`/patients/${patientId}/record`} className="text-xs text-brand-600 hover:underline">Edit record →</Link>}
        >
          <List
            items={data.allergies}
            empty="No known allergies recorded."
            render={(a) => (
              <div>
                <span className={`font-medium ${a.is_dangerous ? "text-red-700" : ""}`}>
                  {a.is_dangerous && "⚠ "}{a.name}
                </span>
                {a.reactions?.length > 0 && <span className="text-slate-600"> · {a.reactions.join(", ")}</span>}
                {a.notes && <p className="text-sm text-slate-600">{a.notes}</p>}
              </div>
            )}
          />
        </Card>

        <Card title="Surgical history">
          <List
            items={data.surgeries}
            empty="None recorded."
            render={(s) => (
              <div>
                <span className="font-medium">{s.name}</span>
                {s.surgery_date && <span className="text-slate-500"> · {dateLabel(s.surgery_date)}</span>}
                {s.description && <p className="text-sm text-slate-600">{s.description}</p>}
              </div>
            )}
          />
        </Card>

        <Card title="Vaccinations">
          <List
            items={data.vaccinations}
            empty="None recorded."
            render={(v) => (
              <div>
                <span className="font-medium">{v.name}</span>
                {v.date_administered && <span className="text-slate-500"> · given {dateLabel(v.date_administered)}</span>}
                {v.next_due_date && <p className="text-sm font-medium text-amber-700">Next due {dateLabel(v.next_due_date)}</p>}
                {v.notes && <p className="text-sm text-slate-600">{v.notes}</p>}
              </div>
            )}
          />
        </Card>

        <Card title="Medical devices">
          <List
            items={data.devices}
            empty="None recorded."
            render={(d) => (
              <div>
                <span className="font-medium">{d.name}</span>
                <span className="text-slate-500"> {[d.make, d.model].filter(Boolean).join(" ")}</span>
                {d.device_id && <p className="text-sm text-slate-600">ID {d.device_id}</p>}
                {d.next_update && <p className="text-sm font-medium text-amber-700">Next check {dateLabel(d.next_update)}</p>}
              </div>
            )}
          />
        </Card>

        <Card title="Tests & diagnostics">
          <List
            items={data.tests}
            empty="None on file."
            render={(t) => (
              <div>
                <span className="font-medium">{t.title}</span>
                <span className="text-slate-500"> · {t.test_type} · {dateLabel(t.test_date)}</span>
                {t.impressions && <p className="text-sm text-slate-700">{t.impressions}</p>}
                {t.file_url ? (
                  <a href={t.file_url} target="_blank" rel="noreferrer" className="text-sm font-medium text-brand-600 hover:underline">
                    📎 {t.file_name || "Open document"} →
                  </a>
                ) : (
                  !t.impressions && <p className="text-sm text-slate-500">No document or finding recorded.</p>
                )}
              </div>
            )}
          />
        </Card>

        <Card title="Family history">
          <List
            items={data.family_history}
            empty="None recorded."
            render={(f) => (
              <div>
                <span className="font-medium">{f.relationship}</span>
                {f.is_deceased && <span className="text-slate-500"> · deceased</span>}
                {f.conditions?.length > 0 && <p className="text-slate-700">{f.conditions.join(", ")}</p>}
                {f.notes && <p className="text-sm text-slate-600">{f.notes}</p>}
              </div>
            )}
          />
        </Card>

        <Card title="Social history">
          <List
            items={data.social_history}
            empty="None recorded."
            render={(h) => (
              <div>
                <span className="font-medium">{h.category}</span>
                <span className={h.is_active ? "text-amber-700" : "text-slate-500"}>
                  {" · "}{h.is_active ? "current" : "not current"}
                </span>
                {(h.frequency || h.amount) && (
                  <p className="text-sm text-slate-600">{[h.amount, h.frequency].filter(Boolean).join(" · ")}</p>
                )}
                {h.started_year && <p className="text-sm text-slate-600">Since {h.started_year}</p>}
              </div>
            )}
          />
        </Card>
      </section>

      <Card title="Nursing notes" subtitle="Written at the vitals station before the consultation.">
        <List
          items={data.nursing_notes}
          empty="No nursing notes yet."
          render={(n) => (
            <div>
              <p className="text-xs text-slate-500">{new Date(n.created_at).toLocaleString()} · {n.nurse}</p>
              {n.complaint && <p className="font-medium">{n.complaint}</p>}
              <p className="whitespace-pre-wrap text-slate-800">{n.observation}</p>
            </div>
          )}
        />
      </Card>

      <Card
        title="Consultation notes"
        action={<Link to={`/patients/${patientId}/notes`} className="text-xs text-brand-600 hover:underline">Open notes →</Link>}
      >
        <List
          items={data.consultation_notes}
          empty="No consultation notes yet."
          render={(n) => (
            <div>
              <p className="text-xs text-slate-500">{new Date(n.visit_time).toLocaleString()} · Dr. {n.doctor}</p>
              <p className="font-medium">{n.reason_for_visit}</p>
              {n.diagnosis && <p className="text-slate-800"><span className="text-slate-500">Diagnosis:</span> {n.diagnosis}</p>}
              {n.plan && <p className="text-slate-800"><span className="text-slate-500">Plan:</span> {n.plan}</p>}
            </div>
          )}
        />
      </Card>

      <section className="grid gap-4 md:grid-cols-2">
        <Card
          title="Prescriptions"
          action={<Link to={`/patients/${patientId}/prescribe`} className="text-xs text-brand-600 hover:underline">Prescribe →</Link>}
        >
          <List
            items={data.prescriptions}
            empty="Nothing prescribed yet."
            render={(p) => (
              <div className="flex items-start justify-between gap-2">
                <div>
                  <span className="font-medium">{p.item} ×{p.quantity}</span>
                  {p.dosage_instructions && <p className="text-sm text-slate-600">{p.dosage_instructions}</p>}
                  <p className="text-xs text-slate-500">
                    Dr. {p.doctor} · {new Date(p.created_at).toLocaleDateString()}
                    {p.dispensed_by && ` · dispensed by ${p.dispensed_by}`}
                  </p>
                </div>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${PRESCRIPTION_TONE[p.status]}`}>
                  {p.status_label}
                </span>
              </div>
            )}
          />
        </Card>

        <Card title="Appointments">
          <List
            items={data.appointments}
            empty="No appointments yet."
            render={(a) => (
              <div className="flex items-start justify-between gap-2">
                <div>
                  <span className="font-medium">{a.reason || "Consultation"}</span>
                  <p className="text-xs text-slate-500">
                    Dr. {a.doctor} · queued {new Date(a.created_at).toLocaleDateString()}
                  </p>
                </div>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${APPOINTMENT_TONE[a.status]}`}>
                  {a.status_label}
                </span>
              </div>
            )}
          />
        </Card>
      </section>

      <Card title="Visits & routing" subtitle="Where the front desk has sent this patient.">
        <List
          items={data.visits}
          empty="No visits opened yet."
          render={(v) => (
            <div>
              <p className="font-medium">
                {v.visit_type} · {v.reason || "No reason given"}
                <span className="ml-2 text-xs font-normal text-slate-500">{new Date(v.created_at).toLocaleDateString()} · {v.status}</span>
              </p>
              {v.attending_doctor && <p className="text-sm text-slate-600">Attending: {v.attending_doctor}</p>}
              {v.routes.length > 0 && (
                <ul className="mt-2 space-y-2 text-sm">
                  {v.routes.map((r) => (
                    <li key={r.id}>
                      <span className="text-slate-600">
                        → {r.department} · {r.purpose} · {r.status}
                        {r.assigned_to && ` · ${r.assigned_to}`}
                      </span>
                      {r.notes && <p className="text-slate-600">Asked: {r.notes}</p>}
                      {/* The answer that came back from the unit. */}
                      {r.result && (
                        <div className="mt-1 rounded-lg border border-emerald-200 bg-emerald-50 p-3">
                          <p className="text-xs font-semibold uppercase tracking-wide text-emerald-800">
                            {r.purpose} result
                          </p>
                          <p className="mt-1 whitespace-pre-wrap text-slate-800">{r.result}</p>
                          <p className="mt-1 text-xs text-slate-600">
                            {r.result_by && `${r.result_by} · `}
                            {r.result_at && new Date(r.result_at).toLocaleString()}
                          </p>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        />
      </Card>

      <LabOrdersCard patientId={patientId} />
    </div>
  );
}

// Laboratory results as the lab filed them — parameter, value, unit,
// reference range and flag — rather than only the summary that travels onto
// the routing note. Read straight from `/lab-orders/`, which scopes itself
// to the patients this doctor holds.
function LabOrdersCard({ patientId }) {
  const [printing, setPrinting] = useState(null);
  const { data } = useQuery({
    queryKey: ["lab-orders", String(patientId)],
    queryFn: () => api.get("/lab-orders/", {
      // `detail` brings the values with the list; the counter asking the
      // same endpoint gets the summary, which has none.
      params: { patient: patientId, detail: 1, page_size: 20 },
    })
      .then((r) => r.data.results ?? r.data),
  });

  const orders = (data ?? []).filter((o) => o.status !== "cancelled");

  return (
    <Card
      title="Laboratory results"
      subtitle="What the bench actually reported, against the reference ranges in force when the test was run. Parameters that were not run are simply absent."
    >
      {orders.length === 0 && <p className="text-sm text-slate-600">No laboratory orders yet.</p>}

      <ul className="space-y-4">
        {orders.map((order) => (
          <li key={order.id} className="rounded-lg border border-slate-200 p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium text-slate-800">
                  {order.order_number}
                  <span className="ml-2 text-sm font-normal text-slate-600">
                    {order.status_label}
                  </span>
                </p>
                <p className="text-xs text-slate-600">
                  {new Date(order.created_at).toLocaleString()}
                  {order.verified_by_name && ` · verified by ${order.verified_by_name}`}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setPrinting(order.id)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                🖨 Report
              </button>
            </div>

            {order.items.filter((i) => i.values?.length || i.comments).map((item) => (
              <div key={item.id} className="mt-3">
                <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-800">
                  {item.test_name}
                  {/* An unverified result is not a report. It is shown —
                      a doctor sometimes needs the figure before the bench
                      has signed it off — but never as a finished one. */}
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
                <div className="overflow-x-auto">
                  <table className="mt-1 w-full min-w-[22rem] text-sm">
                    <tbody>
                      {[...(item.values ?? [])]
                        .sort((a, b) => a.display_order - b.display_order)
                        .map((value) => (
                          <tr key={value.id} className="border-b border-slate-100 last:border-0">
                            <td className="py-1 pr-3 text-slate-700">{value.parameter_name}</td>
                            <td className="py-1 pr-3 font-medium text-slate-900">
                              {value.value} {value.unit}
                              {["low", "high", "abnormal", "critical"].includes(value.flag) && (
                                <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-800">
                                  {value.flag_label}
                                </span>
                              )}
                            </td>
                            <td className="py-1 text-xs text-slate-600">
                              {value.reference_range}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                {item.comments && (
                  <p className="mt-1 text-sm text-slate-700">
                    <span className="font-medium">Comment:</span> {item.comments}
                  </p>
                )}
              </div>
            ))}
          </li>
        ))}
      </ul>

      {printing && <LabReportSheet orderId={printing} onClose={() => setPrinting(null)} />}
    </Card>
  );
}

function Card({ title, subtitle, action, children }) {
  return (
    <div className="rounded-xl border bg-white p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

function Row({ label, value }) {
  if (!value && value !== 0) return null;
  return (
    <div className="flex justify-between gap-3 py-0.5 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  );
}

function List({ items, render, empty }) {
  if (!items?.length) return <p className="text-sm text-slate-500">{empty}</p>;
  return (
    <ul className="divide-y text-sm">
      {items.map((item) => <li key={item.id} className="py-2 first:pt-0 last:pb-0">{render(item)}</li>)}
    </ul>
  );
}

function dateLabel(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString();
}

function ageLabel(patient) {
  return patient.age_display ?? null;
}
