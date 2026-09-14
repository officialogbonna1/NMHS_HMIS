import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import api from "../api/client";
import { patientNumber } from "./patientIdentity.js";
import { Alert, Button, Field, Input, Modal } from "./ui.jsx";

// Permanently deleting a patient — the one irreversible action in the
// application, so the dialog is built to be hard to fire by accident:
//
//  - it names the patient in full, so there is no doubt which record it is;
//  - it says plainly that this cannot be undone;
//  - it will not enable until the hospital number has been typed out, which
//    is the same value the backend demands in `confirm`.
//
// None of that is the control. `IsSuperAdmin` on the API is, and it refuses a
// hospital admin, reception and every clinical role whatever this component
// chooses to render. It also answers 409 — with the counts — for a patient
// the hospital has any financial, visit, laboratory or ward history for, and
// that refusal is shown here rather than swallowed.
export default function DeletePatientModal({ patient, onClose }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [typed, setTyped] = useState("");
  const [error, setError] = useState(null);
  const [blocking, setBlocking] = useState(null);

  const number = patientNumber(patient) ?? "";
  const matches = typed.trim().toUpperCase() === number.toUpperCase() && number !== "";

  const remove = useMutation({
    mutationFn: () =>
      api.delete(`/patients/${patient.uuid}/`, { data: { confirm: typed.trim() } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["patients"] });
      navigate("/patients", { replace: true });
    },
    onError: (err) => {
      const data = err?.response?.data ?? {};
      setBlocking(data.references ?? null);
      setError(data.detail ?? "This patient could not be deleted.");
    },
  });

  return (
    <Modal
      open
      onClose={onClose}
      title="Delete this patient permanently"
      description="This cannot be undone."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            variant="danger" disabled={!matches || remove.isPending}
            loading={remove.isPending} loadingText="Deleting…"
            onClick={() => { setError(null); setBlocking(null); remove.mutate(); }}
          >
            Delete permanently
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Alert tone="danger" title="Permanent deletion">
          The patient record and everything that only describes them — vitals, notes, the health
          record, appointments and prescriptions — are removed from the database for good. There
          is no undo and no archive to restore from.
        </Alert>

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
          <p className="text-base font-semibold text-slate-900">
            {patient.last_name}, {patient.first_name}
          </p>
          <p className="mt-0.5 text-sm text-slate-700">
            {number}
            {patient.sex ? ` · ${patient.sex === "M" ? "Male" : "Female"}` : ""}
            {patient.age_display ? ` · ${patient.age_display}` : ""}
          </p>
        </div>

        <p className="text-sm text-slate-700">
          A patient the hospital has billed, taken money from, opened a visit for, ordered a
          laboratory test for or admitted <strong>cannot</strong> be deleted — that history is the
          hospital&rsquo;s record, not the patient&rsquo;s, and the server will refuse.
        </p>

        <Field label={`Type ${number} to confirm`} required>
          <Input
            value={typed} autoFocus autoCapitalize="characters" spellCheck={false}
            onChange={(e) => setTyped(e.target.value)} placeholder={number}
          />
        </Field>

        {error && (
          <Alert tone="danger" title="Nothing was deleted">
            {error}
            {blocking && (
              <ul className="mt-2 list-inside list-disc">
                {Object.entries(blocking).map(([name, count]) => (
                  <li key={name}>{count} {name.replace(/_/g, " ")}</li>
                ))}
              </ul>
            )}
          </Alert>
        )}
      </div>
    </Modal>
  );
}
