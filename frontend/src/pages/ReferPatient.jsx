import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import PatientPicker, { patientLabel } from "../components/PatientPicker.jsx";
import { useToast } from "../components/Toaster.jsx";

// The doctor's hand-off, mirroring nursing's Send to Doctor: pick the
// patient, pick where they are going, and the unit is notified and sees the
// patient in their queue.
//
// The consultation stays open — a referral is work done during it, not the
// end of it, and the patient usually comes back with a result. Billing is a
// separate act: the counter raises the charge from the Billing Catalog, so
// money is only ever created by the roles that collect it.

const DESTINATIONS = [
  { purpose: "laboratory", label: "Laboratory", icon: "🧫",
    blurb: "Blood work, urinalysis, swabs — anything the lab runs." },
  { purpose: "ultrasound", label: "Ultrasound / Imaging", icon: "🩻",
    blurb: "Scans and imaging." },
  { purpose: "eye", label: "Eye clinic", icon: "👁",
    blurb: "Refraction, eye pressure, and the eye doctors." },
  { purpose: "procedure", label: "Procedure", icon: "🩹",
    blurb: "Dressings, injections and minor procedures." },
];

const PRIORITIES = [
  ["routine", "Routine"],
  ["urgent", "Urgent"],
  ["emergency", "Emergency"],
];

