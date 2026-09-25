import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "../api/client";
import { errorCode, readError } from "../api/errors";
import { useToast } from "./Toaster.jsx";
import { Alert, Badge, Button, Field, Input, Select, Textarea } from "./ui.jsx";

// **Correcting a maternity record — the note's amendment flow, over the ward's
// records.**
//
// `MedicalNotesTab` set this pattern for `ConsultationNoteAmendment` and this
// is that pattern, not a maternity editing experience of its own: a record
// reads as a record until **Amend** is pressed, the correction needs a reason
// off the server's own list, and what it used to say stays visible in the
// history rather than being replaced by what it says now.
//
// The screen is a courtesy throughout. `may_amend` on the server decides who
// may correct anything (the person who entered it, or an administrator), the
// reason is required there, and the identity facts are refused there — so a
// hidden button is never the protection (rule 28).

/** Records whose identity facts can never be corrected, for the read-only note. */
export const IMMUTABLE_NOTE =
  "Who this record belongs to, and when it was filed, are part of what it is. "
  + "They cannot be changed — a record entered against the wrong pregnancy is "
  + "cancelled and re-entered.";

export function AmendedBadge({ record }) {
  if (!record?.is_amended) return null;
  return <Badge tone="warning">Amended</Badge>;
}

/**
 * The record's corrections, newest first.
 *
 * Each row says what changed, from what to what, who changed it, when and
 * why — the `previous` snapshot read against the record as it stands, which
 * the server derives rather than storing (rule 45).
 */
