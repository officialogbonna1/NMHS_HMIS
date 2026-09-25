import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import AppShell from "./AppShell.jsx";

// Who sees Maternity in the side navigation. `NAV_ITEMS`' roles are the
// source of truth (MATERNITY_DESK_ROLES), and `hasRole` admits every admin
// role on top — so the check is that the row follows that group and is not
// quietly widened for layout reasons.

beforeEach(() => {
  vi.spyOn(api, "get").mockResolvedValue({ data: { unread: 0 } });
});
afterEach(() => vi.restoreAllMocks());

const maternityLinks = () => screen.queryAllByRole("link", { name: /^Maternity$/ });

describe("the Maternity navigation row", () => {
  it.each([
    ["nurse", "a midwife works maternity"],
    ["doctor", "so does the maternity doctor"],
    ["reception", "the front desk looks a returning mother up"],
    ["admin", "administrators reach every page"],
    ["hospital_admin", "including the ordinary administrator"],
  ])("is shown to %s — %s", (role) => {
    renderWithApp(<AppShell />, { user: { id: 1, role } });
    expect(maternityLinks().length).toBeGreaterThan(0);
  });

  it.each(["pharmacist", "laboratory", "radiology", "cashier", "accountant",
           "inventory_manager", "ward_manager", "optometrist", "ophthalmologist"])(
    "is hidden from %s", (role) => {
      renderWithApp(<AppShell />, { user: { id: 2, role } });
      expect(maternityLinks()).toHaveLength(0);
    });

  it("points at /maternity and draws its own icon", () => {
    renderWithApp(<AppShell />, { user: { id: 3, role: "nurse" } });
    const [link] = maternityLinks();
    expect(link).toHaveAttribute("href", "/maternity");
    // An icon a row does not have renders blank and silently (rule 49).
    expect(link.querySelector("svg")).toBeTruthy();
  });
});
