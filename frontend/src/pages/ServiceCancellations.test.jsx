import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ServiceCancellations, { listParams } from "./ServiceCancellations.jsx";
import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";

// The Service Cancellations desk as a cashier meets it: the services that need
// action first, a search that takes what the patient says at the window, and
// two clearly different ways to cancel — each ending in the backend service
// that owns the accounting.
const CASHIER = { id: 1, username: "cash", role: "cashier" };

const charge = (over = {}) => ({
  id: 11, patient: 3, patient_name: "Obi, Ada", patient_number: "NMHS-P000012",
  patient_uuid: "8b0e5f1e-1111-4000-8000-000000000000",
  description: "Laboratory: FBC", department_name: "Laboratory",
  amount: "4000.00", amount_paid: "0.00", amount_refunded: "0.00", refundable_amount: "0.00",
  untraceable_amount: "0.00", outstanding: "4000.00", status: "unpaid", is_cancelled: false,
  service_withdrawn: false, created_at: "2026-09-01T09:00:00Z", cancelled_at: null,
  cancellation_reason: "", cancelled_by_name: null,
  ...over,
});

const PAID = charge({ amount_paid: "4000.00", refundable_amount: "4000.00", outstanding: "0.00",
                      status: "paid" });

let rows;
let summary;
let failList;

const lastListParams = () => {
  const calls = api.get.mock.calls.filter(([url]) => url === "/charges/");
  return calls[calls.length - 1]?.[1]?.params;
};

beforeEach(() => {
  rows = [charge()];
  failList = null;
  summary = {
    awaiting: { count: 0, held: "0.00" },
    by_status: { unpaid: 1, partial: 0, paid: 0, waived: 0, cancelled: 0 },
    open: 1,
    cancelled_this_month: { count: 2, value: "6000.00" },
  };
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/charges/cancellation-summary/") return Promise.resolve({ data: summary });
    if (url === "/departments/") return Promise.resolve({ data: [{ id: 5, name: "Laboratory" }] });
    if (url === "/patients/") {
      return Promise.resolve({ data: { count: 1, results: [{ id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000",
                                                             first_name: "Ada", last_name: "Obi",
                                                             patient_number: "NMHS-P000012" }] } });
    }
    if (url === "/charges/") {
      if (failList) return Promise.reject(failList);
      return Promise.resolve({ data: { count: rows.length, results: rows } });
    }
    return Promise.resolve({ data: [] });
  });
  vi.spyOn(api, "post").mockResolvedValue({
    data: { charge: charge({ status: "cancelled", outstanding: "0.00" }),
            amount_refunded: "4000.00", outstanding: "0.00", refunds: [] },
  });
});
afterEach(() => vi.restoreAllMocks());

async function open(user = CASHIER) {
  const rendered = renderWithApp(<ServiceCancellations />, { user });
  await screen.findByText("Laboratory: FBC");
  return rendered;
}

const serviceList = () => within(screen.getByRole("list", { name: "Services" }));

describe("the page", () => {
  it("says what it is for and how the two cancellations differ", async () => {
    await open();
    expect(screen.getByRole("heading", { name: "Service Cancellations" })).toBeInTheDocument();
    expect(screen.getByText(/billed for but never received/i)).toBeInTheDocument();
    const guide = within(screen.getByRole("region", { name: /which cancellation to use/i }));
    expect(guide.getByText("Cancel service")).toBeInTheDocument();
    expect(guide.getByText(/Nothing was paid/)).toBeInTheDocument();
    expect(guide.getByText("Cancel & refund")).toBeInTheDocument();
    expect(guide.getByText(/everything paid for it goes/)).toBeInTheDocument();
  });

  it("heads the page with the desk's figures", async () => {
    summary.awaiting = { count: 3, held: "9000.00" };
    await open();
    expect(await screen.findByText("awaiting cancellation")).toBeInTheDocument();
    expect(screen.getByText("cancelled this month").previousSibling).toHaveTextContent("2");
    expect(screen.getByText("paid on them").previousSibling).toHaveTextContent("₦9,000");
  });

  it("asks the server for withdrawn services first", async () => {
    await open();
    expect(lastListParams()).toMatchObject({ awaiting_first: 1, page: 1, page_size: 25 });
    expect(lastListParams()).not.toHaveProperty("status");
  });

  it("calls out withdrawn services still billed, and shows them on request", async () => {
    summary.awaiting = { count: 2, held: "6000.00" };
    await open();
    expect(await screen.findByText(/2 services were withdrawn but are still billed/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /review them/i }));
    await waitFor(() => expect(lastListParams()).toMatchObject({ awaiting: 1 }));
  });
});

