/**
 * The lockout as the login page reads it — the decisions, without a DOM.
 *
 * None of this enforces anything: the server refuses a locked client before it
 * reads the password. These tests are about whether the page tells the truth
 * about what the server said.
 */
import { describe, it, expect } from "vitest";
import { formatRemaining, lockoutFrom, lockoutMessage } from "./loginLockout.js";

const locked = (retry_after, detail = "blocked") => ({
  response: { status: 429, data: { code: "login_locked", retry_after, detail } },
});

describe("reading a lockout off the server's refusal", () => {
  it("recognises the 429 the login endpoint sends", () => {
    expect(lockoutFrom(locked(300, "For your security…"))).toEqual({
      seconds: 300,
      detail: "For your security…",
    });
  });

  it("ignores an ordinary wrong-password refusal", () => {
    const wrong = { response: { status: 400, data: { non_field_errors: ["nope"] } } };
    expect(lockoutFrom(wrong)).toBeNull();
  });

  it("ignores a 429 that is not this lockout", () => {
    expect(lockoutFrom({ response: { status: 429, data: {} } })).toBeNull();
  });

  it("survives a network error with no response at all", () => {
    expect(lockoutFrom(new Error("Network Error"))).toBeNull();
    expect(lockoutFrom(undefined)).toBeNull();
  });

  it("treats a missing or spent figure as nothing left to wait", () => {
    expect(lockoutFrom(locked(undefined)).seconds).toBe(0);
    expect(lockoutFrom(locked(-5)).seconds).toBe(0);
  });
});

describe("saying how long is left", () => {
  it("speaks minutes and seconds the way somebody waiting would", () => {
    expect(formatRemaining(272)).toBe("4 minutes 32 seconds");
    expect(lockoutMessage(272)).toBe(
      "Too many failed login attempts. Please try again in 4 minutes 32 seconds.",
    );
  });

  it("gets its singulars right", () => {
    expect(formatRemaining(61)).toBe("1 minute 1 second");
    expect(formatRemaining(120)).toBe("2 minutes");
  });

  it("drops the minutes once there are none", () => {
    expect(formatRemaining(45)).toBe("45 seconds");
    expect(formatRemaining(0)).toBe("0 seconds");
  });

  it("never counts below zero", () => {
    expect(formatRemaining(-30)).toBe("0 seconds");
  });
});
