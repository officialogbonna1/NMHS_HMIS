import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { Button, Page, PageHeader } from "../components/ui.jsx";
import PatientPicker from "../components/PatientPicker.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useToast } from "../components/Toaster.jsx";

const STATUS_TONE = {
  queued: "bg-amber-100 text-amber-800",
  accepted: "bg-blue-100 text-blue-700",
  in_progress: "bg-brand-100 text-brand-700",
  completed: "bg-emerald-100 text-emerald-700",
  cancelled: "bg-slate-100 text-slate-400 line-through",
};

const STATUS_LABEL = {
  queued: "Queued",
  accepted: "Accepted",
  in_progress: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
};

const TRANSITION_TOAST = {
  accept: "Appointment accepted",
  cancel: "Appointment cancelled",
  start: "Appointment started",
  end: "Appointment completed",
};

// Appointments are a queue, not a diary: start_time/end_time are stamped by
// the doctor's own start/end actions, so an entry that hasn't been started
// yet is described by when it was raised.
function appointmentTiming(a) {
  if (a.end_time) return `Seen ${new Date(a.end_time).toLocaleString()}`;
  if (a.start_time) return `Started ${new Date(a.start_time).toLocaleTimeString()}`;
  return `Queued ${new Date(a.created_at).toLocaleString()}`;
}

const currency = (n) => new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(n ?? 0);

export default function Appointments() {
  const { user } = useAuth();
  const { showToast } = useToast();
  const canBook = ["reception", "admin", "hospital_admin"].includes(user?.role);
  const isDoctor = user?.role === "doctor";
  const queryClient = useQueryClient();

  const { data: appointments, isLoading } = useQuery({
    queryKey: ["appointments"],
    queryFn: () => api.get("/appointments/").then((r) => r.data.results ?? r.data),
  });

  const transition = useMutation({
    mutationFn: ({ id, action }) => api.post(`/appointments/${id}/${action}/`),
    onSuccess: (response, { action }) => {
      queryClient.invalidateQueries({ queryKey: ["appointments"] });
      showToast({ title: TRANSITION_TOAST[action], message: response.data.patient_name });
    },
    onError: (error) => {
      showToast({
        title: "Could not update appointment",
        message: error.response?.data?.detail || "Please try again.",
        tone: "error",
      });
    },
  });

  return (
    <Page className="space-y-8">
      <PageHeader
        className="mb-0"
        icon="calendar"
        title="Appointments"
        subtitle="A queue, not a diary — reception picks the patient and the doctor, and the doctor's own transitions record the times."
      />

      {canBook && <BookAppointmentForm onDone={() => queryClient.invalidateQueries({ queryKey: ["appointments"] })} />}

      <section>
        <h2 className="font-medium text-slate-800 mb-3">{canBook ? "All appointments" : "My appointments"}</h2>
        {isLoading && <p className="text-slate-600">Loading…</p>}
        {!isLoading && (appointments ?? []).length === 0 && <p className="text-slate-600 text-sm">No appointments yet.</p>}
        <div className="grid gap-2">
          {(appointments ?? []).map((a) => (
            <div key={a.id} className="border rounded-lg p-4 flex items-center justify-between gap-4">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium">{a.patient_name}</span>
                  <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${STATUS_TONE[a.status]}`}>{STATUS_LABEL[a.status]}</span>
                </div>
                <p className="text-sm text-slate-600 mt-1">
                  Dr. {a.doctor_name} · {appointmentTiming(a)} · {a.reason}
                </p>
              </div>
              {isDoctor && (
                <div className="flex flex-wrap items-center gap-1 text-sm sm:shrink-0">
                  {a.status === "queued" && (
                    <>
                      <Button variant="link" size="xs" onClick={() => transition.mutate({ id: a.id, action: "accept" })}>
                        Accept
                      </Button>
                      <Button variant="linkDanger" size="xs" onClick={() => transition.mutate({ id: a.id, action: "cancel" })}>
                        Cancel
                      </Button>
                    </>
                  )}
                  {a.status === "accepted" && (
                    <>
                      <Button variant="link" size="xs" onClick={() => transition.mutate({ id: a.id, action: "start" })}>
                        Start
                      </Button>
                      <Button variant="linkDanger" size="xs" onClick={() => transition.mutate({ id: a.id, action: "cancel" })}>
                        Cancel
                      </Button>
                    </>
                  )}
                  {a.status === "in_progress" && (
                    <Button variant="link" size="xs" onClick={() => transition.mutate({ id: a.id, action: "end" })}>
                      End
                    </Button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </Page>
  );
}

function BookAppointmentForm({ onDone }) {
  const [patient, setPatient] = useState(null);
  const [doctorId, setDoctorId] = useState("");
  const [reason, setReason] = useState("");
  const [billAmount, setBillAmount] = useState("");

  const { data: doctors } = useQuery({
    queryKey: ["users", "doctor"],
    queryFn: () => api.get("/users/", { params: { role: "doctor" } }).then((r) => r.data.results ?? r.data),
  });

  const book = useMutation({
    mutationFn: async () => {
      const { data: appointment } = await api.post("/appointments/", {
        patient: patient.id,
        doctor: Number(doctorId),
        reason,
      });
      if (billAmount) {
        await api.post("/charges/", {
          patient: patient.id,
          description: `Appointment — ${reason || "Consultation"}`,
          amount: billAmount,
          source_type: "appointment",
          source_id: appointment.id,
        });
      }
      return appointment;
    },
    onSuccess: () => {
      onDone();
      setPatient(null);
      setDoctorId("");
      setReason("");
      setBillAmount("");
    },
  });

  const canSubmit = patient && doctorId;

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (canSubmit) book.mutate(); }}
      className="bg-white border rounded-xl p-5 space-y-4"
    >
      <h2 className="font-medium text-slate-800">Queue an appointment</h2>
      <p className="text-xs text-slate-600 -mt-2">
        The doctor is notified straight away and accepts, starts and ends the consultation from their side —
        there is no time to set here.
      </p>

      <div className="grid md:grid-cols-2 gap-4">
        <div className="md:col-span-2">
          <label className="block text-sm font-medium mb-1">Patient *</label>
          <PatientPicker value={patient} onChange={setPatient} />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Doctor *</label>
          <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)} required className="w-full border rounded-md px-3 py-2">
            <option value="">Select doctor</option>
            {(doctors ?? []).map((d) => (
              <option key={d.id} value={d.id}>Dr. {[d.first_name, d.last_name].filter(Boolean).join(" ")}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Reason</label>
          <input value={reason} onChange={(e) => setReason(e.target.value)} className="w-full border rounded-md px-3 py-2" placeholder="Reason for visit" />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Bill amount</label>
          <input type="number" min="0" step="0.01" value={billAmount} onChange={(e) => setBillAmount(e.target.value)} className="w-full border rounded-md px-3 py-2" placeholder="Optional — e.g. consultation fee" />
        </div>
      </div>

      {book.isError && (
        <p className="text-sm text-red-600">{book.error?.response?.data?.detail || "Could not book this appointment."}</p>
      )}
      <button type="submit" disabled={!canSubmit || book.isPending} className="bg-brand-600 text-white px-5 py-2.5 rounded-md hover:bg-brand-700 disabled:opacity-50">
        {book.isPending ? "Queueing…" : "Queue appointment"}
      </button>
    </form>
  );
}
