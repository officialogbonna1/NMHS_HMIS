import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { useAuth } from "../auth/AuthContext.jsx";
import { useToast } from "../components/Toaster.jsx";
import VitalsEntryForm from "../components/VitalsEntryForm.jsx";
import NursingNoteForm from "../components/NursingNoteForm.jsx";

// Mirrors the manual's Vitals section: reverse-chronological readings plus
// an "Add New Vitals" form. Vitals lock immediately after save (backend
// LockedRecordMixin) so there is no edit flow here on purpose. Nurses
// record; doctors read the same list, together with the nursing notes
// written alongside those readings.
export default function VitalsTab({ patientId }) {
  const { user } = useAuth();
  const { showToast } = useToast();
  const canRecord = ["nurse", "admin", "hospital_admin"].includes(user?.role);
  const isDoctor = user?.role === "doctor";
  const [showForm, setShowForm] = useState(false);
  // Set once the reading is in, so the note that follows is filed against it.
  const [savedVitalsId, setSavedVitalsId] = useState(null);
  const queryClient = useQueryClient();

  // page_size: the chart means the whole history, not the first page of it.
  const { data: vitals } = useQuery({
    queryKey: ["vitals", patientId],
    queryFn: () => api.get("/vitals/", { params: { patient: patientId, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });

  const { data: nursingNotes } = useQuery({
    queryKey: ["nursing-notes", patientId],
    queryFn: () => api.get("/nursing-notes/", { params: { patient: patientId, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });

  if (showForm) {
    return (
      <div className="space-y-4">
        <button onClick={() => { setShowForm(false); setSavedVitalsId(null); }} className="text-sm text-brand-600">
          ← Back
        </button>

        <section className="rounded-xl border bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-800">Record vitals</h2>
          <p className="mb-4 text-sm text-slate-600">
            Vitals lock the moment they are saved — check the figures before you save.
          </p>
          <VitalsEntryForm
            patientId={patientId}
            onCancel={() => setShowForm(false)}
            onSaved={(saved) => {
              queryClient.invalidateQueries({ queryKey: ["vitals", patientId] });
              // Kept open, not dismissed: a repeat reading still needs the
              // observation that goes with it, and this is the only place on
              // the chart a nurse can write one.
              setSavedVitalsId(saved?.id ?? null);
              showToast({ title: "Vitals saved", message: "Add the observation below." });
            }}
          />
        </section>

        <section className="rounded-xl border bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-800">Nursing note</h2>
          <p className="mb-4 text-sm text-slate-600">
            What you observed. The doctor reads this alongside the reading; it locks on save, so a
            follow-up is a new note.
          </p>
          <NursingNoteForm
            patientId={patientId}
            vitalsId={savedVitalsId}
            onSaved={() => {
              queryClient.invalidateQueries({ queryKey: ["nursing-notes", patientId] });
              showToast({ title: "Note saved" });
              setShowForm(false);
              setSavedVitalsId(null);
            }}
          />
        </section>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <section>
        <div className="flex justify-between items-center mb-4">
          <h2 className="font-semibold text-lg">Vitals</h2>
          {canRecord && (
            <button onClick={() => setShowForm(true)} className="bg-brand-600 text-white px-4 py-2 rounded-full text-sm">
              Add New Vitals
            </button>
          )}
        </div>

        <div className="grid gap-3">
          {(vitals ?? []).map((v, index) => (
            <Reading key={v.id} reading={v} isLatest={index === 0} />
          ))}
          {!vitals?.length && (
            <p className="text-slate-500 text-sm">
              {isDoctor ? "No vitals recorded for this patient yet — nursing will add them here." : "No vitals recorded yet."}
            </p>
          )}
        </div>
      </section>

      <section>
        <h2 className="font-semibold text-lg mb-4">Nursing notes</h2>
        <div className="grid gap-2">
          {(nursingNotes ?? []).map((n) => (
            <div key={n.id} className="border rounded-lg bg-white p-4 text-sm">
              <p className="text-sm text-slate-600">
                {new Date(n.created_at).toLocaleString()}
                {n.nurse_name && <> · written by <span className="font-medium text-slate-800">{n.nurse_name}</span></>}
              </p>
              {n.complaint && <p className="font-medium mt-1">{n.complaint}</p>}
              <p className="text-slate-800 mt-1 whitespace-pre-wrap">{n.observation}</p>
            </div>
          ))}
          {!nursingNotes?.length && <p className="text-slate-500 text-sm">No nursing notes yet.</p>}
        </div>
      </section>
    </div>
  );
}

// Every figure the entry form can capture, in the order it is taken. The
// list used to stop at five, so a respiratory rate, a height or a glucose
// the nurse had recorded simply never reached the doctor.
const MEASURES = [
  { key: "temperature_c", label: "Temp", unit: "°C" },
  { key: "heart_rate", label: "HR", unit: " bpm" },
  { key: "respiratory_rate", label: "RR", unit: "/min" },
  { key: "sao2", label: "SaO₂", unit: "%" },
  { key: "bp", label: "BP", unit: " mmHg", value: (v) => (v.bp_systolic && v.bp_diastolic ? `${v.bp_systolic}/${v.bp_diastolic}` : null) },
  { key: "height_cm", label: "Height", unit: " cm" },
  { key: "weight_kg", label: "Weight", unit: " kg" },
  { key: "glucose_level", label: "Glucose", unit: " mmol/L" },
];

function Reading({ reading, isLatest }) {
  const taken = MEASURES
    .map((m) => ({ ...m, value: m.value ? m.value(reading) : reading[m.key] }))
    .filter((m) => m.value != null && m.value !== "");

  // How the reading was taken, where it matters clinically: a BP lying down
  // on the left leg is not the same measurement as one sitting.
  const context = [
    [reading.bp_position, reading.bp_extremity].filter(Boolean).join(", "),
    [reading.glucose_time_of_day, reading.glucose_fasting === true ? "fasting"
      : reading.glucose_fasting === false ? "not fasting" : null].filter(Boolean).join(", "),
  ].filter(Boolean);

  return (
    <div className={`rounded-lg border bg-white p-4 ${isLatest ? "border-brand-400 ring-1 ring-brand-200" : "border-slate-200"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-slate-800">{new Date(reading.visit_time).toLocaleString()}</span>
          {isLatest && (
            <span className="rounded-full bg-brand-600 px-2.5 py-0.5 text-xs font-semibold text-white">
              Most recent
            </span>
          )}
          <span className="text-sm text-slate-500">{timeAgo(reading.visit_time)}</span>
        </div>
        {reading.recorded_by_name && (
          <span className="text-sm text-slate-600">
            Recorded by <span className="font-medium text-slate-800">{reading.recorded_by_name}</span>
          </span>
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
        {taken.map((m) => (
          <div key={m.key}>
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{m.label}</div>
            <div className="font-semibold text-slate-800">
              {m.value}<span className="text-sm font-normal text-slate-500">{m.unit}</span>
            </div>
          </div>
        ))}
      </div>

      {context.length > 0 && <p className="mt-3 text-sm text-slate-600">{context.join(" · ")}</p>}
    </div>
  );
}

function timeAgo(when) {
  const minutes = Math.round((Date.now() - new Date(when)) / 60000);
  if (minutes < 0) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}
