import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { patientNumber } from "../components/patientIdentity.js";
import PatientPicker, { patientLabel } from "../components/PatientPicker.jsx";
import { readError } from "../api/errors";
import { useToast } from "../components/Toaster.jsx";
import { Button, Page, PageHeader } from "../components/ui.jsx";

// Nursing's hand-off desk. The vitals station forwards the patient in front
// of you; this page is for the rest of it — someone whose route you already
// closed, or who came back to the desk. Pick the patient, pick the doctor:
// the consultation is queued in that doctor's name and they are notified.
//
// The server (workflow.views.send_to_doctor) is the authority on the rules
// here — an open visit, vitals on file, and nobody already waiting on a
// doctor for this visit. The page mirrors them so the refusal is rare.

const PRIORITY_TONE = {
  emergency: "border-red-300 bg-red-50 text-red-700",
  urgent: "border-amber-300 bg-amber-50 text-amber-800",
  routine: "border-slate-200 bg-slate-50 text-slate-600",
};

function doctorName(d) {
  return [d.first_name, d.last_name].filter(Boolean).join(" ") || d.username;
}

export default function SendToConsultation() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [patient, setPatient] = useState(null);
  const [doctorId, setDoctorId] = useState("");
  const [priority, setPriority] = useState("routine");
  const [notes, setNotes] = useState("");
  const [sent, setSent] = useState(null);
  // The server answers "no vitals on this visit — anyway?" the first time.
  // Holding the question here rather than in a browser confirm() lets it say
  // which patient, and lets the nurse go and take the reading instead.
  const [confirmNoVitals, setConfirmNoVitals] = useState(null);
  // The other question the server can ask: somebody already has this
  // patient. Answering it is how a patient gets moved when the doctor who
  // saw them last is not on seat.
  const [confirmReassign, setConfirmReassign] = useState(null);

  // The patients this nurse is working — the usual case is sending one of
  // these on, so they are one click away rather than something to search for.
  const { data: routes } = useQuery({
    queryKey: ["patient-routes"],
    queryFn: () => api.get("/patient-routes/").then((r) => r.data.results ?? r.data),
  });

  const { data: doctors } = useQuery({
    queryKey: ["users", "doctor"],
    queryFn: () => api.get("/users/", { params: { role: "doctor" } }).then((r) => r.data.results ?? r.data),
  });

  const send = useMutation({
    mutationFn: (ack = {}) => api.post("/patient-routes/send-to-doctor/", {
      patient: patient.id,
      doctor: Number(doctorId),
      priority,
      notes,
      // Each warning is acknowledged by name, and once acknowledged it stays
      // acknowledged — otherwise answering the second question re-raises the
      // first and the nurse loops.
      ...(ack.noVitals || confirmNoVitals ? { acknowledge_no_vitals: true } : {}),
      ...(ack.reassign || confirmReassign ? { acknowledge_reassign: true } : {}),
    }),
    onSuccess: () => {
      const doctor = (doctors ?? []).find((d) => String(d.id) === doctorId);
      const who = doctor ? `Dr. ${doctorName(doctor)}` : "the doctor";
      queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["vitals-recorded-today"] });
      showToast({
        title: "Queued for consultation",
        message: `${patientLabel(patient)} is now waiting for ${who}, and ${who} has been notified.`,
      });
      setSent({ patient, who });
      setConfirmNoVitals(null); setConfirmReassign(null);
      setPatient(null); setDoctorId(""); setNotes(""); setPriority("routine");
    },
    onError: (error) => {
      const code = error.response?.data?.code;
      if (code === "no_vitals") {
        setConfirmNoVitals(error.response.data.detail);
        return;
      }
      if (code === "reassign") {
        setConfirmReassign(error.response.data);
        return;
      }
      showToast({
        title: "Could not send this patient",
        message: readError(error, "Please try again."),
        tone: "error",
      });
    },
  });

  const readyToSend = patient && doctorId && !send.isPending;

  return (
    <Page className="space-y-6">
      <PageHeader
        className="mb-0"
        icon="handoff"
        title="Send to a doctor"
        subtitle="Queue a patient you have seen for consultation. The doctor is notified, the patient appears in their queue, and the chart opens to them."
      />

      {sent && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <span>{patientLabel(sent.patient)} is with {sent.who}.</span>
          <button onClick={() => setSent(null)} className="text-xs underline">Dismiss</button>
        </div>
      )}

      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-medium text-slate-800">1. Which patient?</h2>

        {patient ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-brand-200 bg-brand-50/60 p-3">
            <div>
              <p className="font-medium">{patientLabel(patient)}</p>
              <p className="text-xs text-slate-600">{patientNumber(patient)}</p>
            </div>
            <Button variant="link" size="xs" onClick={() => setPatient(null)}>
              Choose someone else
            </Button>
          </div>
        ) : (
          <>
            {(routes ?? []).length > 0 && (
              <div className="mt-3">
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">In your care now</p>
                <div className="grid gap-2">
                  {(routes ?? []).map((route) => (
                    <button
                      key={route.id}
                      onClick={() => setPatient({
                        id: route.patient_id,
                        patient_number: route.patient_number,
                        display: route.patient_name,
                      })}
                      className="flex items-center justify-between gap-3 rounded-lg border p-3 text-left hover:border-brand-300 hover:bg-brand-50/50"
                    >
                      <span>
                        <span className="font-medium">{route.patient_name}</span>
                        <span className="ml-2 text-xs text-slate-600">{patientNumber(route)}</span>
                      </span>
                      <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${PRIORITY_TONE[route.priority]}`}>
                        {route.priority}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-4">
              <label className="mb-1 block text-sm font-medium">
                {(routes ?? []).length > 0 ? "Or pick anyone else you have seen" : "Pick a patient"}
              </label>
              <PatientPicker
                value={null}
                onChange={(p) => p && setPatient(p)}
                placeholder="Search or pick from the patients routed to you…"
                emptyMessage="Nobody matching. You can only send patients who have been routed to you."
              />
            </div>
          </>
        )}
      </section>

      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-medium text-slate-800">2. Which doctor?</h2>
        <p className="mb-4 text-xs text-slate-600">
          They are notified straight away and the patient shows up in their queue as a consultation.
        </p>

        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium">Doctor *</label>
            <select
              value={doctorId}
              onChange={(e) => { setDoctorId(e.target.value); setConfirmNoVitals(null); setConfirmReassign(null); }}
              disabled={!patient}
              className="w-full rounded-md border px-3 py-2 disabled:bg-slate-100"
            >
              <option value="">Select doctor</option>
              {(doctors ?? []).map((d) => <option key={d.id} value={d.id}>Dr. {doctorName(d)}</option>)}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Priority</label>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              disabled={!patient}
              className="w-full rounded-md border px-3 py-2 disabled:bg-slate-100"
            >
              <option value="routine">Routine</option>
              <option value="urgent">Urgent</option>
              <option value="emergency">Emergency</option>
            </select>
          </div>
          <div className="md:col-span-2">
            <label className="mb-1 block text-sm font-medium">Handover note</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={!patient}
              rows={3}
              placeholder="Anything the doctor should know before they call the patient in"
              className="w-full rounded-md border px-3 py-2 disabled:bg-slate-100"
            />
          </div>
        </div>

        {confirmReassign && (
          <div className="mt-4 rounded-lg border border-blue-300 bg-blue-50 p-4">
            <p className="font-medium text-blue-900">{confirmReassign.detail}</p>
            <p className="mt-1 text-sm text-blue-800">
              {confirmReassign.current_doctor}&apos;s consultation is cancelled, the chart moves to the
              new doctor, and both are told. Use this when the doctor who saw the patient is off.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={() => send.mutate({ reassign: true })}
                disabled={send.isPending}
                className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {send.isPending ? "Moving…" : "Move to this doctor"}
              </button>
              <button
                onClick={() => setConfirmReassign(null)}
                className="rounded-md border border-blue-300 bg-white px-4 py-2 text-sm font-medium text-blue-800"
              >
                Leave them where they are
              </button>
            </div>
          </div>
        )}

        {confirmNoVitals && (
          <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4">
            <p className="font-medium text-amber-900">⚠ {confirmNoVitals}</p>
            <p className="mt-1 text-sm text-amber-800">
              The doctor will be told the reading is missing. If you can take it now, that is what
              they are waiting on.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={() => send.mutate({ noVitals: true })}
                disabled={send.isPending}
                className="rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
              >
                {send.isPending ? "Sending…" : "Send without vitals"}
              </button>
              <button
                onClick={() => setConfirmNoVitals(null)}
                className="rounded-md border border-amber-300 bg-white px-4 py-2 text-sm font-medium text-amber-800"
              >
                Not yet
              </button>
              <Link
                to="/vitals"
                className="rounded-md border border-amber-300 bg-white px-4 py-2 text-sm font-medium text-amber-800"
              >
                Take the vitals first →
              </Link>
            </div>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            onClick={() => send.mutate({})}
            disabled={!readyToSend}
            className="rounded-md bg-brand-600 px-5 py-2.5 text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {send.isPending && !confirmNoVitals ? "Sending…" : "Send for consultation"}
          </button>
          <Button variant="link" size="xs" to="/vitals">
            Back to the vitals station
          </Button>
        </div>
        <p className="mt-3 text-sm text-slate-600">
          The patient needs an open visit. Vitals should be recorded first — if they are not, you
          will be asked to confirm before the patient goes through.
        </p>
      </section>
    </Page>
  );
}
