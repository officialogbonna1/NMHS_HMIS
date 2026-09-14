import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { patientNumber } from "./patientIdentity.js";
import { useToast } from "./Toaster.jsx";
import { refundProblem, refundState } from "./refundPolicy.js";
import { Alert, Button, Field, Input, Modal, Select, Textarea } from "./ui.jsx";

// Handing money back from a payment.
//
// Its own dialog rather than another button on a row, because a refund is the
// only billing action that takes cash *out* of the drawer and the screen
// should say so before anybody types an amount.
//
// The original payment is shown at the top, unchanged and labelled as such:
// the thing a cashier most needs to be sure of is that they are refunding
// *this* payment, not reversing it out of existence. Everything the form
// refuses, the server refuses again — `billing.services.refund_payment` is
// what actually decides — so this only spares the desk a round trip.
//
// This is the flexible refund: any amount up to what is left, and the bill the
// money came off stays active and is owed again. Cancelling an unused service
// and returning *all* of its money is a different decision with a different
// dialog — `CancelServiceModal` — and never passes through here.
const currency = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", minimumFractionDigits: 2 })
    .format(Number(n ?? 0));

const METHODS = [
  ["cash", "Cash"], ["transfer", "Transfer"], ["card", "Card"], ["insurance", "Insurance"],
];

export default function RefundModal({ payment, patient, onClose, onRefunded }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { refunded, refundable, amount: paid } = refundState(payment, null);

  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  // Back the way it came, unless the desk says otherwise — which is what a
  // counter does in practice.
  const [method, setMethod] = useState(payment?.method ?? "cash");
  const [reference, setReference] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [serverError, setServerError] = useState(null);

  const problem = useMemo(
    () => refundProblem({ amount, reason, confirmed, refundable }),
    [amount, reason, confirmed, refundable],
  );

  // The submit button carries the figure as soon as a sensible one is typed,
  // not only once the reason and the tick are in: what the cashier is about to
  // hand over is the thing to keep in front of them while they finish the
  // form, and a label that appears at the last keystroke reads as a glitch.
  const typed = Number(amount);
  const showsAmount = amount !== "" && Number.isFinite(typed) && typed > 0;

  const patientName = patient
    ? `${patient.last_name}, ${patient.first_name}`
    : payment?.patient_name ?? "this patient";
  const number = patientNumber(patient) ?? payment?.patient_number ?? null;

  const refund = useMutation({
    mutationFn: () => api.post(`/payments/${payment.id}/refund/`, {
      amount, reason: reason.trim(), method,
      ...(reference.trim() ? { reference: reference.trim() } : {}),
    }).then((r) => r.data),
    onSuccess: (data) => {
      // Everything a refund moves: the patient's balance and the bills it
      // reopened, the payment rows (which now carry a smaller refundable
      // balance), the register, the debtors list, the dashboards and the
      // finance report. Prefix keys, so `["payments", "patient", 12]` is
      // matched by `["payments"]` and no screen is left showing the figure
      // from before.
      for (const key of ["ledger", "ledgers", "charges", "payments", "adjustments",
                         "refunds", "dashboard", "finance-report",
                         "unread-count", "notifications"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      const pk = patient?.id ?? payment?.patient;
      if (pk) queryClient.invalidateQueries({ queryKey: ["patient-overview", String(pk)] });

      showToast({
        title: `Refund of ${currency(data?.refund?.amount ?? amount)} processed successfully.`,
        message: `${patientName}${number ? ` · ${number}` : ""} — the original payment of `
          + `${currency(paid)} stays on the record.`,
      });
      onRefunded?.(data);
      onClose?.();
    },
    onError: (error) => {
      // The server's own words wherever it gave them: it knows things this
      // form cannot, such as another cashier having refunded the rest a
      // second ago.
      const body = error?.response?.data ?? {};
      setServerError(
        body.detail
        ?? body.amount?.[0] ?? body.reason?.[0] ?? body.method?.[0]
        ?? (error?.response?.status === 403
              ? "You are not authorised to refund a payment."
              : "The refund was refused."),
      );
    },
  });

  if (!payment) return null;

  return (
    <Modal
      open
      onClose={onClose}
      title="Refund this payment"
      description="The original payment stays on the record. The refund is recorded beside it."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            disabled={!!problem || refund.isPending}
            loading={refund.isPending}
            loadingText="Refunding…"
            onClick={() => { setServerError(null); refund.mutate(); }}
          >
            {showsAmount ? `Refund ${currency(typed)}` : "Refund"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Alert tone="warning" title="This is a refund, not a correction">
          Money goes back to the patient. The bill it settled is reopened and will show as
          owing again — it does <strong>not</strong> cancel the service. Nothing about the
          original payment is changed or removed.
        </Alert>

        <dl className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
          <div className="flex justify-between gap-4 py-1">
            <dt className="text-slate-600">Patient</dt>
            <dd className="text-right font-medium text-slate-900">
              {patientName}
              {number && <span className="block text-xs font-normal text-slate-600">{number}</span>}
            </dd>
          </div>
          <div className="flex justify-between gap-4 py-1">
            <dt className="text-slate-600">Original payment</dt>
            <dd className="font-semibold tabular-nums text-slate-900">{currency(paid)}</dd>
          </div>
          <div className="flex justify-between gap-4 py-1">
            <dt className="text-slate-600">Taken</dt>
            <dd className="text-right text-slate-800">
              {payment.created_at ? new Date(payment.created_at).toLocaleString() : "—"}
              {payment.method ? ` · ${payment.method}` : ""}
              {payment.received_by_name ? ` · ${payment.received_by_name}` : ""}
            </dd>
          </div>
          <div className="flex justify-between gap-4 py-1">
            <dt className="text-slate-600">Already refunded</dt>
            <dd className={`font-medium tabular-nums ${refunded > 0 ? "text-amber-800" : "text-slate-800"}`}>
              {currency(refunded)}
            </dd>
          </div>
          <div className="mt-1 flex justify-between gap-4 border-t border-slate-200 pt-2">
            <dt className="font-medium text-slate-700">Remaining refundable</dt>
            <dd className="font-semibold tabular-nums text-slate-900">{currency(refundable)}</dd>
          </div>
        </dl>

        <Field label="Amount to refund" required>
          <Input
            type="number" step="0.01" min="0" max={refundable} autoFocus
            value={amount} onChange={(e) => setAmount(e.target.value)}
            placeholder="0.00"
          />
        </Field>
        <Button size="xs" variant="link" className="-mt-1 -ml-2"
                onClick={() => setAmount(String(refundable))}>
          Refund everything refundable ({currency(refundable)})
        </Button>

        <Field label="Reason for the refund" required
               hint="Kept on the record permanently and shown on the patient's statement.">
          <Textarea
            rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder="Billed twice, overcharged, goodwill…"
          />
        </Field>

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

        <label className="flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">
          <input
            type="checkbox" className="mt-0.5 h-4 w-4" checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>
            I confirm {amount ? currency(Number(amount)) : "this amount"} is being handed
            back to {patientName}.
          </span>
        </label>

        {serverError && <Alert tone="danger" title="Refund refused">{serverError}</Alert>}
        {!serverError && problem && amount !== "" && (
          <p className="text-sm text-slate-600">{problem}</p>
        )}
      </div>
    </Modal>
  );
}