export function AmendmentHistory({ endpoint, recordId }) {
  const { data, isLoading } = useQuery({
    queryKey: ["maternity-amendments", endpoint, recordId],
    queryFn: () => api.get(`/${endpoint}/${recordId}/amendments/`).then((r) => r.data),
    enabled: Boolean(recordId),
  });

  const amendments = data ?? [];
  if (isLoading) return <p className="text-sm text-slate-600">Loading history…</p>;
  if (amendments.length === 0) {
    return (
      <p className="text-sm text-slate-600">
        This record has not been corrected.
      </p>
    );
  }

  return (
    <section aria-label="Amendment history" className="space-y-3">
      <h4 className="text-xs font-bold uppercase tracking-wider text-slate-600">
        Amendment history
      </h4>
      <ol className="space-y-3">
        {amendments.map((amendment) => (
          <li key={amendment.id}
              className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
            <p className="text-sm font-medium text-slate-900">
              {amendment.reason_label}
            </p>
            {amendment.detail && (
              <p className="mt-0.5 text-sm text-slate-700">{amendment.detail}</p>
            )}
            <ul className="mt-2 space-y-1">
              {(amendment.changes ?? []).map((change) => (
                <li key={change.field} className="text-sm text-slate-800">
                  <span className="font-medium">{change.label}</span>
                  {" · "}
                  {/* The original is struck through rather than removed: the
                      point of a trail is that the old value is still there. */}
                  <span className="text-slate-600 line-through">
                    {formatValue(change.from)}
                  </span>
                  {" → "}
                  <span className="font-medium">{formatValue(change.to)}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-600">
              {amendment.amended_by_name ?? "Unknown"} · {formatWhen(amendment.created_at)}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}

/**
 * The correction itself: the fields this record lets you change, plus the
 * reason the server insists on.
 *
 * `fields` is what the caller says is amendable — the server refuses anything
 * else, so this list is about what the form offers, never about what is safe.
 */
export function AmendRecordForm({ endpoint, record, fields, onDone, onCancel }) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [values, setValues] = useState(() =>
    Object.fromEntries(fields.map((f) => [f.name, record[f.name] ?? ""])));
  const [reason, setReason] = useState("");
  const [detail, setDetail] = useState("");
  const [failed, setFailed] = useState(null);

  const { data: reasons } = useQuery({
    queryKey: ["maternity-amendment-reasons", endpoint],
    queryFn: () => api.get(`/${endpoint}/amendment-reasons/`).then((r) => r.data),
  });

  const save = useMutation({
    mutationFn: () => api.patch(`/${endpoint}/${record.id}/`, {
      ...values, amendment_reason: reason, amendment_detail: detail,
    }),
    onSuccess: (response) => {
      setFailed(null);
      queryClient.invalidateQueries({ queryKey: ["maternity-amendments", endpoint, record.id] });
      showToast({ title: "Record corrected",
                  message: "What it said before is kept in the amendment history." });
      onDone?.(response.data);
    },
    onError: (err) => setFailed({
      code: errorCode(err),
      message: readError(err, "Could not correct this record."),
    }),
  });

  return (
    <form
      aria-label="Amend record"
      onSubmit={(e) => { e.preventDefault(); save.mutate(); }}
      className="space-y-4 rounded-xl border border-slate-200 bg-white p-4"
    >
      <div>
        <h3 className="font-semibold text-slate-900">Correct this record</h3>
        <p className="mt-0.5 text-sm text-slate-600">
          What it says now is kept in the amendment history, with your name and reason
          against it. {IMMUTABLE_NOTE}
        </p>
      </div>

      {failed && (
        <Alert tone="danger"
               title={failed.code === "immutable_field"
                 ? "That cannot be changed" : "Could not correct this record"}>
          {failed.message}
        </Alert>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <Field key={field.name} label={field.label}>
            {/* The shared `Input`, which takes the id `Field` puts in
                context — a raw <input> is not labelled by it. */}
            <Input
              type={field.type ?? "text"}
              value={values[field.name] ?? ""}
              onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
            />
          </Field>
        ))}
      </div>

      <Field label="Why is this being corrected?" hint="Required — the record keeps it.">
        <Select value={reason} onChange={(e) => setReason(e.target.value)} required>
          <option value="">Choose a reason…</option>
          {(reasons ?? []).map((r) => (
            <option key={r.value} value={r.value}>{r.label}</option>
          ))}
        </Select>
      </Field>

      <Field label="Anything else worth recording" hint="Optional.">
        <Textarea rows={2} value={detail} onChange={(e) => setDetail(e.target.value)} />
      </Field>

      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="secondary" type="button" onClick={onCancel}>Cancel</Button>
        <Button type="submit" disabled={!reason || save.isPending}>
          Save correction
        </Button>
      </div>
    </form>
  );
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.join(", ") || "—";
  return String(value);
}

function formatWhen(iso) {
  if (!iso) return "";
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? "" : when.toLocaleString();
}


/**
 * **The amendment controls on a record panel** — the badge, the action and
 * the history, in the one place a record is shown.
 *
 * `record` is the row as the API returned it, and everything this renders is
 * read off it: `can_amend` (the server's own `may_amend`, so the button is
 * never offered where the API would refuse), `is_amended`, and
 * `amendable_fields` — which is why the identity facts are not rendered as
 * editable boxes anywhere. There is no list of fields in this file.
 *
 * `endpoint` is the record's collection (`pregnancies`, `deliveries`, …); the
 * PATCH and the history both hang off it.
 */
export function RecordAmendment({ endpoint, record, onAmended, label = "record" }) {
  const [amending, setAmending] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  if (!record?.id) return null;

  if (amending) {
    return (
      <AmendRecordForm
        endpoint={endpoint}
        record={record}
        fields={(record.amendable_fields ?? []).map((f) => ({
          name: f.name, label: f.label,
        }))}
        onDone={(saved) => { setAmending(false); setShowHistory(true); onAmended?.(saved); }}
        onCancel={() => setAmending(false)}
      />
    );
  }

  return (
    <div className="space-y-2" data-testid={`amendment-${endpoint}`}>
      <div className="flex flex-wrap items-center gap-2">
        <AmendedBadge record={record} />
        {record.is_amended && (
          <span className="text-xs text-slate-600">
            Corrected {record.amendment_count === 1 ? "once" : `${record.amendment_count} times`}
            {record.last_amended_by_name ? ` · last by ${record.last_amended_by_name}` : ""}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {/* Offered only where the server says this reader may correct it —
            the record's own `can_amend`, which is `amendments.may_amend`. */}
        {record.can_amend && (
          <Button variant="secondary" size="xs" onClick={() => setAmending(true)}>
            Amend {label}
          </Button>
        )}
        {(record.is_amended || showHistory) && (
          <Button variant="link" size="xs" onClick={() => setShowHistory((was) => !was)}>
            {showHistory ? "Hide" : "View"} amendment history
          </Button>
        )}
      </div>
      {showHistory && <AmendmentHistory endpoint={endpoint} recordId={record.id} />}
    </div>
  );
}
