import { describe, expect, it } from "vitest";

import { directionsOf, productLabel } from "./prescriptionDirections.js";

describe("directionsOf", () => {
  it("joins the parts a prescriber wrote, in reading order", () => {
    expect(directionsOf({
      dosage_instructions: "1 tablet", frequency: "Three times daily", duration: "5 days", route: "oral",
    })).toBe("1 tablet · Three times daily · for 5 days · Oral");
  });

  it("prefers the server's route label and never doubles 'for'", () => {
    expect(directionsOf({ duration: "for 7 days", route: "im", route_label: "Intramuscular (IM)" }))
      .toBe("for 7 days · Intramuscular (IM)");
  });

  it("reads an older script that only ever had a dose line exactly as before", () => {
    expect(directionsOf({ dosage_instructions: "Twice daily", frequency: "", duration: "", route: "" }))
      .toBe("Twice daily");
    expect(directionsOf(null)).toBe("");
  });
});

describe("productLabel", () => {
  it("adds strength and form where the catalogue has them", () => {
    expect(productLabel({ name: "Amoxicillin", strength: "500 mg", dosage_form: "Capsule" }))
      .toBe("Amoxicillin · 500 mg · Capsule");
    expect(productLabel({ name: "Gauze swab", strength: "", dosage_form: "" })).toBe("Gauze swab");
    expect(productLabel({ strength: "250 mg" }, "Paracetamol")).toBe("Paracetamol · 250 mg");
  });
});
