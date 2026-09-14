import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { patientNumber } from "./patientIdentity.js";
import { useToast } from "./Toaster.jsx";
import { cancellationProblem } from "./refundPolicy.js";
import { Alert, Button, Field, Input, Modal, Select, Textarea } from "./ui.jsx";

// Cancelling a service the patient never received.
//
// Two modes, one dialog, because they are the same decision — "this service is
// not happening" — differing only in whether money was already taken for it:
//
//   "cancel"             — `POST /charges/<id>/cancel/`. Nothing was paid, so
//                          nothing moves: no amount, no method, no reference.
//   "cancel_and_refund"  — `POST /charges/<id>/cancel-and-refund/`. The bill is
//                          withdrawn *and* everything paid for it goes back, in
//                          one transaction.
//
// **The refund here is never an amount somebody types.** Cancelling withdraws
// the whole bill, so it returns the whole of what was paid for it; a smaller
// refund would leave the patient in credit against a bill nobody owes. The
// figure is shown, read-only, and sent back only as a confirmation — if another
// cashier changed it after the page loaded, the server answers
// `full_refund_required` and the desk sees the new one. Giving back *part* of a
// payment is `RefundModal`, on the Refunds desk, and leaves the service active.
const currency = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", minimumFractionDigits: 2 })
    .format(Number(n ?? 0));

const METHODS = [
  ["", "The way it was paid"], ["cash", "Cash"], ["transfer", "Transfer"], ["card", "Card"],
  ["insurance", "Insurance"],
];

// Everything a cancellation can move: the balance, the bill, the refund
// register, the counts heading both desks, the dashboards, the report, the bell.
const TOUCHED = ["ledger", "ledgers", "charges", "payments", "adjustments", "refunds",
                 "dashboard", "finance-report", "unread-count", "notifications"];

