/**
 * Reading a failure, so a screen says something useful.
 *
 * The backend answers every refusal in one shape now
 * (`apps/core/exceptions.py`): `detail` and `code` always, per-field errors
 * beside them, `reference` on the rare 500. These are the decisions the
 * frontend makes from that, lifted out of the DOM — the pure-module pattern
 * `refundPolicy.js` set.
 */
import { describe, it, expect } from "vitest";
import { errorCode, errorMessage, errorReference, fieldErrors, readError } from "./errors.js";

const failure = (status, data) => ({ response: { status, data } });

describe("what the person is told", () => {
  it("uses the server's own sentence", () => {
    expect(errorMessage(failure(403, { detail: "You do not have permission to do this.", code: "permission_denied" })))
      .toBe("You do not have permission to do this.");
  });

  it("surfaces a field error rather than a blank form", () => {
    const message = errorMessage(failure(400, { diagnosis: ["This field is required."], code: "invalid" }));
    expect(message).toContain("This field is required.");
  });

  it("says the request never arrived when there is no response", () => {
    expect(errorMessage(new Error("Network Error")))
      .toBe("Could not reach the hospital system. Check your connection and try again.");
  });

  it("never shows an empty message", () => {
    expect(errorMessage(failure(500, {})).length).toBeGreaterThan(0);
    expect(errorMessage(undefined).length).toBeGreaterThan(0);
  });

  it("leaves readError's existing behaviour alone", () => {
    // The helper the forms already call, unchanged.
    expect(readError(failure(400, { detail: "Nope." }))).toBe("Nope.");
  });
});

describe("what the code branches on", () => {
  it("reads the machine-readable code", () => {
    expect(errorCode(failure(409, { code: "already_discharged" }))).toBe("already_discharged");
    expect(errorCode(failure(400, {}))).toBe("");
    expect(errorCode(new Error("boom"))).toBe("");
  });

  it("reads the support reference a 500 carries", () => {
    expect(errorReference(failure(500, { code: "server_error", reference: "RF-3f2a9c" })))
      .toBe("RF-3f2a9c");
    expect(errorReference(failure(400, { code: "invalid" }))).toBe("");
  });

  it("splits per-field errors out for a form to mark up", () => {
    const fields = fieldErrors(failure(400, {
      detail: "That request could not be completed.",
      code: "invalid",
      diagnosis: ["This field is required."],
      summary: "Too short.",
    }));
    expect(fields).toEqual({ diagnosis: "This field is required.", summary: "Too short." });
    // `detail`, `code` and `reference` are the envelope, not fields.
    expect(fields.detail).toBeUndefined();
    expect(fields.code).toBeUndefined();
  });
});
