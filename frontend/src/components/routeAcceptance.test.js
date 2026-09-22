import { describe, expect, it } from "vitest";

import { acceptanceOf } from "./routeAcceptance.js";

// The one reading of "has this referral been accepted" — the decision behind
// the Accept button and the "Accepted" status that replaces it. It invents no
// state: everything here comes off the route's own `status`, `assigned_to`
// and the server's `can_accept`.

const NURSE = { id: 7, role: "nurse" };

const route = (overrides) => ({
  id: 1, purpose: "vitals", status: "queued", assigned_to: null,
  assigned_to_name: null, ...overrides,
});

describe("before anybody has taken the work", () => {
  it("offers Accept on an unclaimed queued route", () => {
    const { accepted, canAccept } = acceptanceOf(route({}), NURSE);
    expect(accepted).toBe(false);
    expect(canAccept).toBe(true);
  });

  it("follows the server's own answer where the payload carries one", () => {
    expect(acceptanceOf(route({ can_accept: false }), NURSE).canAccept).toBe(false);
  });

  it("does not call a route accepted merely because somebody was named on it", () => {
    // Reception may name a nurse on a hand-off: that is an assignment, and
    // the work has not been picked up until `accept` moves the status.
    const named = acceptanceOf(route({ assigned_to: 7, assigned_to_name: "Ada Obi" }), NURSE);
    expect(named.accepted).toBe(false);
    expect(named.mine).toBe(true);
  });
});

describe("once it has been accepted", () => {
  const taken = route({ status: "in_progress", assigned_to: 7, assigned_to_name: "Ada Obi" });

  it("reads as accepted, and the Accept action is spent", () => {
    const { accepted, canAccept, mine, by } = acceptanceOf(taken, NURSE);
    expect(accepted).toBe(true);
    expect(canAccept).toBe(false);
    expect(mine).toBe(true);
    expect(by).toBe("Ada Obi");
  });

  it("reads as accepted for a colleague too, and names who has it", () => {
    const theirs = acceptanceOf(taken, { id: 12, role: "nurse" });
    expect(theirs.accepted).toBe(true);
    expect(theirs.mine).toBe(false);
    expect(theirs.by).toBe("Ada Obi");
    expect(theirs.canAccept).toBe(false);
  });

  it("never offers Accept again, whatever a stale payload claims", () => {
    expect(acceptanceOf({ ...taken, can_accept: true }, NURSE).canAccept).toBe(false);
  });

  it("stays accepted when the holder's account has gone", () => {
    // `assigned_to` is SET_NULL. The status is what records the acceptance,
    // so a route whose holder was deactivated does not fall back into the
    // unclaimed queue.
    const orphaned = acceptanceOf({ ...taken, assigned_to: null, assigned_to_name: null }, NURSE);
    expect(orphaned.accepted).toBe(true);
    expect(orphaned.canAccept).toBe(false);
  });
});

describe("a route nobody can take", () => {
  it("offers nothing on a completed one", () => {
    const done = acceptanceOf(route({ status: "completed", assigned_to: 7 }), NURSE);
    expect(done.canAccept).toBe(false);
    // Nor a badge for a step the Completed tab already puts behind you.
    expect(done.accepted).toBe(false);
  });

  it("offers nothing on a cancelled one", () => {
    expect(acceptanceOf(route({ status: "cancelled" }), NURSE).canAccept).toBe(false);
  });

  it("offers nothing to a colleague on a queued route already named to somebody", () => {
    const theirs = acceptanceOf(route({ assigned_to: 12, assigned_to_name: "Tunde" }), NURSE);
    expect(theirs.mine).toBe(false);
    expect(theirs.canAccept).toBe(false);
  });
});
