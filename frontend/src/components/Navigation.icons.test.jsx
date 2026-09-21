import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import AppShell from "./AppShell.jsx";
import { Icon } from "./icons.jsx";

// Every navigation row must actually draw an icon, and no two rows in one
// group may draw the same one.
//
// `Icon` returns **null** for a name the set does not hold — silently, with no
// error and no placeholder — so a typo in `NAV_ITEMS` leaves a row sitting in
// the sidebar with a blank where its icon should be. That is exactly what
// "clipboard" did on Discharged patients, and what `bed` did to Discharge
// patient in the other direction: it drew the *same* icon as Admissions, so
// two destinations looked like one.

beforeEach(() => {
  vi.spyOn(api, "get").mockResolvedValue({ data: { unread: 0 } });
});
afterEach(() => vi.restoreAllMocks());

/** The `<svg>` a nav link actually rendered, or null if it drew nothing. */
function iconOf(name) {
  const link = screen.getAllByRole("link", { name: new RegExp(`^${name}$`) })[0];
  return link?.querySelector("svg");
}

describe("every navigation row draws an icon", () => {
  it("draws one for both discharge destinations", () => {
    renderWithApp(<AppShell />, { user: { id: 1, role: "admin" } });
    expect(iconOf("Discharge Patient")).toBeTruthy();
    expect(iconOf("Discharged Patients")).toBeTruthy();
  });

  it("gives them different icons, and neither is the Admissions bed", () => {
    renderWithApp(<AppShell />, { user: { id: 1, role: "admin" } });
    const discharge = iconOf("Discharge Patient").innerHTML;
    const discharged = iconOf("Discharged Patients").innerHTML;
    const admissions = iconOf("Admissions").innerHTML;

    expect(discharge).not.toBe(discharged);
    expect(discharge).not.toBe(admissions);
    expect(discharged).not.toBe(admissions);
  });

  it("draws an icon on every row an admin can see", () => {
    // The blanket version of the two above: a role that sees most of the
    // application sees no row with a missing icon.
    const { container } = renderWithApp(<AppShell />, { user: { id: 1, role: "admin" } });
    // Navigation rows only — the skip link and the masthead brand are not
    // nav items and carry no icon by design.
    const rows = [...container.querySelectorAll("nav a")];
    expect(rows.length).toBeGreaterThan(10);
    const blank = rows
      .filter((link) => link.textContent.trim() && !link.querySelector("svg"))
      .map((link) => link.textContent.trim());
    expect(blank).toEqual([]);
  });
});

describe("who sees the discharge workspace", () => {
  const visible = (name) =>
    screen.queryAllByRole("link", { name: new RegExp(`^${name}$`) }).length > 0;

  it("shows both rows to the ordinary hospital administrator", () => {
    // `IsAdmin` on `/admin-discharges/`, mirrored by `ADMIN_ROLES` on the
    // route guard. It was Super Admin only; running the wards is ordinary
    // hospital administration, so both administrators work this door.
    renderWithApp(<AppShell />, { user: { id: 2, role: "hospital_admin" } });
    expect(visible("Discharge Patient")).toBe(true);
    expect(visible("Discharged Patients")).toBe(true);
  });

  it("shows them to the Super Admin as before", () => {
    renderWithApp(<AppShell />, { user: { id: 1, role: "admin" } });
    expect(visible("Discharge Patient")).toBe(true);
    expect(visible("Discharged Patients")).toBe(true);
  });

  it("shows neither to a clinical role, and leaves the ward's own board alone", () => {
    renderWithApp(<AppShell />, { user: { id: 3, role: "nurse" } });
    expect(visible("Discharge Patient")).toBe(false);
    expect(visible("Discharged Patients")).toBe(false);
    // The ward discharges from Admissions exactly as it always has.
    expect(visible("Admissions")).toBe(true);
  });
});

describe("the icon set itself", () => {
  it("draws the two the discharge workspace names", () => {
    const { container } = renderWithApp(
      <><Icon name="discharge" /><Icon name="clipboard" /></>, { user: null });
    expect(container.querySelectorAll("svg")).toHaveLength(2);
  });

  it("renders nothing at all for a name it does not hold", () => {
    // Documenting the trap rather than changing it: a missing icon is silent,
    // which is why the test above exists.
    const { container } = renderWithApp(<Icon name="not-an-icon" />, { user: null });
    expect(container.querySelector("svg")).toBeNull();
  });
});
