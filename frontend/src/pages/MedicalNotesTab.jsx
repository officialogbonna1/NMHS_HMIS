import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext.jsx";
import { EYE_EXAMINATION_ROLES } from "../auth/roles.js";
import EyeExaminationFields from "../components/EyeExaminationFields.jsx";
import { PrintButton } from "../components/printing.jsx";
import { useToast } from "../components/Toaster.jsx";
import {
  Alert, Badge, Button, Card, CardBody, CardFooter, CardHeader, EmptyState,
  Field, Input, Section, Select, Textarea, SkeletonRows,
} from "../components/ui.jsx";
import { Icon } from "../components/icons.jsx";
import api from "../api/client";

// The patient's clinical notes, as a longitudinal record.
//
// **A visit is a note; a correction is an amendment.** Returning next month
// creates a new `ConsultationNote` — nothing overwrites the last one. Fixing
// something already written amends *that* note, which archives what it said
// before in `ConsultationNoteAmendment`. The server holds both rules; this
// page is careful to say which one a button is about, because confusing them
// is how a follow-up ends up overwriting the consultation before it.
//
// Viewing is read-only by design: you have to press Amend to change anything,
// so a saved record cannot be altered by landing on it and typing.
//
// The eye doctor writes this same note with an eye examination under it.
// `?new=1` opens a new note straight away — the eye clinic station's
// "Eye consultation" lands here.

const dateTime = (value) => (value ? new Date(value).toLocaleString() : "—");

export default function MedicalNotesTab({ patientId, patient }) {
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  // null = writing a new one, a note = the one being read, undefined = the list
  const [open, setOpen] = useState(searchParams.get("new") ? null : undefined);

  const { data: notes, isLoading } = useQuery({
    queryKey: ["notes", patientId],
    queryFn: () => api.get("/notes/", { params: { patient: patientId } })
      .then((r) => r.data.results ?? r.data),
  });

  const done = (saved) => {
    queryClient.invalidateQueries({ queryKey: ["notes", patientId] });
    queryClient.invalidateQueries({ queryKey: ["patient-overview", String(patientId)] });
    // Land on the saved note, so Print is one press away from saving it.
    setOpen(saved ?? undefined);
  };

  if (open === null) {
    return <NoteEditor patientId={patientId} note={null} onDone={done}
                       onCancel={() => setOpen(undefined)} />;
  }
  if (open) {
    // Read the live copy: an amendment just written has to show on the page
    // that sent it, not the stale row the list was holding.
    const live = (notes ?? []).find((n) => n.id === open.id) ?? open;
    return <NoteView note={live} patient={patient} onDone={done}
                     onBack={() => setOpen(undefined)} />;
  }

  return (
    <Section
      title="Clinical notes"
      description="Every consultation, newest first. A new visit is a new note; a correction amends the note it belongs to."
      actions={<Button onClick={() => setOpen(null)}>
        <Icon name="plus" className="h-4 w-4" aria-hidden="true" />
        New note
      </Button>}
    >
      {isLoading && <SkeletonRows rows={3} />}

      {!isLoading && !notes?.length && (
        <Card>
          <EmptyState
            icon="list"
            title="No clinical notes yet"
            description="The first consultation written for this patient appears here, and every one after it."
          />
        </Card>
      )}

      <div className="grid gap-2.5">
        {(notes ?? []).map((note) => (
          <NoteRow key={note.id} note={note} patient={patient} onOpen={() => setOpen(note)} />
        ))}
      </div>
    </Section>
  );
}

function NoteRow({ note, patient, onOpen }) {
  const { user } = useAuth();
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm
      transition hover:border-slate-300 sm:flex-row sm:items-center sm:justify-between">
      <button onClick={onOpen} className="min-w-0 flex-1 text-left">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <span className="font-semibold text-slate-900">{note.reason_for_visit}</span>
          {note.eye_examination && <Badge tone="brand">Eye examination</Badge>}
          {/* Quiet, but impossible to miss — the point is that nobody reads an
              amended note believing it is still as first written. */}
          {note.is_amended && <Badge tone="warning">Amended</Badge>}
        </div>
        <p className="mt-1 text-sm text-slate-600">
          {dateTime(note.visit_time)} · {note.doctor_name ?? "—"}
          {note.is_amended && ` · last amended ${dateTime(note.last_amended_at)}`}
        </p>
        {note.diagnosis && (
          <p className="mt-1 line-clamp-2 text-sm text-slate-700">{note.diagnosis}</p>
        )}
      </button>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <PrintButton role={user?.role} variant="secondary" size="sm"
                     documents={["consultation_note"]} context={{ note, patient }} />
        <Button variant="link" size="sm" onClick={onOpen}>
          View
          <Icon name="chevronRight" className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}

