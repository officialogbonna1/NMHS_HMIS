import { describe, expect, it } from "vitest";

import {
  ACTIVE_PREGNANCY, NEW_PATIENT, NO_ACTIVE_PREGNANCY, STATE_TITLE, actionsFor,
  activePregnancy, deskState, encounterBody, gravidaPara, lastSeen, pregnancySummary,
  previousPregnancies, visitTypes,
} from "./maternityPatient";

// The returning-patient decision, without the DOM. Every answer comes from
// the server's lookup; nothing here works out whether she is pregnant.

const CURRENT = {
  id: 7, number: 2, reference: "PRG-000007", is_active: true,
  gestation: "32w 4d", edd: "2026-03-12", gravida: 2, para: 1, encounter_count: 3,
  last_encounter: { id: 30, type: "ANC — follow-up", seen_on: "2026-09-01",
                    status: "completed", gestation: "29w 0d" },
};
const PREVIOUS = {
  id: 3, number: 1, reference: "PRG-000003", is_active: false,
  gestation: "", edd: "2024-02-02", outcome: "delivered", ended_on: "2024-02-05",
  encounter_count: 5, last_encounter: null,
};

const lookup = (overrides) => ({
  known: true, patient: { id: 1, name: "Jane, Mary", patient_number: "NMHS-P000123" },
  active_pregnancy: CURRENT, history: [CURRENT, PREVIOUS],
  can_continue: true, can_start: false, has_history: true,
  visit_types: [{ id: 2, code: "anc-followup", name: "ANC — follow-up" },
                { id: 4, code: "labour-assessment", name: "Labour assessment" }],
  ...overrides,
});

describe("which of the three states the desk is in", () => {
  it("is an existing maternity patient when a pregnancy is active", () => {
    expect(deskState(lookup())).toBe(ACTIVE_PREGNANCY);
    expect(STATE_TITLE[ACTIVE_PREGNANCY]).toBe("Existing maternity patient");
  });

  it("is an existing patient with no active pregnancy", () => {
    const state = deskState(lookup({ active_pregnancy: null, can_continue: false,
                                     can_start: true }));
    expect(state).toBe(NO_ACTIVE_PREGNANCY);
    expect(STATE_TITLE[state]).toBe("Existing patient — no active pregnancy");
  });

  it("is a new patient only when the server says she is not known", () => {
    expect(deskState(lookup({ known: false }))).toBe(NEW_PATIENT);
  });

  it("is nothing at all before the lookup has answered", () => {
    // A payload that has not arrived is "nothing chosen", never "new
    // patient" — offering to register her while her record loads is the
    // mistake this screen exists to prevent.
    expect(deskState(null)).toBeNull();
    expect(deskState(undefined)).toBeNull();
  });
});

describe("what the desk may do", () => {
  it("offers Continue and never Start while she is pregnant", () => {
    const actions = actionsFor(lookup());
    expect(actions.continuePregnancy).toBe(true);
    expect(actions.startPregnancy).toBe(false);
    expect(actions.register).toBe(false);
  });

  it("offers Start and never Continue when she is not", () => {
    const actions = actionsFor(lookup({ active_pregnancy: null, can_continue: false,
                                        can_start: true }));
    expect(actions.startPregnancy).toBe(true);
    expect(actions.continuePregnancy).toBe(false);
  });

  it("offers registration only for somebody the hospital does not know", () => {
    expect(actionsFor(lookup({ known: false })).register).toBe(true);
  });

  it("mirrors the server rather than deciding for itself", () => {
    // The server is what refuses a second active pregnancy; if it ever said
    // both, the screen would say both rather than inventing a rule.
    const actions = actionsFor({ can_continue: true, can_start: true, known: true });
    expect(actions.continuePregnancy && actions.startPregnancy).toBe(true);
  });

  it("offers nothing on an empty payload", () => {
    expect(actionsFor(null)).toEqual({ register: true, continuePregnancy: false,
                                       startPregnancy: false });
  });
});

describe("the pregnancy summary the desk reads out", () => {
  it("names the pregnancy, the gestation and the EDD", () => {
    const summary = pregnancySummary(CURRENT);
    expect(summary).toContain("Pregnancy #2");
    expect(summary).toContain("32w 4d");
    expect(summary).toContain("EDD");
  });

  it("drops what the record does not hold rather than inventing it", () => {
    expect(pregnancySummary({ number: 1, gestation: "", edd: null }))
      .toBe("Pregnancy #1");
  });

  it("says nothing at all without a pregnancy", () => {
    expect(pregnancySummary(null)).toBe("");
  });

  it("reads gravida and para only where she has told the hospital", () => {
    expect(gravidaPara(CURRENT)).toBe("G2 P1");
    expect(gravidaPara({ gravida: 3 })).toBe("G3");
    expect(gravidaPara({})).toBe("");
  });

  it("says when she was last seen, or that she has not been", () => {
    expect(lastSeen(CURRENT)).toContain("ANC — follow-up");
    expect(lastSeen({ last_encounter: null })).toBe("No visits recorded yet");
  });
});

describe("her history", () => {
  it("lists the earlier pregnancies without repeating the active one", () => {
    expect(previousPregnancies(lookup()).map((p) => p.number)).toEqual([1]);
  });

  it("lists them all when none is active", () => {
    const earlier = previousPregnancies(lookup({ active_pregnancy: null,
                                                 history: [PREVIOUS] }));
    expect(earlier.map((p) => p.number)).toEqual([1]);
  });

  it("copes with a patient who has none", () => {
    expect(previousPregnancies(lookup({ active_pregnancy: null, history: [] }))).toEqual([]);
    expect(previousPregnancies(null)).toEqual([]);
  });

  it("reads the active pregnancy off the payload", () => {
    expect(activePregnancy(lookup()).number).toBe(2);
    expect(activePregnancy(lookup({ active_pregnancy: null }))).toBeNull();
  });
});

describe("what recording a visit submits", () => {
  it("sends the pregnancy she is already in and the clinic she is attending", () => {
    expect(encounterBody({ pregnancy: CURRENT, visitTypeId: "2" }))
      .toEqual({ pregnancy: 7, visit_type: 2 });
  });

  it("never carries a patient or a new pregnancy", () => {
    const body = encounterBody({ pregnancy: CURRENT, visitTypeId: "4" });
    // Continuing is the absence of creating — that is the whole point.
    expect(body).not.toHaveProperty("patient");
    expect(body).not.toHaveProperty("number");
    expect(Object.keys(body).sort()).toEqual(["pregnancy", "visit_type"]);
  });

  it("offers the clinics the hospital configured, in the server's order", () => {
    expect(visitTypes(lookup()).map((t) => t.code))
      .toEqual(["anc-followup", "labour-assessment"]);
    expect(visitTypes(null)).toEqual([]);
  });
});
