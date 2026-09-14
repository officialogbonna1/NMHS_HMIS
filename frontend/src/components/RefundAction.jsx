import { useState } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import { Badge, Button } from "./ui.jsx";
import RefundModal from "./RefundModal.jsx";
import { refundState } from "./refundPolicy.js";

const naira = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 })
    .format(Number(n ?? 0));

/**
 * The one refund entry point, rendered wherever a payment is listed.
 *
 * Two screens show it — the Billing counter's payment list and Transaction
 * History — and both get this component rather than each spelling out its own
 * button, its own eligibility test and its own modal. That is what stops the
 * counter offering a refund the statement says is already spent.
 *
 * It is `dangerOutline`, not a text link: a refund takes cash out of the
 * drawer, and it should not look like "Show more". The amount is in the label
 * — "Refund ₦7,000" — because what is *left* to refund is the number the
 * cashier needs before they decide, not after.
 *
 * When there is nothing left it renders a plain badge saying so, rather than a
 * greyed-out button: a disabled control invites clicking to find out why.
 */
export default function RefundAction({ payment, patient, size = "sm", className = "", onRefunded }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const state = refundState(payment, user, { format: naira });

  // Not a role that may refund: the control is not theirs to see. The API
  // refuses them either way (REFUND_ROLES on `payments/<id>/refund/`).
  if (!state.visible) return null;

  if (!state.enabled) {
    return (
      <Badge tone="neutral" className={className} title={`${naira(state.refunded)} already refunded`}>
        {state.reason}
      </Badge>
    );
  }

  return (
    <>
      <Button
        size={size} variant="dangerOutline" className={className}
        onClick={() => setOpen(true)}
      >
        {state.label}
      </Button>
      {open && (
        <RefundModal
          payment={payment}
          patient={patient}
          onClose={() => setOpen(false)}
          onRefunded={onRefunded}
        />
      )}
    </>
  );
}