export default function CancelServiceModal({ charge, patient, mode = "cancel", onClose, onCancelled }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const withRefund = mode === "cancel_and_refund";

  const [reason, setReason] = useState("");
  const [method, setMethod] = useState("");
  const [reference, setReference] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [serverError, setServerError] = useState(null);
  const [errorCode, setErrorCode] = useState(null);
  const [needsRefund, setNeedsRefund] = useState(null);

  const problem = useMemo(() => cancellationProblem({ reason, confirmed }), [reason, confirmed]);

  const refundable = Number(charge?.refundable_amount ?? charge?.amount_paid ?? 0);
  const refunded = Number(charge?.amount_refunded ?? 0);
  const patientName = patient
    ? `${patient.last_name}, ${patient.first_name}`
    : charge?.patient_name ?? "this patient";
  // The desk lists every patient's bills and opens this without a patient
  // record, so the charge row's own number is the fallback — `||`, because
  // `patientNumber` answers an empty string rather than null for no patient.
  const number = (patient ? patientNumber(patient) : null) || charge?.patient_number || null;
  const who = `${patientName}${number ? ` · ${number}` : ""}`;

  const submit = useMutation({
    mutationFn: () => (withRefund
      ? api.post(`/charges/${charge.id}/cancel-and-refund/`, {
        reason: reason.trim(),
        // A confirmation of the figure on screen, not a choice of amount.
        amount: refundable.toFixed(2),
        ...(method ? { method } : {}),
        ...(reference.trim() ? { reference: reference.trim() } : {}),
      })
      : api.post(`/charges/${charge.id}/cancel/`, { reason: reason.trim() })
    ).then((r) => r.data),
    onSuccess: (data) => {
      for (const key of TOUCHED) queryClient.invalidateQueries({ queryKey: [key] });
      const pk = patient?.id ?? charge?.patient;
      if (pk) queryClient.invalidateQueries({ queryKey: ["patient-overview", String(pk)] });
      showToast(withRefund
        ? {
          title: `${charge.description} cancelled and ${currency(data?.amount_refunded ?? refundable)} refunded.`,
          message: `${who} — the patient now owes ${currency(data?.charge?.outstanding ?? 0)} on this `
                   + "service. The charge, the original payment and the refund all stay on the record.",
        }
        : {
          title: `${charge.description} cancelled.`,
          message: `${who} — the patient no longer owes ${currency(charge.amount)} for this service. `
                   + "The charge stays on the record.",
        });
      onCancelled?.(data);
      onClose?.();
    },
    onError: (error) => {
      const body = error?.response?.data ?? {};
      setErrorCode(body.code ?? null);
      if (body.code === "refund_required") setNeedsRefund(body.refundable);
      // The figure moved under us: fetch the bill again so the desk is looking
      // at what the server is looking at before anybody presses again.
      if (body.code === "full_refund_required") {
        queryClient.invalidateQueries({ queryKey: ["charges"] });
      }
      setServerError(
        body.detail ?? body.reason?.[0]
        ?? (error?.response?.status === 403
              ? (withRefund
                ? "You are not authorised to cancel a service and refund its payment."
                : "You are not authorised to cancel a service.")
              : "This service could not be cancelled."),
      );
    },
  });

  if (!charge) return null;
  const submitLabel = withRefund ? `Cancel & refund ${currency(refundable)}` : "Cancel service";

  return (
    <Modal
      open
      onClose={onClose}
      title={withRefund ? "Cancel service and refund" : "Cancel this service"}
      description={withRefund
        ? "The bill is withdrawn and everything paid for it goes back, together."
        : "The patient stops being financially responsible for it."}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Keep the charge</Button>
          <Button
            variant="danger" disabled={!!problem || submit.isPending}
            loading={submit.isPending} loadingText={withRefund ? "Cancelling and refunding…" : "Cancelling…"}
            onClick={() => { setServerError(null); setErrorCode(null); setNeedsRefund(null); submit.mutate(); }}
          >
            {submitLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {withRefund ? (
          <Alert tone="warning" title="This cancels the service and returns the money">
            {patientName} stops owing for <strong>{charge.description}</strong>, and the full{" "}
            {currency(refundable)} paid for it goes back. The charge, the original payment and the
            refund all stay on the record.
          </Alert>
        ) : (
          <Alert tone="warning" title="This withdraws the bill">
            The charge stays on the record with its original amount, department and date — it
            simply stops being owed. No money moves, because nothing has been paid against it.
          </Alert>
        )}

        <dl className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
          <Row label="Patient">
            <span className="font-medium text-slate-900">{patientName}</span>
            {number && <span className="block text-xs font-normal text-slate-600">{number}</span>}
          </Row>
          <Row label="Service">
            <span className="font-medium text-slate-900">{charge.description}</span>
            {charge.department_name && (
              <span className="block text-xs font-normal text-slate-600">{charge.department_name}</span>
            )}
          </Row>
          <Row label="Original charge">
            <span className="font-semibold tabular-nums text-slate-900">{currency(charge.amount)}</span>
          </Row>
          <Row label="Amount paid">
            <span className="tabular-nums text-slate-800">{currency(charge.amount_paid)}</span>
          </Row>
          {withRefund && (
            <>
              <Row label="Already refunded">
                <span className={`tabular-nums ${refunded > 0 ? "text-amber-800" : "text-slate-800"}`}>
                  {currency(refunded)}
                </span>
              </Row>
              <Row label="Refund now" strong>
                <span className="font-semibold tabular-nums text-amber-800">{currency(refundable)}</span>
                <span className="block text-xs font-normal text-slate-600">
                  Everything paid for this service
                </span>
              </Row>
              <Row label="Outstanding now">
                <span className="tabular-nums text-slate-800">{currency(charge.outstanding)}</span>
              </Row>
            </>
          )}
          <div className="mt-1 flex justify-between gap-4 border-t border-slate-200 pt-2">
            <dt className="font-medium text-slate-700">Outstanding after cancelling</dt>
            <dd className="font-semibold tabular-nums text-emerald-700">{currency(0)}</dd>
          </div>
        </dl>

        <Field label="Why is this service being cancelled?" required
               hint="Kept on the record permanently, against your name.">
          <Textarea
            rows={3} value={reason} autoFocus onChange={(e) => setReason(e.target.value)}
            placeholder="Test never run, patient did not attend, billed in error…"
          />
        </Field>

        {withRefund && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="How it is going back">
              <Select value={method} onChange={(e) => setMethod(e.target.value)}>
                {METHODS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </Select>
            </Field>
            <Field label="Reference" hint="Optional — a transfer or receipt number.">
              <Input value={reference} onChange={(e) => setReference(e.target.value)}
                     placeholder="e.g. TRF-88213" />
            </Field>
          </div>
        )}

        <label className="flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">
          <input
            type="checkbox" className="mt-0.5 h-4 w-4" checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          {withRefund ? (
            <span>
              <strong>Cancel {charge.description} and refund {currency(refundable)}?</strong> This
              ends {patientName}&rsquo;s financial responsibility for this service and returns the
              full amount paid for it.
            </span>
          ) : (
            <span>
              <strong>Cancel {charge.description}?</strong> This will end {patientName}&rsquo;s
              financial responsibility for this service.
            </span>
          )}
        </label>

        {serverError && (
          <Alert tone="danger"
                 title={errorCode === "full_refund_required" ? "The amount has changed" : "Nothing was cancelled"}>
            {serverError}
            {needsRefund && (
              <p className="mt-1.5">
                Use <strong>Cancel &amp; refund</strong> instead, so the{" "}
                {currency(needsRefund)} already paid goes back to the patient.
              </p>
            )}
          </Alert>
        )}
      </div>
    </Modal>
  );
}

function Row({ label, strong = false, children }) {
  return (
    <div className="flex justify-between gap-4 py-1">
      <dt className={strong ? "font-medium text-slate-700" : "text-slate-600"}>{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}