/** A saved note, read-only until Amend is pressed. */
function NoteView({ note, patient, onDone, onBack }) {
  const { user } = useAuth();
  const [amending, setAmending] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  if (amending) {
    return (
      <NoteEditor
        patientId={note.patient}
        note={note}
        onDone={(saved) => { setAmending(false); onDone(saved); }}
        onCancel={() => setAmending(false)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <Button variant="link" size="xs" onClick={onBack}>
        <Icon name="back" className="h-4 w-4" aria-hidden="true" />
        Back to the notes
      </Button>

      <Card>
        <CardHeader
          title={note.reason_for_visit}
          description={`${dateTime(note.visit_time)} · ${note.doctor_name ?? "—"}`}
          actions={
            <>
              {/* The server decides this (`may_amend`); the button only
                  reflects it. A caller who forges the request is refused. */}
              {note.can_amend && (
                <Button variant="secondary" onClick={() => setAmending(true)}>Amend note</Button>
              )}
              <PrintButton role={user?.role} variant="secondary"
                           documents={["consultation_note"]} context={{ note, patient }} />
            </>
          }
        />
        <CardBody className="space-y-4">
          <dl className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
            <Fact label="Documented by" value={note.doctor_name} />
            <Fact label="Staff number" value={note.doctor_staff_number} />
            <Fact label="Created" value={dateTime(note.created_at)} />
            <Fact label="Status" value={note.is_amended ? "Amended" : "Original"} />
            {note.is_amended && (
              <Fact label="Last amended" value={dateTime(note.last_amended_at)} />
            )}
            {note.is_amended && (
              <Fact label="Last amended by" value={note.last_amended_by_name} />
            )}
          </dl>

          {note.is_amended && (
            <Alert tone="warning" title="This note has been amended">
              What is shown below is the current record.{" "}
              <button type="button" onClick={() => setShowHistory((v) => !v)}
                      className="font-medium underline underline-offset-2">
                {showHistory ? "Hide" : "View"} amendment history
              </button>
              {" "}to see what it said before, and why it was changed.
            </Alert>
          )}

          {showHistory && <AmendmentHistory note={note} />}

          <ReadOnlyField label="Chief complaint" value={note.chief_complaint} />
          <ReadOnlyField label="Notes" value={note.note_text} />
          <ReadOnlyField label="Diagnosis" value={note.diagnosis} />
          <ReadOnlyField label="Plan" value={note.plan} />

          {note.eye_examination && (
            <EyeExaminationFields value={note.eye_examination} onChange={() => {}} disabled />
          )}
        </CardBody>
      </Card>
    </div>
  );
}

/** The trail, newest first, with what each correction actually changed. */
function AmendmentHistory({ note }) {
  const amendments = note.amendments ?? [];
  return (
    <section aria-label="Amendment history"
             className="rounded-xl border border-slate-200 bg-slate-50 p-4">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-700">
        Amendment history
      </h3>
      <ol className="mt-3 space-y-3">
        {amendments.map((amendment, index) => (
          <li key={amendment.id} className="border-l-2 border-slate-300 pl-3">
            <p className="text-sm font-medium text-slate-900">
              Amendment #{amendments.length - index} · {dateTime(amendment.created_at)}
            </p>
            <p className="text-sm text-slate-700">
              By {amendment.amended_by_name ?? "—"}
              {amendment.reason_label && ` · ${amendment.reason_label}`}
            </p>
            {amendment.detail && (
              <p className="mt-1 text-sm text-slate-700">“{amendment.detail}”</p>
            )}
            {amendment.changes?.length > 0 && (
              <dl className="mt-2 space-y-1.5">
                {amendment.changes.map((change) => (
                  <div key={change.field} className="text-sm">
                    <dt className="font-medium text-slate-800">{change.label}</dt>
                    <dd className="text-slate-700">
                      <span className="line-through decoration-slate-400">
                        {change.previous || "(blank)"}
                      </span>
                      {" → "}
                      <span className="font-medium text-slate-900">
                        {change.current || "(blank)"}
                      </span>
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </li>
        ))}
        {/* The original is the foot of the chain, so the trail reads all the
            way back to who first documented it. */}
        <li className="border-l-2 border-slate-300 pl-3">
          <p className="text-sm font-medium text-slate-900">
            Original · {dateTime(note.created_at)}
          </p>
          <p className="text-sm text-slate-700">Created by {note.doctor_name ?? "—"}</p>
        </li>
      </ol>
    </section>
  );
}

function Fact({ label, value }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="text-slate-800">{value || "—"}</dd>
    </div>
  );
}

function ReadOnlyField({ label, value }) {
  if (!value) return null;
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-0.5 whitespace-pre-wrap text-slate-800">{value}</p>
    </div>
  );
}

/**
 * Writing a new note, or amending a saved one. The same fields either way —
 * what differs is that an amendment must say why, and archives what the note
 * said before it.
 */
function NoteEditor({ patientId, note, onDone, onCancel }) {
  const { user } = useAuth();
  const { showToast } = useToast();
  const isNew = note === null;

  const [form, setForm] = useState({
    visit_time: note?.visit_time ?? new Date().toISOString().slice(0, 16),
    reason_for_visit: note?.reason_for_visit ?? "",
    chief_complaint: note?.chief_complaint ?? "",
    note_text: note?.note_text ?? "",
    diagnosis: note?.diagnosis ?? "",
    plan: note?.plan ?? "",
  });
  const [reason, setReason] = useState("");
  const [detail, setDetail] = useState("");
  const [reasonChoices, setReasonChoices] = useState(null);
  const [error, setError] = useState(null);
  const [eyeExamination, setEyeExamination] = useState(note?.eye_examination ?? {});
  const [eyeErrors, setEyeErrors] = useState(null);

  // The eye examination travels only on a note that has one, or that the eye
  // doctor is writing — a general doctor's note is sent exactly as before.
  const showEye = Boolean(note?.eye_examination)
    || EYE_EXAMINATION_ROLES.includes(user?.role);

  const mutation = useMutation({
    mutationFn: () => {
      const body = showEye ? { ...form, eye_examination: eyeExamination } : { ...form };
      if (isNew) return api.post("/notes/", { patient: patientId, ...body });
      return api.patch(`/notes/${note.id}/`, {
        ...body, amendment_reason: reason, amendment_detail: detail,
      });
    },
    onSuccess: (response) => {
      showToast({
        title: isNew ? "Medical note saved" : "Medical note amended",
        message: isNew
          ? "It is on the patient's record. Print it from here."
          : "The previous version is kept in the amendment history.",
      });
      onDone(response.data);
    },
    onError: (err) => {
      const data = err.response?.data;
      setEyeErrors(data?.eye_examination ?? null);
      if (data?.code === "amendment_reason_required") setReasonChoices(data.choices ?? []);
      setError(data?.amendment_reason ?? data?.detail
        ?? (data?.eye_examination ? "Check the eye examination below." : "Could not save this note."));
    },
  });

  const REASONS = reasonChoices ?? [
    { value: "correction", label: "Correction of error" },
    { value: "additional_information", label: "Additional clinical information" },
    { value: "clarification", label: "Clarification" },
    { value: "documentation", label: "Documentation correction" },
    { value: "other", label: "Other" },
  ];

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const ready = form.reason_for_visit.trim() && (isNew || reason);

  return (
    <div className="space-y-4">
      <Button variant="link" size="xs" onClick={onCancel}>
        <Icon name="back" className="h-4 w-4" aria-hidden="true" />
        {isNew ? "Back to the notes" : "Back to the note"}
      </Button>

      <Card>
        <CardHeader
          title={isNew ? "New clinical note" : "Amend this note"}
          description={isNew
            ? "A note of its own for this visit. Nothing written before is changed."
            : `Correcting the note ${note.doctor_name ?? "—"} wrote on ${dateTime(note.visit_time)}. What it says now is kept in the amendment history.`}
        />
        <CardBody className="space-y-4">
          {!isNew && (
            <>
              <Field label="Reason for amendment" required>
                <Select value={reason} onChange={(e) => setReason(e.target.value)}>
                  <option value="">Choose a reason…</option>
                  {REASONS.map((r) => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Additional explanation"
                     hint="What you are correcting, in your own words. Kept with the amendment.">
                <Textarea rows={2} value={detail} onChange={(e) => setDetail(e.target.value)} />
              </Field>
            </>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Reason for visit" required>
              <Input value={form.reason_for_visit} onChange={set("reason_for_visit")} />
            </Field>
            <Field label="Chief complaint">
              <Input value={form.chief_complaint} onChange={set("chief_complaint")} />
            </Field>
          </div>
          <Field label="Notes">
            <Textarea rows={4} value={form.note_text} onChange={set("note_text")} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Diagnosis">
              <Textarea rows={4} value={form.diagnosis} onChange={set("diagnosis")} />
            </Field>
            <Field label="Plan">
              <Textarea rows={4} value={form.plan} onChange={set("plan")} />
            </Field>
          </div>

          {showEye && (
            <EyeExaminationFields value={eyeExamination} onChange={setEyeExamination}
                                  errors={eyeErrors} />
          )}

          {error && <Alert tone="danger">{error}</Alert>}
        </CardBody>

        <CardFooter>
          <Button onClick={() => { setError(null); mutation.mutate(); }}
                  disabled={!ready || mutation.isPending}
                  loading={mutation.isPending} loadingText="Saving…">
            {isNew ? "Save medical note" : "Save amendment"}
          </Button>
          <Button variant="secondary" onClick={onCancel}>Cancel</Button>
          {!isNew && !reason && (
            <span className="text-sm text-slate-600">
              Choose a reason for the amendment to save it.
            </span>
          )}
        </CardFooter>
      </Card>
    </div>
  );
}
