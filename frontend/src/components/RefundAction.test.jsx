import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RefundAction from "./RefundAction.jsx";
import api from "../api/client";
import { PATIENT, makePayment, renderWithApp } from "../test/harness.jsx";

// The refund entry point as a cashier actually meets it: a button that says
// what is left, a dialog that shows the payment it is about to reverse, and
// the round trip to `POST /payments/<id>/refund/` — the endpoint the backend
// already had, which this never goes around.
const CASHIER = { id: 1, username: "cash", role: "cashier" };

// The dialog holds two controls with "Refund" in the name — the footer's
// submit and the "refund everything refundable" shortcut. This is the submit.
const SUBMIT = /^Refund( ₦[\d,.]+)?$/;

beforeEach(() => {
  vi.spyOn(api, "post").mockResolvedValue({
    data: { refund: { id: 1, amount: "3000.00" }, payment: makePayment({ amount_refunded: "3000.00" }) },
  });
});
afterEach(() => vi.restoreAllMocks());

async function openModal(payment = makePayment(), user = CASHIER) {
  const rendered = renderWithApp(
    <RefundAction payment={payment} patient={PATIENT} />, { user },
  );
  await userEvent.click(screen.getByRole("button", { name: /refund/i }));
  return rendered;
}

describe("the button", () => {
  it("offers the whole payment when none of it has gone back", () => {
    renderWithApp(<RefundAction payment={makePayment()} patient={PATIENT} />, { user: CASHIER });
    expect(screen.getByRole("button", { name: "Refund ₦10,000" })).toBeInTheDocument();
  });

  it("offers only what is left after a partial refund", () => {
    renderWithApp(
      <RefundAction payment={makePayment({ amount_refunded: "3000.00" })} patient={PATIENT} />,
      { user: CASHIER },
    );
    expect(screen.getByRole("button", { name: "Refund ₦7,000" })).toBeInTheDocument();
  });

  it("says why, rather than offering a dead button, once it is fully refunded", () => {
    renderWithApp(
      <RefundAction payment={makePayment({ amount_refunded: "10000.00" })} patient={PATIENT} />,
      { user: CASHIER },
    );
    expect(screen.getByText("Fully refunded")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refund ₦/i })).not.toBeInTheDocument();
  });

  it("shows nothing at all to a role the API would refuse", () => {
    for (const role of ["reception", "pharmacist", "doctor", "nurse"]) {
      const { unmount } = renderWithApp(
        <RefundAction payment={makePayment()} patient={PATIENT} />,
        { user: { role } },
      );
      expect(screen.queryByRole("button", { name: /refund/i })).not.toBeInTheDocument();
      expect(screen.queryByText("Fully refunded")).not.toBeInTheDocument();
      unmount();
    }
  });
});