// Which tests the doctor wants, when the referral is going to the lab.
// The list is the catalogue's — never a hard-coded one — so a test the lab
// added this morning is orderable this afternoon.
function LabTestChooser({ chosen, onToggle }) {
  const [term, setTerm] = useState("");

  const { data: tests } = useQuery({
    queryKey: ["lab-tests", term],
    queryFn: () => api.get("/lab-tests/", {
      params: { ...(term.trim() ? { search: term.trim() } : {}), page_size: 300 },
    }).then((r) => r.data.results ?? r.data),
    staleTime: 60000,
  });

  const { data: panels } = useQuery({
    queryKey: ["lab-panels"],
    queryFn: () => api.get("/lab-panels/").then((r) => r.data.results ?? r.data),
    staleTime: 300000,
  });

  const grouped = useMemo(() => {
    const groups = new Map();
    for (const test of tests ?? []) {
      if (!groups.has(test.category_label)) groups.set(test.category_label, []);
      groups.get(test.category_label).push(test);
    }
    return [...groups.entries()];
  }, [tests]);

  return (
    <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/60 p-4">
      <p className="text-sm font-medium text-slate-800">Which tests?</p>
      <p className="mt-0.5 text-sm text-slate-600">
        These, and only these, go onto the lab's worklist — the catalogue is what is available,
        not what gets ordered. Each one raises a charge on the patient at the price shown, which
        the front desk or the cash desk collects.
      </p>

      {(panels?.length ?? 0) > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {panels.map((panel) => (
            <button
              key={panel.id}
              type="button"
              title={panel.description}
              onClick={() => {
                const adding = panel.test_details.filter((t) => !chosen.some((c) => c.id === t.id));
                const total = adding.reduce((sum, t) => sum + Number(t.charge_amount ?? 0), 0);
                // A panel is several tests and several charges. Asking first
                // is the difference between ordering a profile and ordering
                // twelve tests by accident.
                if (adding.length && window.confirm(
                  `Add all ${adding.length} tests in "${panel.name}"?\n\n`
                  + adding.map((t) => `• ${t.name}`).join("\n")
                  + `\n\nThis adds ₦${total.toLocaleString()} to the patient's bill.`)) {
                  adding.forEach(onToggle);
                }
              }}
              className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs font-semibold text-brand-700 transition hover:bg-brand-100"
            >
              + {panel.name}
            </button>
          ))}
        </div>
      )}

      {chosen.length > 0 && (
        <div className="mt-3">
          <div className="flex flex-wrap gap-2">
            {chosen.map((test) => (
              <button
                key={test.id}
                type="button"
                onClick={() => onToggle(test)}
                className="rounded-full bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white"
              >
                {test.name} ✕
              </button>
            ))}
          </div>
          <p className="mt-2 text-sm font-medium text-slate-800">
            {chosen.length} test{chosen.length === 1 ? "" : "s"} · ₦
            {chosen.reduce((sum, t) => sum + Number(t.charge_amount ?? 0), 0).toLocaleString()}{" "}
            will be added to the patient's bill.
          </p>
        </div>
      )}

      <input
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        placeholder="Search the catalogue — FBC, MP, urinalysis…"
        className="mt-3 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-base text-slate-900 sm:py-2 sm:text-sm"
      />
      <div className="mt-3 max-h-56 overflow-y-auto pr-1">
        {grouped.map(([category, rows]) => (
          <div key={category} className="mb-3">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              {category}
            </p>
            <div className="flex flex-wrap gap-2">
              {rows.map((test) => {
                const picked = chosen.some((c) => c.id === test.id);
                return (
                  <button
                    key={test.id}
                    type="button"
                    onClick={() => onToggle(test)}
                    className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                      picked
                        ? "border-brand-500 bg-brand-50 text-brand-800"
                        : "border-slate-300 bg-white text-slate-800 hover:border-brand-400"}`}
                  >
                    {picked ? "✓ " : ""}{test.name}
                    {Number(test.charge_amount) > 0 && (
                      <span className={picked ? "text-brand-700" : "text-slate-600"}>
                        {" "}· ₦{Number(test.charge_amount).toLocaleString()}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ReferPatient() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [patient, setPatient] = useState(null);
  const [purpose, setPurpose] = useState("");
  const [priority, setPriority] = useState("routine");
  const [notes, setNotes] = useState("");
  const [sent, setSent] = useState(null);
  const [error, setError] = useState(null);
  const [labTests, setLabTests] = useState([]);

  const refer = useMutation({
    // Two calls on purpose: the referral is the existing workflow and must
    // happen whatever else does, then the named tests are put on the order
    // it created. A catalogue hiccup must not swallow the referral.
    mutationFn: async () => {
      const { data: route } = await api.post("/patient-routes/refer/", {
        patient: patient.id, purpose, priority, notes,
      });
      if (purpose === "laboratory" && labTests.length) {
        const { data: order } = await api.post("/lab-orders/for-route/", { route: route.id });
        await api.post(`/lab-orders/${order.id}/add-tests/`,
          { tests: labTests.map((t) => t.id) });
      }
      return route;
    },
    onSuccess: (route) => {
      const where = DESTINATIONS.find((d) => d.purpose === purpose);
      // The server says who it actually reached. Silence is deliberate for
      // doctor-work nobody is holding, but the referrer has to know.
      if (route?.notice) {
        showToast({ title: "Nobody notified", message: route.notice, tone: "error" });
      }
      queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["patient-overview", String(patient.id)] });
      showToast({
        title: "Referral sent",
        message: `${patientLabel(patient)} is now waiting on ${where?.label ?? purpose}.`,
      });
      setSent({ patient, where: where?.label ?? purpose });
      setPatient(null); setPurpose(""); setPriority("routine"); setNotes("");
      setLabTests([]); setError(null);
    },
    onError: (err) => setError(readError(err, "Could not send this referral.")),
  });

  const ready = patient && purpose && !refer.isPending;

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-5 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Refer a patient</h1>
        <p className="mt-1 text-sm text-slate-700">
          Send a patient on to the lab, imaging, the eye clinic or for a procedure. The unit is
          notified and the patient appears in their queue. Your consultation stays open.
        </p>
      </div>

      {sent && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <span>{patientLabel(sent.patient)} is with {sent.where}.</span>
          <button onClick={() => setSent(null)} className="text-sm underline">Dismiss</button>
        </div>
      )}

      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-medium text-slate-800">1. Which patient?</h2>
        <p className="mb-3 text-sm text-slate-700">
          The patients on your list — anyone routed to you or attending under your name.
        </p>
        <PatientPicker
          value={patient}
          onChange={(p) => { setPatient(p); setError(null); }}
          placeholder="Search or pick a patient…"
        />
      </section>

      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-medium text-slate-800">2. Where are they going?</h2>
        <p className="mb-3 text-sm text-slate-700">
          Everyone on duty in that unit is notified — the work is not held up waiting for one named
          person.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          {DESTINATIONS.map((d) => (
            <button
              key={d.purpose}
              onClick={() => { setPurpose(d.purpose); setError(null); }}
              disabled={!patient}
              className={`rounded-xl border p-4 text-left transition disabled:opacity-50 ${
                purpose === d.purpose
                  ? "border-brand-500 bg-brand-50 ring-1 ring-brand-200"
                  : "border-slate-200 hover:border-brand-300 hover:bg-brand-50/40"
              }`}
            >
              <p className="font-medium text-slate-800">
                <span className="mr-2">{d.icon}</span>{d.label}
              </p>
              <p className="mt-1 text-sm text-slate-700">{d.blurb}</p>
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-medium text-slate-800">3. What are you asking for?</h2>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Priority</label>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              disabled={!patient}
              className="w-full rounded-md border border-slate-300 px-3 py-2 disabled:bg-slate-100"
            >
              {PRIORITIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-sm font-medium text-slate-700">Clinical note</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={!patient}
              rows={3}
              placeholder="What you are looking for, and why — e.g. 'RUQ pain, rule out gallstones'"
              className="w-full rounded-md border border-slate-300 px-3 py-2 disabled:bg-slate-100"
            />
            <p className="mt-1 text-sm text-slate-600">
              This is what the unit reads. A request with no note is one nobody can query.
            </p>
          </div>
        </div>

        {purpose === "laboratory" && patient && (
          <LabTestChooser
            chosen={labTests}
            onToggle={(test) => setLabTests((current) => (
              current.some((t) => t.id === test.id)
                ? current.filter((t) => t.id !== test.id)
                : [...current, test]
            ))}
          />
        )}
      </section>

      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => { setError(null); refer.mutate(); }}
          disabled={!ready}
          className="rounded-md bg-brand-600 px-6 py-2.5 font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {refer.isPending ? "Sending…" : "Send referral"}
        </button>
        <Link to="/queue" className="text-sm text-brand-600 hover:underline">My queue</Link>
        <span className="text-sm text-slate-700">
          {purpose === "laboratory" && labTests.length > 0
            ? "The charge is raised with the order; the patient settles it at Reception or the cash desk."
            : "The counter bills the service separately."}
        </span>
      </div>
    </div>
  );
}
