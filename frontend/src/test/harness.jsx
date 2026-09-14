import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../components/Toaster.jsx";
import { AuthContext } from "../auth/AuthContext.jsx";

/**
 * Render a component inside the providers the application gives it: a router
 * (Button renders a Link when given `to`), TanStack Query, the toaster, and an
 * auth context holding whichever role the test is about.
 *
 * `retry: false` so a mutation that is meant to fail fails once and the
 * assertion is not waiting on three backoffs.
 */
export function renderWithApp(ui, { user = null, queryClient } = {}) {
  const client = queryClient ?? new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const value = { user, login: () => {}, logout: () => {}, loading: false };
  return {
    client,
    ...render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <AuthContext.Provider value={value}>
            <ToastProvider>{ui}</ToastProvider>
          </AuthContext.Provider>
        </QueryClientProvider>
      </MemoryRouter>,
    ),
  };
}

/** A payment row shaped the way `PaymentSerializer` sends one. */
export function makePayment(overrides = {}) {
  const amount = overrides.amount ?? "10000.00";
  const refunded = overrides.amount_refunded ?? "0.00";
  return {
    id: 7,
    patient: 3,
    patient_name: "Obi, Ada",
    patient_number: "NMHS-P000012",
    amount,
    method: "cash",
    channel: "cashier",
    reference: "",
    received_by_name: "Ada Bello",
    created_at: "2026-09-01T10:30:00Z",
    amount_refunded: refunded,
    refundable_balance: (Number(amount) - Number(refunded)).toFixed(2),
    is_fully_refunded: Number(amount) - Number(refunded) <= 0,
    ...overrides,
  };
}

export const PATIENT = {
  id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000",
  first_name: "Ada", last_name: "Obi", patient_number: "NMHS-P000012",
};
