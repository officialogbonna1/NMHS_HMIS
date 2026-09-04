import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import PatientPicker, { patientLabel } from "../components/PatientPicker.jsx";
import { useToast } from "../components/Toaster.jsx";

// The ward: who is in which bed, admitting, moving somebody, and sending
// them home. The API for all of this already existed (apps/inpatient) with
// no page in front of it — the admin dashboard counted admissions and
// linked to a route that was never built.

export default function Admissions() {
  const [tab, setTab] = useState("beds");

  const wards = useQuery({
    queryKey: ["wards"],
    queryFn: () => api.get("/wards/", { params: { page_size: 200 } }).then((r) => r.data.results ?? r.data),
  });
  const beds = useQuery({
    queryKey: ["beds"],
    queryFn: () => api.get("/beds/", { params: { page_size: 500 } }).then((r) => r.data.results ?? r.data),
  });
  const admissions = useQuery({
    queryKey: ["admissions"],
    queryFn: () => api.get("/admissions/", { params: { status: "admitted", page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });

  const stats = useMemo(() => {
    const all = beds.data ?? [];
    const active = all.filter((b) => b.is_active);
    return {
      beds: active.length,
      occupied: active.filter((b) => b.occupied).length,
      free: active.filter((b) => !b.occupied).length,
    };
  }, [beds.data]);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-5 md:p-8">
      <header>
        <h1 className="text-2xl font-semibold">Admissions</h1>
        <p className="mt-1 text-sm text-slate-700">
          Who is on the ward, and in which bed. Admit a patient, move them, or discharge them.
        </p>
        <p className="mt-2 text-sm text-slate-700">
          <strong>{stats.occupied}</strong> of <strong>{stats.beds}</strong> beds occupied ·{" "}
          <strong className={stats.free === 0 ? "text-red-600" : ""}>{stats.free}</strong> free
        </p>
      </header>

      <div className="flex w-fit overflow-hidden rounded-xl border bg-white text-sm">
        {[["beds", "Beds"], ["admitted", "On the ward"], ["admit", "Admit a patient"]].map(([value, label]) => (
          <button
            key={value}
            onClick={() => setTab(value)}
            className={`px-4 py-2 font-medium ${tab === value ? "bg-brand-600 text-white" : "text-slate-700 hover:bg-slate-50"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "beds" && <BedBoard wards={wards.data} beds={beds.data} loading={beds.isLoading} />}
      {tab === "admitted" && <OnTheWard admissions={admissions.data} beds={beds.data} loading={admissions.isLoading} />}
      {tab === "admit" && <AdmitForm beds={beds.data} onDone={() => setTab("admitted")} />}
    </div>
  );
}

function BedBoard({ wards, beds, loading }) {
  if (loading) return <p className="text-sm text-slate-700">Loading the ward…</p>;
  if (!wards?.length) {
    return (
      <div className="rounded-xl border bg-white p-10 text-center text-slate-700">
        <p className="text-3xl">🛏</p>
        <p className="mt-2 font-medium">No wards set up yet</p>
        <p className="mt-1 text-sm">An admin adds wards and beds in the Django admin.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {wards.map((ward) => {
        const wardBeds = (beds ?? []).filter((b) => b.ward === ward.id);
        return (
          <section key={ward.id} className="rounded-xl border bg-white p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-medium text-slate-800">{ward.name}</h2>
              <span className="text-sm text-slate-700">
                {ward.occupied_count} / {ward.bed_count} occupied
              </span>
            </div>
            {wardBeds.length === 0 ? (
              <p className="text-sm text-slate-600">No beds in this ward yet.</p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
                {wardBeds.map((bed) => (
                  <div
                    key={bed.id}
                    className={`rounded-lg border p-3 ${
                      !bed.is_active ? "border-slate-200 bg-slate-50 opacity-60"
                        : bed.occupied ? "border-amber-300 bg-amber-50"
                        : "border-emerald-300 bg-emerald-50"
                    }`}
                  >
                    <p className="font-semibold text-slate-800">Bed {bed.number}</p>
                    <p className="mt-0.5 text-sm text-slate-700">
                      {!bed.is_active ? "Out of service" : bed.occupied ? bed.occupant : "Free"}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function OnTheWard({ admissions, beds, loading }) {
  if (loading) return <p className="text-sm text-slate-700">Loading…</p>;
  if (!admissions?.length) {
    return (
      <div className="rounded-xl border bg-white p-10 text-center text-slate-700">
        <p className="text-3xl">🛏</p>
        <p className="mt-2 font-medium">Nobody is admitted</p>
        <p className="mt-1 text-sm">Admissions appear here while the patient is on the ward.</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {admissions.map((admission) => (
        <AdmissionRow key={admission.id} admission={admission} beds={beds} />
      ))}
    </div>
  );
}

function AdmissionRow({ admission, beds }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [panel, setPanel] = useState(null);          // "move" | "discharge" | null
  const [toBed, setToBed] = useState("");
  const [reason, setReason] = useState("");
  const [summary, setSummary] = useState({ diagnosis: "", summary: "", instructions: "", follow_up: "" });
  const [error, setError] = useState(null);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["admissions"] });
    queryClient.invalidateQueries({ queryKey: ["beds"] });
    queryClient.invalidateQueries({ queryKey: ["wards"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const move = useMutation({
    mutationFn: () => api.post("/bed-transfers/", {
      admission: admission.id, to_bed: Number(toBed), reason,
    }),
    onSuccess: () => {
      refresh(); setPanel(null); setToBed(""); setReason(""); setError(null);
      showToast({ title: "Moved", message: `${admission.patient_name} is in a new bed.` });
    },
    onError: (err) => setError(readError(err, "Could not move this patient.")),
  });

  const discharge = useMutation({
    mutationFn: () => api.post("/discharges/", {
      admission: admission.id, ...summary, follow_up: summary.follow_up || null,
    }),
    onSuccess: () => {
      refresh(); setPanel(null); setError(null);
      showToast({ title: "Discharged", message: `${admission.patient_name} has gone home.` });
    },
    onError: (err) => setError(readError(err, "Could not discharge this patient.")),
  });

  const freeBeds = (beds ?? []).filter((b) => b.is_active && !b.occupied);

  return (
    <article className="rounded-xl border bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-medium text-slate-800">{admission.patient_name}</p>
          <p className="text-sm text-slate-700">
            {admission.patient_file_number} · {admission.ward_name} bed {admission.bed_number}
            {admission.attending_doctor_name && ` · Dr. ${admission.attending_doctor_name}`}
          </p>
          <p className="text-sm text-slate-600">
            Admitted {new Date(admission.admitted_at).toLocaleString()}
          </p>
          {admission.diagnosis && <p className="mt-1 text-sm text-slate-800">{admission.diagnosis}</p>}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setPanel(panel === "move" ? null : "move")}
            className="rounded-full border px-3 py-1.5 text-sm font-medium hover:bg-slate-50"
          >
            Move bed
          </button>
          <button
            onClick={() => setPanel(panel === "discharge" ? null : "discharge")}
            className="rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-100"
          >
            Discharge
          </button>
        </div>
      </div>

      {panel === "move" && (
        <div className="mt-3 grid gap-3 border-t pt-3 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Move to *</label>
            <select
              value={toBed}
              onChange={(e) => setToBed(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">Select a free bed…</option>
              {freeBeds.map((b) => (
                <option key={b.id} value={b.id}>{b.ward_name} — bed {b.number}</option>
              ))}
            </select>
            {freeBeds.length === 0 && <p className="mt-1 text-sm text-amber-700">No free beds.</p>}
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Why</label>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Closer to the nurses' station"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div className="sm:col-span-2">
            <button
              onClick={() => move.mutate()}
              disabled={!toBed || move.isPending}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              {move.isPending ? "Moving…" : "Move"}
            </button>
          </div>
        </div>
      )}

      {panel === "discharge" && (
        <div className="mt-3 grid gap-3 border-t pt-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className="mb-1 block text-sm font-medium text-slate-700">Diagnosis *</label>
            <input
              value={summary.diagnosis}
              onChange={(e) => setSummary({ ...summary, diagnosis: e.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-sm font-medium text-slate-700">Summary *</label>
            <textarea
              rows={3}
              value={summary.summary}
              onChange={(e) => setSummary({ ...summary, summary: e.target.value })}
              placeholder="How the admission went"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-sm font-medium text-slate-700">Instructions home</label>
            <textarea
              rows={2}
              value={summary.instructions}
              onChange={(e) => setSummary({ ...summary, instructions: e.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Follow-up date</label>
            <input
              type="date"
              value={summary.follow_up}
              onChange={(e) => setSummary({ ...summary, follow_up: e.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div className="sm:col-span-2">
            <button
              onClick={() => discharge.mutate()}
              disabled={!summary.diagnosis.trim() || !summary.summary.trim() || discharge.isPending}
              className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {discharge.isPending ? "Discharging…" : "Discharge and free the bed"}
            </button>
          </div>
        </div>
      )}

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </article>
  );
}

function AdmitForm({ beds, onDone }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [patient, setPatient] = useState(null);
  const [bedId, setBedId] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [diagnosis, setDiagnosis] = useState("");
  const [error, setError] = useState(null);

  const { data: doctors } = useQuery({
    queryKey: ["users", "doctor"],
    queryFn: () => api.get("/users/", { params: { role: "doctor" } }).then((r) => r.data.results ?? r.data),
  });

  const admit = useMutation({
    mutationFn: () => api.post("/admissions/", {
      patient: patient.id,
      bed: Number(bedId),
      attending_doctor: doctorId ? Number(doctorId) : null,
      diagnosis,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admissions"] });
      queryClient.invalidateQueries({ queryKey: ["beds"] });
      queryClient.invalidateQueries({ queryKey: ["wards"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      showToast({ title: "Admitted", message: `${patientLabel(patient)} is on the ward.` });
      setPatient(null); setBedId(""); setDoctorId(""); setDiagnosis(""); setError(null);
      onDone();
    },
    onError: (err) => setError(readError(err, "Could not admit this patient.")),
  });

  const freeBeds = (beds ?? []).filter((b) => b.is_active && !b.occupied);

  return (
    <section className="rounded-xl border bg-white p-5">
      <h2 className="font-medium text-slate-800">Admit a patient</h2>
      <p className="mb-4 text-sm text-slate-700">
        The bed is held the moment you admit — two people cannot be put in the same one.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className="mb-1 block text-sm font-medium text-slate-700">Patient *</label>
          <PatientPicker value={patient} onChange={setPatient} />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">Bed *</label>
          <select
            value={bedId}
            onChange={(e) => setBedId(e.target.value)}
            disabled={!patient}
            className="w-full rounded-md border border-slate-300 px-3 py-2 disabled:bg-slate-100"
          >
            <option value="">Select a free bed…</option>
            {freeBeds.map((b) => (
              <option key={b.id} value={b.id}>{b.ward_name} — bed {b.number}</option>
            ))}
          </select>
          {freeBeds.length === 0 && (
            <p className="mt-1 text-sm text-red-700">No free beds on any ward.</p>
          )}
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">Attending doctor</label>
          <select
            value={doctorId}
            onChange={(e) => setDoctorId(e.target.value)}
            disabled={!patient}
            className="w-full rounded-md border border-slate-300 px-3 py-2 disabled:bg-slate-100"
          >
            <option value="">Not assigned yet</option>
            {(doctors ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                Dr. {[d.first_name, d.last_name].filter(Boolean).join(" ") || d.username}
              </option>
            ))}
          </select>
        </div>
        <div className="sm:col-span-2">
          <label className="mb-1 block text-sm font-medium text-slate-700">Admitting diagnosis</label>
          <textarea
            rows={2}
            value={diagnosis}
            onChange={(e) => setDiagnosis(e.target.value)}
            disabled={!patient}
            placeholder="Why they are being admitted"
            className="w-full rounded-md border border-slate-300 px-3 py-2 disabled:bg-slate-100"
          />
        </div>
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      <button
        onClick={() => { setError(null); admit.mutate(); }}
        disabled={!patient || !bedId || admit.isPending}
        className="mt-4 rounded-md bg-brand-600 px-6 py-2.5 font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {admit.isPending ? "Admitting…" : "Admit"}
      </button>
    </section>
  );
}
