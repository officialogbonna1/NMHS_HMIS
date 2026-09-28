/**
 * A prescription is billed when the doctor writes it, and the pharmacy
 * dispenses a line once **its own** bill is paid, written off or set to Pay
 * later — never on the patient's balance.
 *
 * The screen reads `dispensable` and `billing` off the prescription row; the
 * dispense service is what actually refuses (`code: "payment_required"`).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import Pharmacy from "./Pharmacy.jsx";
import { ChargeRow } from "./Billing.jsx";
import PayChargePanel from "../components/PayChargePanel.jsx";

const PHARMACIST = { id: 5, username: "ph", role: "pharmacist" };

const billing = (overrides) => ({
  billed: true, charge: 41, status: "unpaid", label: "UNPAID", requires_payment: true,
  amount: "200.00", paid: "0.00", outstanding: "200.00", deferred: false, ...overrides,
});

const line = (overrides) => ({
  id: 7, patient: 3, patient_name: "John, Michael", patient_number: "NMHS-P000014",
  item_name: "Paracetamol 500mg", item_strength: "500 mg", item_form: "", item_unit: "tab",
  quantity: 10, status: "pending", doctor_name: "Tunde Okafor",
  created_at: "2026-09-28T09:00:00Z", billing: billing({}), dispensable: false,
  ...overrides,
});

let queue;

beforeEach(() => {
  queue = [line({})];
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/prescriptions/") return Promise.resolve({ data: queue });
    if (url === "/stock-locations/") return Promise.resolve({ data: [] });
    return Promise.resolve({ data: [] });
  });
});
afterEach(() => vi.restoreAllMocks());

const card = async () => (await screen.findByText(/Paracetamol 500mg/)).closest(".rounded-lg");

describe("the dispensing queue reads each line's own bill", () => {
  it("shows the patient, hospital number, drug, quantity, prescriber and the bill", async () => {
    renderWithApp(<Pharmacy />, { user: PHARMACIST });
    const row = await card();
    expect(within(row).getByText("John, Michael")).toBeInTheDocument();
    expect(within(row).getByText(/NMHS-P000014/)).toBeInTheDocument();
    expect(within(row).getByText(/×10 tab/)).toBeInTheDocument();
    expect(within(row).getByText(/Tunde Okafor/)).toBeInTheDocument();
    expect(within(row).getByText(/UNPAID/)).toBeInTheDocument();
  });

  it("will not dispense an unpaid line and says why", async () => {
    const post = vi.spyOn(api, "post");
    const user = userEvent.setup();
    renderWithApp(<Pharmacy />, { user: PHARMACIST });
    const row = await card();
    const dispense = within(row).getByRole("button", { name: "Dispense" });
    expect(dispense).toBeDisabled();
    expect(within(row).getByText(/once this bill is paid, waived or set to Pay later/))
      .toBeInTheDocument();
    await user.click(dispense);
    expect(post).not.toHaveBeenCalled();
  });

  it("takes payment for that line alone, naming its charge", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { amount: "200.00" } });
    const user = userEvent.setup();
    renderWithApp(<Pharmacy />, { user: PHARMACIST });
    const row = await card();
    await user.click(within(row).getByRole("button", { name: "Take payment" }));
    await user.click(screen.getByRole("button", { name: /Take ₦200/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/payments/", {
      patient: 3, charge: 41, amount: "200", method: "cash" }));
  });

  it("dispenses a line whose bill is settled", async () => {
    queue = [line({ billing: billing({ status: "paid", label: "PAID", requires_payment: false,
                                       paid: "200.00", outstanding: "0.00" }),
                    dispensable: true })];
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: line({ status: "dispensed" }) });
    const user = userEvent.setup();
    renderWithApp(<Pharmacy />, { user: PHARMACIST });
    const row = await card();
    expect(within(row).getByText(/PAID/)).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Take payment" })).not.toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Dispense" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/prescriptions/7/dispense/"));
  });

  it("lets Pay later through, because the server says it is dispensable", async () => {
    queue = [line({ billing: billing({ status: "deferred", label: "PAY LATER",
                                       requires_payment: false, deferred: true }),
                    dispensable: true })];
    renderWithApp(<Pharmacy />, { user: PHARMACIST });
    const row = await card();
    expect(within(row).getByText(/PAY LATER/)).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Dispense" })).toBeEnabled();
  });

  it("offers to raise the bill for a script written before prescriptions were billed", async () => {
    queue = [line({ billing: { billed: false, status: "unbilled", priced: false,
                               requires_payment: false, amount: "0.00", outstanding: "0.00" },
                    dispensable: false })];
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    renderWithApp(<Pharmacy />, { user: PHARMACIST });
    const row = await card();
    expect(within(row).getByText("NOT BILLED")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Dispense" })).toBeDisabled();
    await user.click(within(row).getByRole("button", { name: "Raise bill" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/prescriptions/7/bill/"));
  });
});

describe("the Billing counter names a prescription's bill", () => {
  const charge = {
    id: 41, patient: 3, description: "Medication: Paracetamol 500mg ×10", amount: "200.00",
    balance: "200.00", outstanding: "200.00", amount_paid: "0.00", amount_discounted: "0.00",
    amount_waived: "0.00", payable: "200.00", status: "unpaid", settlement_status: "unpaid",
    created_at: "2026-09-28T09:00:00Z", deferral: null,
    prescription: { id: 7, drug: "Paracetamol 500mg", strength: "500 mg", quantity: 10,
                    unit: "tab", prescriber: "Tunde Okafor", status: "pending",
                    status_label: "Awaiting dispensing" },
  };

  it("shows the drug, quantity and prescriber, and pays this bill alone", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { amount: "200.00" } });
    const user = userEvent.setup();
    renderWithApp(<ChargeRow charge={charge} canWaive onDone={() => {}} />,
                  { user: { role: "cashier" } });
    expect(screen.getByText(/Pharmacy prescription:/)).toBeInTheDocument();
    expect(screen.getByText(/prescribed by Tunde Okafor/)).toBeInTheDocument();
    expect(screen.getByText(/×10 tab/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Pay this prescription" }));
    expect(screen.getByText(/Settles this bill only/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Take ₦200/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/payments/", {
      patient: 3, charge: 41, amount: "200", method: "cash" }));
  });

  it("offers no prescription payment on an ordinary bill", () => {
    renderWithApp(<ChargeRow charge={{ ...charge, prescription: null,
                                       description: "Consultation" }}
                             canWaive onDone={() => {}} />, { user: { role: "cashier" } });
    expect(screen.queryByRole("button", { name: "Pay this prescription" })).not.toBeInTheDocument();
  });
});

describe("PayChargePanel", () => {
  it("shows the server's refusal and stays open", async () => {
    // Settled at another desk since this screen loaded: only the server knows.
    vi.spyOn(api, "post").mockRejectedValue({
      response: { status: 400, data: { detail: "Medication: X is not owing anything." } },
    });
    const user = userEvent.setup();
    renderWithApp(<PayChargePanel charge={41} patient={3} outstanding={200} description="X" />,
                  { user: { role: "cashier" } });
    await user.click(screen.getByRole("button", { name: /Take ₦200/ }));
    expect(await screen.findByText(/is not owing anything/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Take ₦200/ })).toBeInTheDocument();
  });
});
