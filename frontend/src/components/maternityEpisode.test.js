import { describe, expect, it } from "vitest";

import {
  BEFORE_LABOUR, DELIVERED, IN_LABOUR, babiesOf, birthDescription, canRecordDelivery,
  deliveredLabour, deliveryBody, episodeActions, episodeStage, observationSummary,
  openLabour, whereSheIs,
} from "./maternityEpisode";

// Where in the pregnancy she is today, read off the labour rows the server
// sent. Nothing here decides anything the ward cannot also see.

const OPEN = { id: 1, status: "in_progress", ward_name: "Maternity Ward", bed_number: "M01",
               admission: 4 };
const CLOSED = { id: 2, status: "delivered", ward_name: "Maternity Ward", bed_number: "M01" };

const TWINS = {
  id: 9, newborns: [
    { id: 2, birth_order: 2, sex: "F", sex_label: "Female" },
    { id: 1, birth_order: 1, sex: "M", sex_label: "Male" },
  ],
};

describe("where the pregnancy has got to", () => {
  it("is 'not in labour' before anything has happened", () => {
    expect(episodeStage([])).toBe(BEFORE_LABOUR);
  });

  it("is 'in labour' while one is open", () => {
    expect(episodeStage([OPEN])).toBe(IN_LABOUR);
    expect(openLabour([OPEN, CLOSED])).toBe(OPEN);
  });

  it("is 'delivered' once the labour closed with a delivery", () => {
    expect(episodeStage([CLOSED])).toBe(DELIVERED);
    expect(deliveredLabour([CLOSED])).toBe(CLOSED);
  });

  it("is nothing at all before the record has loaded", () => {
    // Never "not in labour": offering to open one while her record loads is
    // the same mistake the booking screen avoids.
    expect(episodeStage(null)).toBeNull();
    expect(episodeStage(undefined)).toBeNull();
  });

  it("prefers the open labour over an older delivered one", () => {
    expect(episodeStage([OPEN, CLOSED])).toBe(IN_LABOUR);
  });
});

describe("what maternity may do next", () => {
  it("offers only opening a labour before one exists", () => {
    const actions = episodeActions([]);
    expect(actions.openLabour).toBe(true);
    expect(actions.observe).toBe(false);
    expect(actions.deliver).toBe(false);
  });

  it("offers observing and delivering while one is open, and never a second labour", () => {
    const actions = episodeActions([OPEN]);
    expect(actions.openLabour).toBe(false);
    expect(actions.observe).toBe(true);
    expect(actions.deliver).toBe(true);
  });

  it("offers admitting only while she has not been admitted", () => {
    expect(episodeActions([OPEN]).admit).toBe(false);
    expect(episodeActions([{ ...OPEN, admission: null }]).admit).toBe(true);
  });

  it("offers postpartum once she has delivered, and no further delivery", () => {
    const actions = episodeActions([CLOSED]);
    expect(actions.postpartum).toBe(true);
    expect(actions.deliver).toBe(false);
    expect(actions.observe).toBe(false);
  });
});

describe("reading an observation back", () => {
  it("names only what was measured", () => {
    expect(observationSummary({ cervical_dilation_cm: 6, fetal_heart_rate: 140 }))
      .toBe("6 cm · FHR 140");
  });

  it("says nothing for an empty check rather than inventing zeros", () => {
    expect(observationSummary({})).toBe("");
    expect(observationSummary(null)).toBe("");
  });

  it("keeps a genuine zero, which is not the same as blank", () => {
    expect(observationSummary({ cervical_dilation_cm: 0 })).toBe("0 cm");
  });
});

describe("where she is lying", () => {
  it("reads the ward and bed off the admission", () => {
    expect(whereSheIs(OPEN)).toBe("Maternity Ward · Bed M01");
  });

  it("says so plainly when she has not been admitted", () => {
    expect(whereSheIs({ ...OPEN, ward_name: null })).toBe("Not admitted");
    expect(whereSheIs(null)).toBe("Not admitted");
  });
});

describe("the babies", () => {
  it("reads them back in birth order whatever order they arrived in", () => {
    expect(babiesOf(TWINS).map((baby) => baby.birth_order)).toEqual([1, 2]);
  });

  it("names a multiple birth for what it is", () => {
    expect(birthDescription(TWINS)).toBe("Twins");
    expect(birthDescription({ newborns: [{ birth_order: 1, sex: "F" }] })).toBe("One baby");
    expect(birthDescription({ newborns: [1, 2, 3].map((n) => ({ birth_order: n, sex: "F" })) }))
      .toBe("Triplets");
    expect(birthDescription({ newborns: [] })).toBe("No baby recorded");
  });
});

describe("what recording a delivery submits", () => {
  const babies = [
    { sex: "M", birth_weight_grams: "2400", apgar_1_min: "8", apgar_5_min: "9" },
    { sex: "F", birth_weight_grams: "2250" },
  ];

  it("sends one delivery with a list of babies — never two deliveries", () => {
    const body = deliveryBody({ deliveryType: "3", babies });
    expect(body.delivery_type).toBe(3);
    expect(body.newborns).toHaveLength(2);
    expect(body.newborns.map((baby) => baby.birth_order)).toEqual([1, 2]);
  });

  it("carries no patient and no pregnancy — the labour already knows", () => {
    const body = deliveryBody({ deliveryType: "3", babies });
    expect(body).not.toHaveProperty("patient");
    expect(body).not.toHaveProperty("pregnancy");
  });

  it("omits a figure nobody measured rather than sending zero", () => {
    const [, second] = deliveryBody({ deliveryType: "3", babies }).newborns;
    expect(second).not.toHaveProperty("apgar_1_min");
    expect(second.birth_weight_grams).toBe(2250);
  });

  it("will not submit without a delivery type and at least one baby", () => {
    expect(canRecordDelivery({ deliveryType: "3", babies })).toBe(true);
    expect(canRecordDelivery({ deliveryType: "", babies })).toBe(false);
    expect(canRecordDelivery({ deliveryType: "3", babies: [{ sex: "" }] })).toBe(false);
    expect(canRecordDelivery({ deliveryType: "3", babies: [] })).toBe(false);
  });
});
