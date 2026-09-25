import { describe, expect, it } from "vitest";

import {
  MATERNITY_ROLES, authorizedDepartments, hasRole, worksIn, worksInAs,
} from "./roles.js";

// **Where a member of staff may work**, as `/auth/me/` reports it.
//
// Every one of these is a *courtesy* check — it keeps somebody off a page that
// would only refuse them and keeps the nav honest. The backend asks
// `works_in(request.user, …)` against the database on every
// department-sensitive request, so nothing here is the control, and the last
// describe block says so in a test rather than only in a comment.

const SINGLE = {
  role: "doctor",
  department: "General Medicine",
  authorized_departments: [{ id: 1, code: "general-medicine", name: "General Medicine" }],
};

const BOTH = {
  role: "doctor",
  department: "General Medicine",
  authorized_departments: [
    { id: 1, code: "general-medicine", name: "General Medicine" },
    { id: 2, code: "maternity", name: "Maternity" },
  ],
};

const MIDWIFE = { role: "maternity_nurse", department: "", authorized_departments: [] };
const LEGACY = { role: "radiology", department: "Radiology / Ultrasound" };

describe("a single-department account still behaves correctly", () => {
  it("reports the one department it has", () => {
    expect(authorizedDepartments(SINGLE).map((d) => d.code)).toEqual(["general-medicine"]);
  });

  it("is authorised there and nowhere else", () => {
    expect(worksIn(SINGLE, "general-medicine")).toBe(true);
    expect(worksIn(SINGLE, "maternity")).toBe(false);
    expect(worksIn(SINGLE, "pharmacy")).toBe(false);
  });

  it("survives a payload with no list at all", () => {
    // An older token, or a response shape a test did not bother to fill in.
    expect(authorizedDepartments(LEGACY)).toEqual([]);
    expect(worksIn(LEGACY, "radiology")).toBe(false);
    expect(() => worksIn(undefined, "radiology")).not.toThrow();
  });
});

describe("a multi-department account", () => {
  it("reports both", () => {
    expect(authorizedDepartments(BOTH).map((d) => d.code))
      .toEqual(["general-medicine", "maternity"]);
  });

  it("is authorised in each of them", () => {
    expect(worksIn(BOTH, "general-medicine")).toBe(true);
    expect(worksIn(BOTH, "maternity")).toBe(true);
  });

  it("is authorised in nothing else", () => {
    for (const elsewhere of ["pharmacy", "laboratory", "radiology", "theatre"]) {
      expect(worksIn(BOTH, elsewhere)).toBe(false);
    }
  });

  it("matches on the code or the name, and ignores case", () => {
    expect(worksIn(BOTH, "MATERNITY")).toBe(true);
    expect(worksIn(BOTH, "Maternity")).toBe(true);
    expect(worksIn(BOTH, "maternity")).toBe(true);
  });

  it("is authorised nowhere by an empty or missing code", () => {
    expect(worksIn(BOTH, "")).toBe(false);
    expect(worksIn(BOTH, null)).toBe(false);
    expect(worksIn(BOTH, undefined)).toBe(false);
  });
});

describe("role AND department, never one of the two", () => {
  it("needs both", () => {
    expect(worksInAs(BOTH, "maternity", MATERNITY_ROLES)).toBe(true);
    // Posted to Maternity, but the role is not one that works there.
    const cashier = { ...BOTH, role: "cashier" };
    expect(worksInAs(cashier, "maternity", MATERNITY_ROLES)).toBe(false);
    // The right role, posted somewhere else.
    expect(worksInAs(SINGLE, "maternity", MATERNITY_ROLES)).toBe(false);
  });

  it("lets a role that IS the department through without a posting", () => {
    // The midwife is the labour ward by definition — the same exception
    // `in_maternity_team` makes on the server.
    expect(worksInAs(MIDWIFE, "maternity", MATERNITY_ROLES,
                     { always: ["maternity_nurse"] })).toBe(true);
    expect(worksInAs(MIDWIFE, "maternity", MATERNITY_ROLES)).toBe(false);
  });

  it("does not let a posting stand in for a role the person lacks", () => {
    const pharmacist = { ...BOTH, role: "pharmacist" };
    expect(hasRole(pharmacist, MATERNITY_ROLES)).toBe(false);
    expect(worksInAs(pharmacist, "maternity", MATERNITY_ROLES)).toBe(false);
  });

  it("still lets an administrator through, as every role check here does", () => {
    const admin = { role: "admin", authorized_departments: [] };
    expect(hasRole(admin, MATERNITY_ROLES)).toBe(true);
  });
});

describe("what this is not", () => {
  it("is a reading of the server's answer, never a grant", () => {
    // Editing the list in memory is the whole of what a manipulated client
    // can do. It changes this function's answer and nothing else: every
    // department-sensitive endpoint re-asks the database.
    const tampered = {
      ...SINGLE,
      authorized_departments: [
        ...SINGLE.authorized_departments,
        { id: 99, code: "maternity", name: "Maternity" },
      ],
    };
    expect(worksIn(tampered, "maternity")).toBe(true);
    // …which is exactly why the frontend is not the control. The backend test
    // `test_sending_a_department_id_in_a_request_grants_nothing` is the one
    // that decides, and it returns an empty list to this same account.
    expect(worksIn(SINGLE, "maternity")).toBe(false);
  });
});
