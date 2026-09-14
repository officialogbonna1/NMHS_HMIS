import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Refunds from "./Refunds.jsx";
import api from "../api/client";
import { makePayment, PATIENT, renderWithApp } from "../test/harness.jsx";

// The Refunds desk: money going back to a patient. Find the patient, find the
// payment, refund all or part of what is left — through the one refund entry
// point every other screen uses.
const CASHIER = { id: 1, username: "cash", role: "cashier" };

const refund = (over = {}) => ({
  id: 41, payment: 7, payment_amount: "10000.00", payment_date: "2026-09-01T10:30:00Z",
  patient: 3, patient_name: "Obi, Ada", patient_number: "NMHS-P000012",
  amount: "3000.00", reason: "Overcharged", method: "cash", method_label: "Cash",
  reference: "RCP-9", processed_by_name: "Ada Bello", from_cancellation: false,
  allocations: [{ charge: 11, charge_description: "General Consultation", department_name: "Consultation", amount: "3000.00" }],
  created_at: "2026-09-02T11:00:00Z",
  ...over,
});

let payments;
let register;

const lastParams = (url) => {
  const calls = api.get.mock.calls.filter(([u]) => u === url);
  return calls[calls.length - 1]?.[1]?.params;
};

beforeEach(() => {
  payments = [
    makePayment({ amount_refunded: "3000.00" }),
    makePayment({ id: 8, amount: "2000.00", amount_refunded: "2000.00" }),
  ];
  register = [refund(), refund({ id: 42, amount: "4000.00", reason: "Test never run",
                                 from_cancellation: true, reference: "" })];
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url === "/refunds/summary/") {
      return Promise.resolve({ data: { today: { count: 1, amount: "3000.00" },
                                       month: { count: 2, amount: "7000.00" } } });
    }
    if (url === "/patients/") return Promise.resolve({ data: [PATIENT] });
    if (url === "/payments/") return Promise.resolve({ data: payments });
    if (url === "/refunds/") {
      const patient = config?.params?.patient;
      return Promise.resolve({ data: patient ? register.slice(0, 1)
                                             : { count: register.length, results: register } });
    }
    return Promise.resolve({ data: [] });
  });
  vi.spyOn(api, "post").mockResolvedValue({
    data: { refund: { amount: "2000.00" }, payment: makePayment({ amount_refunded: "5000.00" }) },
  });
});
afterEach(() => vi.restoreAllMocks());

async function pickPatient(user = CASHIER) {
  renderWithApp(<Refunds />, { user });
  await userEvent.click(screen.getByRole("button", { name: /select a patient/i }));
  await userEvent.click(await screen.findByRole("option", { name: /Obi, Ada/ }));
  return within(await screen.findByRole("list", { name: "Payments" }));
}

describe("the page", () => {
  it("says it is about money going back, and shows the day's refunds", async () => {
    renderWithApp(<Refunds />, { user: CASHIER });
    expect(screen.getByRole("heading", { name: "Refunds" })).toBeInTheDocument();
    expect(screen.getByText(/Money going back to a patient/)).toBeInTheDocument();
    expect(await screen.findByText("refunded today")).toBeInTheDocument();
    expect(screen.getByText("refunded today").previousSibling).toHaveTextContent("₦3,000");
    expect(screen.getByText("this month").previousSibling).toHaveTextContent("₦7,000");
  });

  it("opens on a patient dropdown that stays closed until it is clicked", async () => {
    renderWithApp(<Refunds />, { user: CASHIER });
    expect(screen.getByRole("button", { name: "Refund a payment" })).toHaveAttribute("aria-current", "page");
    const trigger = screen.getByRole("button", { name: /select a patient/i });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(api.get.mock.calls.some(([url]) => url === "/patients/")).toBe(false);

    await userEvent.click(trigger);
    // Open: a search box for the identifiers staff actually have, and the list.
    expect(screen.getByRole("combobox", { name: /search patients/i }))
      .toHaveAttribute("placeholder", expect.stringMatching(/NMHS number, name or phone/i));
    expect(await screen.findByRole("option", { name: /Obi, Ada/ })).toBeInTheDocument();
  });

  it("points an unused service at the other desk", async () => {
    renderWithApp(<Refunds />, { user: CASHIER });
    expect(await screen.findByRole("link", { name: "Service Cancellations" }))
      .toHaveAttribute("href", "/service-cancellations");
  });
});