describe("the dialog", () => {
  it("opens carrying the patient and the payment it is about to reverse", async () => {
    await openModal(makePayment({ amount_refunded: "3000.00" }));
    const dialog = within(screen.getByRole("dialog"));

    // Named on the payment block and again in the confirmation sentence.
    expect(dialog.getAllByText(/Obi, Ada/).length).toBeGreaterThan(0);
    expect(dialog.getByText("NMHS-P000012")).toBeInTheDocument();
    expect(dialog.getByText("Original payment").closest("div"))
      .toHaveTextContent("₦10,000.00");
    expect(dialog.getByText("Already refunded").closest("div"))
      .toHaveTextContent("₦3,000.00");
    expect(dialog.getByText("Remaining refundable").closest("div"))
      .toHaveTextContent("₦7,000.00");
    // It says plainly that the original stays.
    expect(dialog.getByText(/nothing about the original payment is changed or removed/i))
      .toBeInTheDocument();
  });

  it("will not submit until an amount, a reason and the confirmation are all given", async () => {
    await openModal();
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByRole("button", { name: SUBMIT })).toBeDisabled();

    await userEvent.type(dialog.getByLabelText(/amount to refund/i), "3000");
    // The figure is on the button from the moment it is typed…
    expect(dialog.getByRole("button", { name: "Refund ₦3,000.00" })).toBeDisabled();

    await userEvent.type(dialog.getByLabelText(/reason for the refund/i), "Overcharged");
    expect(dialog.getByRole("button", { name: SUBMIT })).toBeDisabled();

    // …and only the confirmation finally enables it.
    await userEvent.click(dialog.getByRole("checkbox"));
    expect(dialog.getByRole("button", { name: "Refund ₦3,000.00" })).toBeEnabled();
  });

  it("refuses more than is left on the payment without asking the server", async () => {
    await openModal(makePayment({ amount_refunded: "3000.00" }));
    const dialog = within(screen.getByRole("dialog"));
    await userEvent.type(dialog.getByLabelText(/amount to refund/i), "7001");
    await userEvent.type(dialog.getByLabelText(/reason for the refund/i), "Too much");
    await userEvent.click(dialog.getByRole("checkbox"));

    expect(dialog.getByRole("button", { name: SUBMIT })).toBeDisabled();
    expect(dialog.getByText(/more than is left to refund/i)).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("fills the whole remaining balance in one press", async () => {
    await openModal(makePayment({ amount_refunded: "3000.00" }));
    const dialog = within(screen.getByRole("dialog"));
    await userEvent.click(dialog.getByRole("button", { name: /refund everything refundable/i }));
    expect(dialog.getByLabelText(/amount to refund/i)).toHaveValue(7000);
  });
});

describe("submitting", () => {
  async function submit(dialog, { amount = "3000", reason = "Overcharged" } = {}) {
    await userEvent.type(dialog.getByLabelText(/amount to refund/i), amount);
    await userEvent.type(dialog.getByLabelText(/reason for the refund/i), reason);
    await userEvent.click(dialog.getByRole("checkbox"));
    await userEvent.click(dialog.getByRole("button", { name: SUBMIT }));
  }

  it("calls the existing refund endpoint with the amount and the reason", async () => {
    await openModal();
    await submit(within(screen.getByRole("dialog")));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe("/payments/7/refund/");
    expect(body).toMatchObject({ amount: "3000", reason: "Overcharged", method: "cash" });
  });

  it("sends a reference only when one was typed", async () => {
    await openModal();
    const dialog = within(screen.getByRole("dialog"));
    await userEvent.type(dialog.getByLabelText(/reference/i), "TRF-88213");
    await submit(dialog);

    await waitFor(() => expect(api.post).toHaveBeenCalled());
    expect(api.post.mock.calls[0][1]).toMatchObject({ reference: "TRF-88213" });
  });

  it("confirms the refund and says the original payment is still there", async () => {
    await openModal();
    await submit(within(screen.getByRole("dialog")));

    await waitFor(() =>
      expect(screen.getByText(/Refund of ₦3,000\.00 processed successfully\./))
        .toBeInTheDocument());
    expect(screen.getByText(/the original payment of ₦10,000\.00 stays on the record/i))
      .toBeInTheDocument();
    // …and the dialog closes behind it.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("refreshes the balance, the bills, the register and the finance report", async () => {
    const rendered = renderWithApp(
      <RefundAction payment={makePayment()} patient={PATIENT} />, { user: CASHIER },
    );
    const invalidate = vi.spyOn(rendered.client, "invalidateQueries");
    await userEvent.click(screen.getByRole("button", { name: /refund ₦/i }));
    await submit(within(screen.getByRole("dialog")));

    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    const keys = invalidate.mock.calls.map((call) => call[0].queryKey[0]);
    for (const key of ["ledger", "ledgers", "charges", "payments", "adjustments",
                       "refunds", "dashboard", "finance-report", "unread-count"]) {
      expect(keys).toContain(key);
    }
    // The chart's own cache is keyed on the integer pk.
    expect(invalidate.mock.calls.map((c) => c[0].queryKey))
      .toContainEqual(["patient-overview", "3"]);
  });

  it("shows the server's refusal rather than a generic failure", async () => {
    api.post.mockRejectedValueOnce({
      response: { status: 400, data: { detail: "Only 500.00 of this payment is still refundable.", code: "refund_refused" } },
    });
    await openModal();
    const dialog = within(screen.getByRole("dialog"));
    await submit(dialog);

    await waitFor(() =>
      expect(screen.getByText(/Only 500\.00 of this payment is still refundable\./))
        .toBeInTheDocument());
    // The dialog stays open so the amount can be corrected, and nothing claims
    // success.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText(/processed successfully/i)).not.toBeInTheDocument();
  });

  it("names the permission when the API refuses the role outright", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 403, data: {} } });
    await openModal();
    await submit(within(screen.getByRole("dialog")));

    await waitFor(() =>
      expect(screen.getByText(/not authorised to refund a payment/i)).toBeInTheDocument());
  });
});
