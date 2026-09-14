import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppShell, { navItemActive } from "./AppShell.jsx";
import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";

// The Pharmacy group: every pharmacy job one click away, each link carrying the
// roles of the route it opens, so nobody is offered a page that would bounce
// them. Several items open tabs of one page, which is what `navItemActive` is for.
beforeEach(() => {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/notifications/unread-count/") return Promise.resolve({ data: { unread: 0 } });
    return Promise.resolve({ data: [] });
  });
});
afterEach(() => vi.restoreAllMocks());

const shellFor = (role) => renderWithApp(<AppShell />, { user: { id: 1, username: "u", role } });
const rail = () => document.querySelector("aside");
const link = (name) => within(rail()).queryByRole("link", { name });

const PHARMACY_LINKS = [
  [/^Pharmacy Dashboard$/, "/pharmacy?tab=overview"],
  [/^Prescriptions$/, "/pharmacy"],
  [/^Dispensing$/, "/pharmacy?tab=dispensed"],
  [/^Pharmacy POS$/, "/pharmacy/pos"],
  [/^Sales$/, "/pharmacy/sales"],
  [/^Returns & Refunds$/, "/pharmacy/sales?tab=returns"],
  [/^Products$/, "/pharmacy?tab=products"],
  [/^Stock$/, "/pharmacy?tab=stock"],
  [/^Stock Operations$/, "/pharmacy?tab=transfer"],
  [/^Stock Count$/, "/pharmacy?tab=count"],
  [/^Inventory Import\/Export$/, "/pharmacy?tab=import"],
];

describe("the Pharmacy navigation", () => {
  it("gives a pharmacist every pharmacy job, each at its own address", () => {
    shellFor("pharmacist");
    for (const [name, href] of PHARMACY_LINKS) {
      expect(link(name)).toHaveAttribute("href", href);
    }
  });

  it("gives a cashier the till, its sales and returns — and no dispensing or stock", () => {
    shellFor("cashier");
    for (const name of [/^Pharmacy POS$/, /^Sales$/, /^Returns & Refunds$/]) expect(link(name)).toBeInTheDocument();
    for (const name of [/^Prescriptions$/, /^Dispensing$/, /^Stock Count$/, /^Inventory Import\/Export$/]) {
      expect(link(name)).not.toBeInTheDocument();
    }
  });

  it("lets the accountant read sales and returns without operating the till", () => {
    shellFor("accountant");
    expect(link(/^Sales$/)).toBeInTheDocument();
    expect(link(/^Returns & Refunds$/)).toBeInTheDocument();
    expect(link(/^Pharmacy POS$/)).not.toBeInTheDocument();
  });

  it("shows no pharmacy work to roles that have none", () => {
    for (const role of ["doctor", "nurse", "reception", "laboratory"]) {
      const { unmount } = shellFor(role);
      expect(screen.queryByText("Pharmacy")).not.toBeInTheDocument();
      expect(link(/^Pharmacy POS$/)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("gives the inventory manager the count import beside the stock desk", () => {
    shellFor("inventory_manager");
    expect(link(/^Stock Import \/ Export$/)).toHaveAttribute("href", "/inventory?tab=import");
    expect(link(/^Stock Count$/)).not.toBeInTheDocument();
  });
});

describe("navItemActive", () => {
  const paths = PHARMACY_LINKS.map(([, href]) => href);
  const at = (url) => {
    const [pathname, search = ""] = url.split("?");
    return { pathname, search: search ? `?${search}` : "" };
  };

  it("lights a tabbed item only on its own tab", () => {
    expect(navItemActive("/pharmacy?tab=stock", at("/pharmacy?tab=stock"), paths)).toBe(true);
    expect(navItemActive("/pharmacy?tab=stock", at("/pharmacy?tab=count"), paths)).toBe(false);
    expect(navItemActive("/pharmacy?tab=stock", at("/pharmacy"), paths)).toBe(false);
  });

  it("lights the plain page item only while none of its tabbed siblings is showing", () => {
    expect(navItemActive("/pharmacy", at("/pharmacy"), paths)).toBe(true);
    expect(navItemActive("/pharmacy", at("/pharmacy?tab=payments"), paths)).toBe(true);
    expect(navItemActive("/pharmacy", at("/pharmacy?tab=stock"), paths)).toBe(false);
    expect(navItemActive("/pharmacy", at("/pharmacy/pos"), paths)).toBe(false);
    expect(navItemActive("/pharmacy/sales", at("/pharmacy/sales?tab=registers"), paths)).toBe(true);
    expect(navItemActive("/pharmacy/sales", at("/pharmacy/sales?tab=returns"), paths)).toBe(false);
  });

  it("keeps the old behaviour for pages with no tabs", () => {
    expect(navItemActive("/patients", at("/patients/abc"), ["/patients"])).toBe(true);
    expect(navItemActive("/", at("/patients"), ["/"])).toBe(false);
  });
});
