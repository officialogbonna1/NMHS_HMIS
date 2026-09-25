import { describe, expect, it } from "vitest";

import {
  ASSIGN_ROLES, RESPONSIBILITIES, UNASSIGNED, assignDepartmentBody, assignNurseBody,
  assignPersonBody, assignmentState, canAssign, changeLabel, holderOf,
  responsibleDoctor, responsibleNurse, staffLabel,
} from "./maternityAssignment.js";

// The two questions kept apart: which department she is in (visibility) and
// who is responsible for her (assignment). Everything here is the second one
// never being allowed to answer the first.

describe("who may assign", () => {
  it("is the desk and administration", () => {
    for (const role of ["reception", "admin", "hospital_admin"]) {
      expect(canAssign({ role })).toBe(true);
    }
  });

  it("is not a maternity nurse — she claims work, she does not hand it over", () => {
    expect(canAssign({ role: "maternity_nurse" })).toBe(false);
    expect(ASSIGN_ROLES).not.toContain("maternity_nurse");
  });

  it("is nobody with no role at all", () => {
    expect(canAssign(null)).toBe(false);
    expect(canAssign({})).toBe(false);
  });
});

describe("a row in the picker", () => {
  const row = {
    name: "Eze, Ngozi", patient_number: "NMHS-P000015",
    pregnancy_number: 2, gestation: "30w 4d", assigned_nurse: "Grace Nwosu",
  };

  it("names the responsible midwife, or says plainly that nobody is", () => {
    expect(responsibleNurse(row)).toBe("Grace Nwosu");
    expect(responsibleNurse({ ...row, assigned_nurse: null })).toBe(UNASSIGNED);
  });
});

describe("what the panel says", () => {
  it("asks for the department when she is not in Maternity at all", () => {
    const where = assignmentState({ in_maternity: false });
    expect(where.key).toBe("outside");
    expect(where.detail).toMatch(/ward's list/);
  });

  it("leads with the department, because that is what decides access", () => {
    const where = assignmentState({ in_maternity: true, assigned_nurse: null });
    expect(where.title).toBe("Maternity");
    expect(where.detail).toMatch(/whole maternity team can see and work with her/);
  });

  it("says in as many words that the names do not limit access", () => {
    const where = assignmentState({
      in_maternity: true,
      assigned_nurse: { id: 5, name: "Grace Nwosu" },
      assigned_doctor: { id: 9, name: "Dara Ibe" },
    });
    // The department is still the headline — a named person never becomes it.
    expect(where.title).toBe("Maternity");
    expect(where.detail).toMatch(/do not limit who has access/);
  });
});

describe("the two responsibilities", () => {
  const held = {
    in_maternity: true,
    assigned_nurse: { id: 5, name: "Grace Nwosu" },
    assigned_doctor: { id: 9, name: "Dara Ibe" },
  };

  it("are two rows, each with its own field", () => {
    expect(RESPONSIBILITIES.map((r) => r.field)).toEqual(["nurse", "doctor"]);
    expect(RESPONSIBILITIES.map((r) => r.label))
      .toEqual(["Assigned Nurse", "Assigned Doctor"]);
  });

  it("each read their own holder", () => {
    expect(holderOf(held, "nurse").name).toBe("Grace Nwosu");
    expect(holderOf(held, "doctor").name).toBe("Dara Ibe");
  });

  it("say 'Not assigned' rather than leaving a blank", () => {
    expect(holderOf({ in_maternity: true }, "nurse")).toBeNull();
    expect(RESPONSIBILITIES.every((r) => r.empty === "Not assigned")).toBe(true);
  });

  it("each say that the rest of the team still sees her", () => {
    expect(RESPONSIBILITIES[0].hint).toMatch(/Every maternity nurse sees her either way/);
    expect(RESPONSIBILITIES[1].hint).toMatch(/Every maternity doctor sees her either way/);
  });

  it("label the button for what is already there", () => {
    expect(changeLabel(held, "nurse")).toBe("Change midwife");
    expect(changeLabel(held, "doctor")).toBe("Change doctor");
    expect(changeLabel({ in_maternity: true }, "doctor")).toBe("Assign doctor");
  });

  it("send one field per call, never both", () => {
    expect(assignPersonBody({ id: 3 }, "doctor", "9")).toEqual({ patient: 3, doctor: 9 });
    expect(assignPersonBody({ id: 3 }, "nurse", "5")).toEqual({ patient: 3, nurse: 5 });
    expect(assignPersonBody({ id: 3 }, "doctor", "9")).not.toHaveProperty("nurse");
    expect(assignPersonBody({ id: 3 }, "nurse", "5")).not.toHaveProperty("doctor");
  });

  it("clear either explicitly", () => {
    expect(assignPersonBody({ id: 3 }, "doctor", "")).toEqual({ patient: 3, doctor: null });
  });
});

describe("who is responsible, on a picker row", () => {
  it("names each, or says nobody is", () => {
    expect(responsibleDoctor({ assigned_doctor: "Dara Ibe" })).toBe("Dara Ibe");
    expect(responsibleDoctor({ assigned_doctor: null })).toBe(UNASSIGNED);
  });
});

describe("what is sent", () => {
  it("clears the nurse explicitly, because 'nobody' is a real choice", () => {
    expect(assignNurseBody({ id: 3 }, "")).toEqual({ patient: 3, nurse: null });
  });

  it("carries the chosen midwife as a number", () => {
    expect(assignNurseBody({ id: 3 }, "5")).toEqual({ patient: 3, nurse: 5 });
  });

  it("omits the nurse when assigning the department with nobody named", () => {
    expect(assignDepartmentBody({ id: 3 }, "")).toEqual({ patient: 3 });
    expect(assignDepartmentBody({ id: 3 }, "5")).toEqual({ patient: 3, nurse: 5 });
  });

  it("carries no department on a nurse change — that is a separate decision", () => {
    expect(assignNurseBody({ id: 3 }, "5")).not.toHaveProperty("department");
  });
});

describe("the midwife selector's labels", () => {
  it("says who she is and what she is", () => {
    expect(staffLabel({ name: "Grace Nwosu", role_label: "Maternity" }))
      .toBe("Grace Nwosu — Maternity");
  });

  it("falls back to the name alone", () => {
    expect(staffLabel({ name: "Grace Nwosu" })).toBe("Grace Nwosu");
    expect(staffLabel(null)).toBe("");
  });
});
