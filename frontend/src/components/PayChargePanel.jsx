import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { readError } from "../api/errors";
import { useToast } from "./Toaster.jsx";
import { Button, Field, Input, Select, naira } from "./ui.jsx";

// Take money for **one bill** — `POST /payments/` with `charge`, which
// `billing.services.record_payment` settles on that charge alone rather than
// spreading it over the patient's other bills oldest-first.
//
// It exists because a prescription unlocks at the pharmacy on its own charge
// (`pharmacy.services.dispensing_clearance`): money paid "for the Amoxicillin"
// has to land on the Amoxicillin, not on last month's consultation. The same
// `Payment` row and allocation as every other payment — one payment system,
// told where the money goes. The server caps it at what the bill still owes;
// less than that is a part payment of this bill.
//
// Rendered by the Billing counter's charge rows and the pharmacy's dispensing
// queue, so the two desks take money for a script the same way.

export default function PayChargePanel({ charge, patient, outstanding, description, onDone, onCancel }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [amount, setAmount] = useState(String(outstanding ?? ""));
  const [method, setMethod] = useState("cash");
  const [error, setError] = useState(null);

  const pay = useMutation({
    mutationFn: () => api.post("/payments/", { patient, charge, amount, method }),
    onSuccess: (response) => {
      // Everything the money touched: the bill, the account, the script's
      // own status at the pharmacy, and the cash desk's figures.
      for (const key of ["charges", "ledger", "ledgers", "payments", "prescriptions",
                         "dashboard", "finance-report", "unread-count"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      showToast({ title: "Payment received",
                  message: `${naira(response.data.amount)} for ${description}` });
      onDone?.(response.data);
    },
    onError: (err) => setError(readError(err, "Could not record this payment.")),
  });

  return (
    <form
      aria-label={`Pay for ${description}`}
      onSubmit={(e) => { e.preventDefault(); setError(null); if (Number(amount) > 0) pay.mutate(); }}
      className="mt-2 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3"
    >
      <p className="text-sm font-medium text-slate-800">
        Pay for {description} — {naira(outstanding)} owed on this bill
      </p>
      <p className="text-xs text-slate-600">
        Settles this bill only. The patient's other bills are not touched.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-36">
          <Field label="Amount">
            <Input type="number" min="0.01" step="0.01" max={outstanding} value={amount}
                   onChange={(e) => setAmount(e.target.value)} />
          </Field>
        </div>
        <div className="w-36">
          <Field label="Method">
            <Select value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="cash">Cash</option>
              <option value="card">Card</option>
              <option value="transfer">Transfer</option>
              <option value="insurance">Insurance</option>
            </Select>
          </Field>
        </div>
        <Button type="submit" size="sm" disabled={!(Number(amount) > 0) || pay.isPending}>
          {pay.isPending ? "Recording…" : `Take ${naira(Number(amount) || 0)}`}
        </Button>
        {onCancel && (
          <Button type="button" variant="linkMuted" size="sm" onClick={onCancel}>Cancel</Button>
        )}
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
    </form>
  );
}
