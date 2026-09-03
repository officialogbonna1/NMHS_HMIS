import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";

// The observation written alongside a reading. Shared by the vitals station
// and the patient chart's Vitals tab, so a nurse taking a repeat reading has
// the same place to say what they saw as one working the queue — a reading
// with no context is half the record.
//
// Notes lock on save like vitals do: a follow-up is a new note, never an
// edit of the last one.
export default function NursingNoteForm({ patientId, vitalsId, onSaved, compact = false }) {
  const [complaint, setComplaint] = useState("");
  const [observation, setObservation] = useState("");
  const [error, setError] = useState(null);

  const save = useMutation({
    // `vitals` is left out rather than sent as null when the note is written
    // before the reading — the field is optional, and an absent key is what
    // the serializer reads as "no reading to hang this on".
    mutationFn: () => api.post("/nursing-notes/", {
      patient: patientId, complaint, observation, ...(vitalsId ? { vitals: vitalsId } : {}),
    }),
    onSuccess: (response) => {
      setComplaint("");
      setObservation("");
      setError(null);
      onSaved?.(response.data);
    },
    onError: (err) => setError(readError(err, "Could not save this note.")),
  });

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (observation.trim()) save.mutate(); }}
      className="space-y-3"
    >
      <div>
        <label className="mb-1 block text-sm font-medium text-slate-700">Presenting complaint</label>
        <input
          value={complaint}
          onChange={(e) => setComplaint(e.target.value)}
          placeholder="What the patient says is wrong"
          className="w-full rounded-md border border-slate-300 px-3 py-2"
        />
      </div>
      <div>
        <label className="mb-1 block text-sm font-medium text-slate-700">Observation *</label>
        <textarea
          value={observation}
          onChange={(e) => setObservation(e.target.value)}
          rows={compact ? 3 : 4}
          placeholder="What you observed while taking the vitals"
          className="w-full rounded-md border border-slate-300 px-3 py-2"
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={!observation.trim() || save.isPending}
          className="rounded-md bg-brand-600 px-5 py-2.5 text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {save.isPending ? "Saving…" : "Save note"}
        </button>
        {!observation.trim() && <span className="text-sm text-slate-500">An observation is required.</span>}
      </div>
    </form>
  );
}
