import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext.jsx";
import api from "../api/client";

// Mirrors the manual's Medical Notes section: reverse-chronological list,
// "Add Medical Note" button, any doctor can view all notes but only the
// author can edit their own (before it locks). Editing a locked note
// (admin only) creates an amendment snapshot rather than overwriting.
export default function MedicalNotesTab({ patientId }) {
  const [editingNote, setEditingNote] = useState(undefined); // undefined = closed, null = new, object = editing
  const queryClient = useQueryClient();

  const { data: notes } = useQuery({
    queryKey: ["notes", patientId],
    queryFn: () => api.get("/notes/", { params: { patient: patientId } }).then((r) => r.data.results ?? r.data),
  });

  if (editingNote !== undefined) {
    return (
      <NoteEditor
        patientId={patientId}
        note={editingNote}
        onDone={() => {
          queryClient.invalidateQueries({ queryKey: ["notes", patientId] });
          setEditingNote(undefined);
        }}
        onCancel={() => setEditingNote(undefined)}
      />
    );
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="font-semibold text-lg">Medical Notes</h2>
        <button
          onClick={() => setEditingNote(null)}
          className="bg-brand-600 text-white px-4 py-2 rounded-full text-sm"
        >
          Add Medical Note
        </button>
      </div>

      <div className="grid gap-3">
        {notes?.map((note) => (
          <button
            key={note.id}
            onClick={() => setEditingNote(note)}
            className="text-left border rounded-lg p-4 hover:bg-gray-50"
          >
            <div className="flex justify-between text-sm text-slate-600">
              <span>{new Date(note.visit_time).toLocaleString()}</span>
              {note.is_locked && <span className="text-xs text-slate-500">🔒 locked</span>}
            </div>
            <div className="font-medium mt-1">{note.reason_for_visit}</div>
            <div className="text-sm text-slate-700 line-clamp-2">{note.note_text}</div>
          </button>
        ))}
        {!notes?.length && <p className="text-slate-500 text-sm">No medical notes yet.</p>}
      </div>
    </div>
  );
}

function NoteEditor({ patientId, note, onDone, onCancel }) {
  const { user } = useAuth();
  const isNew = note === null;
  const isOwner = !isNew && note.doctor === user?.id;
  const canEdit = isNew || (isOwner && !note.is_locked) || user?.role === "admin";

  const [form, setForm] = useState({
    visit_time: note?.visit_time ?? new Date().toISOString().slice(0, 16),
    reason_for_visit: note?.reason_for_visit ?? "",
    chief_complaint: note?.chief_complaint ?? "",
    note_text: note?.note_text ?? "",
    diagnosis: note?.diagnosis ?? "",
    plan: note?.plan ?? "",
  });
  const [error, setError] = useState(null);

  const mutation = useMutation({
    mutationFn: () =>
      isNew
        ? api.post("/notes/", { patient: patientId, ...form })
        : api.patch(`/notes/${note.id}/`, form),
    onSuccess: onDone,
    onError: (err) => setError(err.response?.data?.detail ?? "Could not save note."),
  });

  return (
    <div>
      <button onClick={onCancel} className="text-sm text-brand-600 mb-4">← Back</button>

      {!canEdit && (
        <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-md text-sm text-amber-800">
          This note is locked. {isOwner ? "Only an admin can amend it now." : "You can only edit your own notes."}
        </div>
      )}

      <div className="bg-white border rounded-xl p-6 space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <Field label="Reason for Visit" value={form.reason_for_visit} disabled={!canEdit}
            onChange={(v) => setForm((f) => ({ ...f, reason_for_visit: v }))} />
          <Field label="Chief Complaint (optional)" value={form.chief_complaint} disabled={!canEdit}
            onChange={(v) => setForm((f) => ({ ...f, chief_complaint: v }))} />
        </div>
        <TextAreaField label="Notes" value={form.note_text} disabled={!canEdit}
          onChange={(v) => setForm((f) => ({ ...f, note_text: v }))} />
        <div className="grid grid-cols-2 gap-4">
          <TextAreaField label="Diagnosis" value={form.diagnosis} disabled={!canEdit}
            onChange={(v) => setForm((f) => ({ ...f, diagnosis: v }))} />
          <TextAreaField label="Plan" value={form.plan} disabled={!canEdit}
            onChange={(v) => setForm((f) => ({ ...f, plan: v }))} />
        </div>

        {note?.amendments?.length > 0 && (
          <div className="border-t pt-4">
            <p className="text-sm font-medium text-slate-700 mb-2">Previous edits</p>
            {note.amendments.map((a) => (
              <div key={a.id} className="text-xs text-slate-600 border-l-2 pl-3 mb-2">
                {new Date(a.created_at).toLocaleString()} — amended by admin
              </div>
            ))}
          </div>
        )}

        {error && <p className="text-red-500 text-sm">{error}</p>}

        {canEdit && (
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={onCancel} className="px-4 py-2 rounded-full border">Cancel</button>
            <button
              onClick={() => mutation.mutate()}
              disabled={mutation.isPending}
              className="px-4 py-2 rounded-full bg-brand-600 text-white disabled:opacity-50"
            >
              {mutation.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, value, onChange, disabled }) {
  return (
    <div>
      <label className="text-xs text-slate-600 uppercase">{label}</label>
      <input
        className="w-full border rounded-md px-3 py-2 mt-1 disabled:bg-gray-50"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function TextAreaField({ label, value, onChange, disabled }) {
  return (
    <div>
      <label className="text-xs text-slate-600 uppercase">{label}</label>
      <textarea
        className="w-full border rounded-md px-3 py-2 mt-1 disabled:bg-gray-50"
        rows={4}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
