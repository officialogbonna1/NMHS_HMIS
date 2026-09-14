import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppShell from "./AppShell.jsx";
import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";

// Two desks in the side navigation — Refunds and Service Cancellations — kept
// apart because they are two different decisions. Each must be there for the
// roles the backend lets through, absent for everyone else, and reachable from
// the desktop rail and the phone drawer alike.
beforeEach(() => {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/notifications/unread-count/") return Promise.resolve({ data: { unread: 0 } });
    if (url === "/hospital-settings/current/") {
      return Promise.resolve({ data: { name: "NMHS", full_name: "Ngozi Maternity", address: "Aba" } });
    }
    return Promise.resolve({ data: [] });
  });
});
afterEach(() => vi.restoreAllMocks());

const shellFor = (role) => renderWithApp(<AppShell />, { user: { id: 1, username: "u", role } });
const rail = () => document.querySelector("aside");

const DESKS = [
  { name: /^Refunds$/, href: "/refunds" },
  { name: /^Service Cancellations$/, href: "/service-cancellations" },
];

describe("the two desks", () => {
  it("are both offered to the roles that may use them", () => {
    for (const role of ["cashier", "accountant", "admin", "hospital_admin"]) {
      const { unmount } = shellFor(role);
      for (const desk of DESKS) {
        const link = within(rail()).getByRole("link", { name: desk.name });
        expect(link).toHaveAttribute("href", desk.href);
      }
      unmount();
    }
  });

  it("are hidden from roles the API would refuse — including reception", () => {
    // Reception is in BILLING_ROLES and sees Billing, but neither reversing
    // money nor withdrawing a bill is a front-desk decision.
    for (const role of ["reception", "pharmacist", "doctor", "nurse", "laboratory",
                        "ward_manager", "inventory_manager"]) {
      const { unmount } = shellFor(role);
      for (const desk of DESKS) {
        expect(screen.queryByRole("link", { name: desk.name })).not.toBeInTheDocument();
      }
      unmount();
    }
  });

  it("are two separate items, not one combined entry", () => {
    shellFor("cashier");
    expect(screen.queryByRole("link", { name: /Refunds & Cancellations/i })).not.toBeInTheDocument();
    const refunds = within(rail()).getByRole("link", { name: /^Refunds$/ });
    const cancellations = within(rail()).getByRole("link", { name: /^Service Cancellations$/ });
    expect(refunds).not.toBe(cancellations);
    // …with different pictures, so the two read as different things at a glance.
    expect(refunds.querySelector("svg").innerHTML)
      .not.toEqual(cancellations.querySelector("svg").innerHTML);
  });

  it("sit together in the Finance group", () => {
    shellFor("cashier");
    const finance = within(rail()).getByText("Finance").closest("div");
    for (const desk of DESKS) {
      expect(within(finance).getByRole("link", { name: desk.name })).toBeInTheDocument();
    }
  });

  it("carry icons from the shared SVG set, not emoji", () => {
    shellFor("cashier");
    for (const desk of DESKS) {
      const link = within(rail()).getByRole("link", { name: desk.name });
      expect(link.querySelector("svg")).toBeTruthy();
      expect(link.textContent).toMatch(/^[\x20-\x7E]*$/);
    }
  });
});

describe("on a phone", () => {
  it("reaches both desks through the drawer", async () => {
    shellFor("accountant");
    await userEvent.click(screen.getByRole("button", { name: /open menu/i }));
    const drawer = within(await screen.findByRole("dialog", { name: /main navigation/i }));
    for (const desk of DESKS) {
      expect(drawer.getByRole("link", { name: desk.name })).toHaveAttribute("href", desk.href);
    }
  });

  it("does not offer them in the drawer to a role that cannot use them", async () => {
    shellFor("reception");
    await userEvent.click(screen.getByRole("button", { name: /open menu/i }));
    const drawer = within(await screen.findByRole("dialog", { name: /main navigation/i }));
    for (const desk of DESKS) {
      expect(drawer.queryByRole("link", { name: desk.name })).not.toBeInTheDocument();
    }
  });
});
