import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";

// Each desk follows its own role group. They hold the same people today, so
// pull them apart and check the navigation reads each one independently.
vi.mock("../auth/roles.js", async (importOriginal) => ({
  ...(await importOriginal()),
  CANCEL_ROLES: ["cashier"],
  REFUND_ROLES: ["accountant"],
}));

const { default: AppShell } = await import("./AppShell.jsx");

beforeEach(() => {
  vi.spyOn(api, "get").mockResolvedValue({ data: { unread: 0 } });
});
afterEach(() => vi.restoreAllMocks());

describe("the two desks follow two groups", () => {
  it("shows Service Cancellations, not Refunds, to a role that may only cancel", () => {
    renderWithApp(<AppShell />, { user: { id: 1, role: "cashier" } });
    expect(screen.getAllByRole("link", { name: /^Service Cancellations$/ }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: /^Refunds$/ })).not.toBeInTheDocument();
  });

  it("shows Refunds, not Service Cancellations, to a role that may only refund", () => {
    renderWithApp(<AppShell />, { user: { id: 2, role: "accountant" } });
    expect(screen.getAllByRole("link", { name: /^Refunds$/ }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: /^Service Cancellations$/ })).not.toBeInTheDocument();
  });
});