describe("a patient's payments", () => {
  it("lists each payment with what arrived, what went back and what is left", async () => {
    const list = await pickPatient();
    const [first, second] = list.getAllByRole("listitem").map((li) => within(li));
    expect(first.getByText("Payment of ₦10,000")).toBeInTheDocument();
    expect(first.getByText("PARTLY REFUNDED")).toBeInTheDocument();
    expect(first.getByText("Original payment").nextSibling).toHaveTextContent("₦10,000");
    expect(first.getByText("Already refunded").nextSibling).toHaveTextContent("₦3,000");
    expect(first.getByText("Remaining").nextSibling).toHaveTextContent("₦7,000");
    expect(first.getByRole("button", { name: "Refund ₦7,000" })).toBeInTheDocument();
    // Nothing left: a badge that says so, never a dead button.
    expect(second.getByText("FULLY REFUNDED")).toBeInTheDocument();
    expect(second.queryByRole("button", { name: /refund/i })).not.toBeInTheDocument();
    expect(lastParams("/payments/")).toMatchObject({ patient: 3 });
  });

  it("processes a partial refund through the payment refund endpoint", async () => {
    const list = await pickPatient();
    await userEvent.click(list.getByRole("button", { name: "Refund ₦7,000" }));
    const modal = within(screen.getByRole("dialog"));
    await userEvent.type(modal.getByLabelText(/amount to refund/i), "2000");
    await userEvent.type(modal.getByLabelText(/reason for the refund/i), "Overcharged");
    await userEvent.click(modal.getByRole("checkbox"));
    await userEvent.click(modal.getByRole("button", { name: "Refund ₦2,000.00" }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe("/payments/7/refund/");
    expect(body).toMatchObject({ amount: "2000", reason: "Overcharged" });
    expect(await screen.findByText(/Refund of ₦2,000\.00 processed successfully/)).toBeInTheDocument();
  });

  it("processes a full refund of everything left in one press", async () => {
    const list = await pickPatient();
    await userEvent.click(list.getByRole("button", { name: "Refund ₦7,000" }));
    const modal = within(screen.getByRole("dialog"));
    await userEvent.click(modal.getByRole("button", { name: /refund everything refundable/i }));
    await userEvent.type(modal.getByLabelText(/reason for the refund/i), "Billed twice");
    await userEvent.click(modal.getByRole("checkbox"));
    await userEvent.click(modal.getByRole("button", { name: "Refund ₦7,000.00" }));
    await waitFor(() => expect(api.post.mock.calls[0][1]).toMatchObject({ amount: "7000" }));
  });

  it("will not send more than is left on the payment", async () => {
    const list = await pickPatient();
    await userEvent.click(list.getByRole("button", { name: "Refund ₦7,000" }));
    const modal = within(screen.getByRole("dialog"));
    await userEvent.type(modal.getByLabelText(/amount to refund/i), "7001");
    await userEvent.type(modal.getByLabelText(/reason for the refund/i), "Too much");
    await userEvent.click(modal.getByRole("checkbox"));
    expect(modal.getByRole("button", { name: /^Refund ₦/ })).toBeDisabled();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("shows the refunds already made to the patient", async () => {
    await pickPatient();
    const refunds = within(await screen.findByRole("list", { name: "Refunds to this patient" }));
    expect(refunds.getByText("₦3,000 refunded")).toBeInTheDocument();
    expect(refunds.getByText("Overcharged")).toBeInTheDocument();
    expect(lastParams("/refunds/")).toMatchObject({ patient: 3 });
  });

  it("offers no refund to a role the API would refuse", async () => {
    const list = await pickPatient({ id: 9, role: "reception" });
    expect(list.queryByRole("button", { name: /refund/i })).not.toBeInTheDocument();
  });
});

describe("refund history", () => {
  async function openHistory() {
    renderWithApp(<Refunds />, { user: CASHIER });
    await userEvent.click(screen.getByRole("button", { name: "Refund history" }));
    return within(await screen.findByRole("list", { name: "Refund history" }));
  }

  it("lists every refund with its status, reason, method, reference and payment", async () => {
    const list = await openHistory();
    const [plain, cancelled] = list.getAllByRole("listitem").map((li) => within(li));
    expect(plain.getByText("₦3,000 refunded")).toBeInTheDocument();
    expect(plain.getByText("Completed")).toBeInTheDocument();
    expect(plain.getByText(/Obi, Ada/)).toBeInTheDocument();
    expect(plain.getByText("Overcharged")).toBeInTheDocument();
    expect(plain.getByText("Method").nextSibling).toHaveTextContent("Cash");
    expect(plain.getByText("Reference").nextSibling).toHaveTextContent("RCP-9");
    expect(plain.getByText("From payment").nextSibling).toHaveTextContent("₦10,000");
    expect(plain.getByText("Processed by").nextSibling).toHaveTextContent("Ada Bello");
    expect(plain.queryByText("Service cancelled")).not.toBeInTheDocument();
    // A refund that was the money half of a cancellation says so.
    expect(cancelled.getByText("Service cancelled")).toBeInTheDocument();
  });

  it("searches and filters the register on the server", async () => {
    await openHistory();
    await userEvent.type(screen.getByRole("searchbox", { name: /search refunds/i }), "NMHS-P000012");
    await waitFor(() => expect(lastParams("/refunds/")).toMatchObject({ search: "NMHS-P000012" }));
    await userEvent.selectOptions(screen.getByLabelText("Refund method"), "transfer");
    await waitFor(() => expect(lastParams("/refunds/")).toMatchObject({ method: "transfer", page: 1 }));
  });
});