describe("finding a service", () => {
  it("picks the patient from a dropdown that lists patients before anything is typed", async () => {
    await open();
    const trigger = screen.getByRole("button", { name: /select a patient/i });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(trigger);
    // Listed on opening — nothing has to be typed first.
    expect(await screen.findByRole("option", { name: /Obi, Ada/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("option", { name: /Obi, Ada/ }));
    await waitFor(() => expect(lastListParams()).toMatchObject({ patient: 3, page: 1 }));

    // …and cleared the same way it was chosen.
    await userEvent.click(screen.getByRole("button", { name: /^change$/i }));
    await waitFor(() => expect(lastListParams()).not.toHaveProperty("patient"));
  });

  it("also takes a typed NMHS number, name or phone", async () => {
    await open();
    await userEvent.click(screen.getByRole("button", { name: /select a patient/i }));
    await userEvent.type(screen.getByRole("combobox", { name: /search patients/i }), "NMHS-P000012");
    await waitFor(() => {
      const calls = api.get.mock.calls.filter(([url]) => url === "/patients/");
      expect(calls.at(-1)[1].params).toMatchObject({ search: "NMHS-P000012" });
    });
    await userEvent.click(await screen.findByRole("option", { name: /Obi, Ada/ }));
    await waitFor(() => expect(lastListParams()).toMatchObject({ patient: 3 }));
  });

  it("filters by payment state, and by awaiting cancellation", async () => {
    await open();
    const tabs = within(screen.getByRole("navigation", { name: /filter services/i }));
    for (const [label, expected] of [["Paid", { status: "paid" }],
                                     ["Partially paid", { status: "partial" }],
                                     ["Unpaid", { status: "unpaid" }],
                                     ["Cancelled", { status: "cancelled" }],
                                     ["Awaiting cancellation", { awaiting: 1 }]]) {
      await userEvent.click(tabs.getByRole("button", { name: new RegExp(`^${label}`) }));
      await waitFor(() => expect(lastListParams()).toMatchObject(expected));
    }
    await userEvent.click(tabs.getByRole("button", { name: /^All services/ }));
    await waitFor(() => {
      expect(lastListParams()).not.toHaveProperty("status");
      expect(lastListParams()).not.toHaveProperty("awaiting");
    });
  });

  it("filters by department and by the date the service was billed", async () => {
    await open();
    await screen.findByRole("option", { name: "Laboratory" });
    await userEvent.selectOptions(screen.getByLabelText("Department"), "5");
    await userEvent.type(screen.getByLabelText("Billed from"), "2026-09-01");
    await userEvent.type(screen.getByLabelText("Billed to"), "2026-09-30");
    await waitFor(() => expect(lastListParams()).toMatchObject({
      department: "5", created_from: "2026-09-01", created_to: "2026-09-30",
    }));
  });

  it("builds the query the same way the page does", () => {
    expect(listParams({ filter: "awaiting", patient: 3, page: 2 }))
      .toEqual({ awaiting: 1, patient: 3, awaiting_first: 1, page: 2, page_size: 25 });
    expect(listParams({ filter: "all" })).toEqual({ awaiting_first: 1, page: 1, page_size: 25 });
  });

  it("says so when nothing is waiting to be cancelled", async () => {
    await open();
    rows = [];
    await userEvent.click(screen.getByRole("button", { name: /^Awaiting cancellation/ }));
    expect(await screen.findByText(/Nothing is waiting to be cancelled/)).toBeInTheDocument();
  });

  it("shows an error, with a retry, when the list cannot load", async () => {
    failList = { response: { status: 500 } };
    renderWithApp(<ServiceCancellations />, { user: CASHIER });
    expect(await screen.findByText(/services could not be loaded/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

describe("a service row", () => {
  it("carries everything needed to decide", async () => {
    rows = [charge({ amount_paid: "2500.00", refundable_amount: "2000.00", amount_refunded: "500.00",
                     outstanding: "2000.00", status: "partial", service_withdrawn: true })];
    await open();
    const row = within(serviceList().getAllByRole("listitem")[0]);
    expect(row.getByRole("link", { name: "Obi, Ada" }))
      .toHaveAttribute("href", "/patients/8b0e5f1e-1111-4000-8000-000000000000/billing");
    expect(row.getByText(/NMHS-P000012/)).toBeInTheDocument();
    expect(row.getByText("Laboratory")).toBeInTheDocument();
    expect(row.getByText(/Billed 1 Sept? 2026/)).toBeInTheDocument();
    expect(row.getByText("PARTIALLY PAID")).toBeInTheDocument();
    expect(row.getByText("Withdrawn by Laboratory")).toBeInTheDocument();
    expect(row.getByText("Charged").nextSibling).toHaveTextContent("₦4,000");
    expect(row.getByText("Paid").nextSibling).toHaveTextContent("₦2,500");
    expect(row.getByText("Refunded").nextSibling).toHaveTextContent("₦500");
    expect(row.getByText("Outstanding").nextSibling).toHaveTextContent("₦2,000");
  });

  it("offers only Cancel service when nothing has been paid", async () => {
    await open();
    expect(screen.getByRole("button", { name: /^cancel service$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /cancel & refund/i })).not.toBeInTheDocument();
  });

  it("offers only Cancel & refund, with the full amount, when it was paid for", async () => {
    rows = [PAID];
    await open();
    expect(screen.getByRole("button", { name: /cancel & refund ₦4,000/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^cancel service$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refund only/i })).not.toBeInTheDocument();
  });

  it("keeps a cancelled service on the page, struck through, with who and why", async () => {
    rows = [charge({ status: "cancelled", is_cancelled: true, outstanding: "0.00",
                     cancellation_reason: "Test never run", cancelled_by_name: "Ada Bello",
                     cancelled_at: "2026-09-02T10:00:00Z" })];
    await open();
    expect(screen.getByText("CANCELLED")).toBeInTheDocument();
    expect(screen.getByText("Laboratory: FBC")).toHaveClass("line-through");
    expect(screen.getByText(/Cancelled by Ada Bello on 2 Sept? 2026 — Test never run/)).toBeInTheDocument();
    // Scoped to the rows: the filter strip has a "Cancelled" tab of its own.
    expect(serviceList().queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
  });

  it("will not offer to refund money it cannot trace", async () => {
    rows = [charge({ ...PAID, untraceable_amount: "1500.00" })];
    await open();
    expect(serviceList().queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
    expect(screen.getByText(/cannot be traced to a payment/)).toBeInTheDocument();
  });

  it("offers nothing to a role the API would refuse", async () => {
    rows = [PAID, charge({ id: 12, description: "Adult Card", department_name: "Reception" })];
    await open({ id: 9, role: "reception" });
    expect(serviceList().getAllByRole("listitem")).toHaveLength(2);
    expect(serviceList().queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
  });

  it("stacks on a phone and lays out in columns on a desktop, with no table to drag sideways", async () => {
    await open();
    const item = serviceList().getAllByRole("listitem")[0];
    expect(item).toHaveClass("grid");
    expect(item.className).toMatch(/lg:grid-cols-/);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("Cancel service", () => {
  async function dialog() {
    await open();
    await userEvent.click(screen.getByRole("button", { name: /^cancel service$/i }));
    return within(screen.getByRole("dialog"));
  }

  it("shows what will happen before committing, and moves no money", async () => {
    const modal = await dialog();
    expect(modal.getByText("Obi, Ada")).toBeInTheDocument();
    expect(modal.getByText("NMHS-P000012")).toBeInTheDocument();
    expect(modal.getByText("Original charge").closest("div")).toHaveTextContent("₦4,000.00");
    expect(modal.getByText("Outstanding after cancelling").closest("div")).toHaveTextContent("₦0.00");
    expect(modal.queryByText("Refund now")).not.toBeInTheDocument();
    expect(modal.queryByLabelText(/how it is going back/i)).not.toBeInTheDocument();
  });

  it("will not submit without a reason and the confirmation", async () => {
    const modal = await dialog();
    const submit = modal.getByRole("button", { name: /^cancel service$/i });
    expect(submit).toBeDisabled();
    await userEvent.type(modal.getByLabelText(/why is this service being cancelled/i), "Not run");
    expect(submit).toBeDisabled();
    await userEvent.click(modal.getByRole("checkbox"));
    expect(submit).toBeEnabled();
  });

  it("calls the cancel endpoint and confirms it", async () => {
    const modal = await dialog();
    await userEvent.type(modal.getByLabelText(/why is this service/i), "Test never run");
    await userEvent.click(modal.getByRole("checkbox"));
    await userEvent.click(modal.getByRole("button", { name: /^cancel service$/i }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/charges/11/cancel/",
                                                              { reason: "Test never run" }));
    expect(await screen.findByText("Laboratory: FBC cancelled.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("Cancel & refund", () => {
  async function dialog(row = PAID) {
    rows = [row];
    const rendered = await open();
    await userEvent.click(screen.getByRole("button", { name: /cancel & refund ₦/i }));
    return { modal: within(screen.getByRole("dialog")), rendered };
  }

  async function confirm(modal, reason = "Test never run") {
    await userEvent.type(modal.getByLabelText(/why is this service/i), reason);
    await userEvent.click(modal.getByRole("checkbox"));
    await userEvent.click(modal.getByRole("button", { name: /^cancel & refund ₦/i }));
  }

  it("shows the full refund, read-only — there is no amount to type", async () => {
    const { modal } = await dialog();
    expect(modal.getByText("Refund now").closest("div")).toHaveTextContent("₦4,000.00");
    expect(modal.getByText("Already refunded").closest("div")).toHaveTextContent("₦0.00");
    expect(modal.getByText("Outstanding after cancelling").closest("div")).toHaveTextContent("₦0.00");
    expect(modal.queryByLabelText(/amount to refund/i)).not.toBeInTheDocument();
    expect(modal.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(modal.getByText(/Cancel Laboratory: FBC and refund ₦4,000\.00\?/)).toBeInTheDocument();
    expect(modal.getByRole("button", { name: "Cancel & refund ₦4,000.00" })).toBeDisabled();
  });

  it("sends the full refundable amount as a confirmation, to the combined endpoint", async () => {
    const { modal } = await dialog();
    await confirm(modal);
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe("/charges/11/cancel-and-refund/");
    expect(body).toEqual({ reason: "Test never run", amount: "4000.00" });
  });

  it("refunds what a part-paid service actually took, and nothing is left owing", async () => {
    const { modal } = await dialog(charge({ amount_paid: "2000.00", refundable_amount: "2000.00",
                                            outstanding: "2000.00", status: "partial" }));
    expect(modal.getByText("Refund now").closest("div")).toHaveTextContent("₦2,000.00");
    expect(modal.getByText("Outstanding now").closest("div")).toHaveTextContent("₦2,000.00");
    expect(modal.getByText("Outstanding after cancelling").closest("div")).toHaveTextContent("₦0.00");
    await confirm(modal);
    await waitFor(() => expect(api.post.mock.calls[0][1]).toMatchObject({ amount: "2000.00" }));
  });

  it("sends a refund method and reference only when chosen", async () => {
    const { modal } = await dialog();
    await userEvent.selectOptions(modal.getByLabelText(/how it is going back/i), "transfer");
    await userEvent.type(modal.getByLabelText(/reference/i), "TRF-1");
    await confirm(modal);
    await waitFor(() => expect(api.post.mock.calls[0][1])
      .toMatchObject({ method: "transfer", reference: "TRF-1" }));
  });

  it("confirms the outcome and refreshes everything the money touched", async () => {
    const { modal, rendered } = await dialog();
    const invalidate = vi.spyOn(rendered.client, "invalidateQueries");
    await confirm(modal);

    expect(await screen.findByText(/Laboratory: FBC cancelled and ₦4,000\.00 refunded\./))
      .toBeInTheDocument();
    expect(screen.getByText(/now owes ₦0\.00 on this service/)).toBeInTheDocument();
    const keys = invalidate.mock.calls.map((call) => call[0].queryKey[0]);
    for (const key of ["ledger", "ledgers", "charges", "payments", "adjustments", "refunds",
                       "dashboard", "finance-report", "unread-count", "notifications"]) {
      expect(keys).toContain(key);
    }
  });

  it("keeps the dialog open and says so when the amount changed under it", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 400, data: {
      code: "full_refund_required", refundable: "3000.00",
      detail: "This service is holding 3000.00 and Cancel & refund returns all of it." } } });
    const { modal } = await dialog();
    await confirm(modal);

    expect(await screen.findByText("The amount has changed")).toBeInTheDocument();
    expect(screen.getByText(/holding 3000\.00/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("names the permission on a 403", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 403, data: {} } });
    const { modal } = await dialog();
    await confirm(modal);
    expect(await screen.findByText(/not authorised to cancel a service and refund its payment/i))
      .toBeInTheDocument();
    expect(screen.queryByText(/refunded\./)).not.toBeInTheDocument();
  });
});
