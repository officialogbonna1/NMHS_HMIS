import { describe, expect, it } from "vitest";
import { emptyRow, isBlank, materialsBody, rowProblem } from "./procedureMaterials.js";

// The bedside half of received = used + remaining + wastage. The server is
// what refuses (`workflow/procedures.clean_materials`); these hold that the
// screen agrees with it before anybody presses Save.

const row = (overrides) => ({ ...emptyRow(), name: "Gauze swab", ...overrides });

describe("rowProblem", () => {
  it("passes a row that balances, decimals included", () => {
    expect(rowProblem(row({ quantity_received: "10", quantity_used: "6",
                            quantity_remaining: "3", wastage: "1" }))).toBeNull();
    expect(rowProblem(row({ quantity_received: "5", quantity_used: "4.5",
                            quantity_remaining: "0.5" }))).toBeNull();
  });

  it("refuses a row that does not add up, saying both figures", () => {
    expect(rowProblem(row({ quantity_received: "10", quantity_used: "6", quantity_remaining: "3" })))
      .toBe("Received (10) must equal used + remaining + wastage (9).");
  });

  it("records used alone when nothing was received to balance", () => {
    expect(rowProblem(row({ quantity_used: "2" }))).toBeNull();
  });

  it("refuses negatives, non-numbers, no name and no quantity", () => {
    expect(rowProblem(row({ quantity_used: "-1" }))).toMatch(/negative/);
    expect(rowProblem(row({ quantity_used: "two" }))).toMatch(/numbers/);
    expect(rowProblem({ ...emptyRow(), quantity_used: "1" })).toMatch(/Name the item/);
    expect(rowProblem(row({}))).toMatch(/how much was used/);
  });

  it("ignores a row nobody has typed into", () => {
    expect(isBlank(emptyRow())).toBe(true);
    expect(rowProblem(emptyRow())).toBeNull();
  });
});

describe("materialsBody", () => {
  it("drops blank rows and blank fields, and trims", () => {
    expect(materialsBody([emptyRow(), row({ quantity_used: " 2 ", unit: " roll " })]))
      .toEqual([{ name: "Gauze swab", quantity_used: "2", unit: "roll" }]);
  });
});
