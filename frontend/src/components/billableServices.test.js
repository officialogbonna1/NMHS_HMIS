/**
 * The desk's reading of the hospital's billable services.
 *
 * A pure module (the `refundPolicy.js` pattern), because the decisions worth
 * holding here — what is searched, what is grouped, what is posted as a
 * charge — are decisions, not DOM. The list itself is the server's
 * (`billing/catalogue.py`); nothing in this module may ever invent a service
 * or a price.
 */
import { describe, expect, it, vi } from "vitest";

import {
  billServicesBody, chargeBody, grouped, isWriteIn, matching, priceOf, serviceQuery,
  toggle, totalOf,
} from "./billableServices";

const FBC = { key: "lab_test:1", source: "lab_test", id: 1, name: "Full Blood Count (FBC)",
              category: "laboratory", source_type: "laboratory", price: "3500.00",
              detail: "Haematology · Whole blood" };
const MP = { key: "lab_test:2", source: "lab_test", id: 2, name: "Malaria Parasite (MP)",
             category: "laboratory", source_type: "laboratory", price: "1500.00",
             detail: "Parasitology · Whole blood" };
const SCAN = { key: "billing_item:9", source: "billing_item", id: 9, name: "Obstetric Ultrasound",
               category: "ultrasound", source_type: "ultrasound", price: "15000.00", detail: "" };

describe("the query", () => {
  it("asks the shared endpoint, never the price list alone", async () => {
    const api = { get: vi.fn().mockResolvedValue({ data: { results: [FBC] } }) };
    const query = serviceQuery(api, { category: "laboratory", search: " malaria " });
    await query.queryFn();
    expect(api.get).toHaveBeenCalledWith("/billable-services/", {
      params: { category: "laboratory", search: "malaria" },
    });
  });

  it("keys the cache by category and term, so two tabs do not share a list", () => {
    expect(serviceQuery({}, { category: "laboratory" }).queryKey)
      .not.toEqual(serviceQuery({}, { category: "ultrasound" }).queryKey);
  });

  it("sends no empty search and survives a bare array", async () => {
    const api = { get: vi.fn().mockResolvedValue({ data: [MP] }) };
    const query = serviceQuery(api, { category: "laboratory" });
    expect(await query.queryFn()).toEqual([MP]);
    expect(api.get.mock.calls[0][1]).toEqual({ params: { category: "laboratory" } });
  });
});

describe("narrowing a long catalogue", () => {
  it("matches on the name and on what the service is", () => {
    expect(matching([FBC, MP], "malaria")).toEqual([MP]);
    expect(matching([FBC, MP], "haematology")).toEqual([FBC]);
  });

  it("returns everything for an empty term — a picker opens browsable", () => {
    expect(matching([FBC, MP, SCAN], "  ")).toHaveLength(3);
  });

  it("never invents a row", () => {
    expect(matching([], "anything")).toEqual([]);
    expect(matching(undefined, "")).toEqual([]);
  });
});

describe("grouping", () => {
  it("heads each group with what the catalogue said, not a hard-coded list", () => {
    expect(grouped([FBC, MP]).map(([heading]) => heading))
      .toEqual(["Haematology", "Parasitology"]);
  });

  it("falls back to the category label where a service has no detail", () => {
    expect(grouped([{ ...SCAN, category_label: "Ultrasound / Imaging" }])[0][0])
      .toBe("Ultrasound / Imaging");
  });
});

describe("what gets billed", () => {
  it("posts the catalogue's own name, price and source type", () => {
    expect(chargeBody(3, SCAN)).toEqual({
      patient: 3, description: "Obstetric Ultrasound", amount: "15000.00",
      source_type: "ultrasound",
    });
  });

  it("falls back to the category when a row carries no source type", () => {
    const { source_type: _unused, ...withoutSource } = SCAN;
    expect(chargeBody(3, withoutSource).source_type).toBe("ultrasound");
  });

  it("reads a price as a number and totals a chosen set", () => {
    expect(priceOf(SCAN)).toBe(15000);
    expect(priceOf({})).toBe(0);
    expect(totalOf([FBC, MP])).toBe(5000);
    expect(totalOf(undefined)).toBe(0);
  });

  it("knows the one category that is typed in rather than picked", () => {
    expect(isWriteIn("other")).toBe(true);
    expect(isWriteIn("laboratory")).toBe(false);
  });
});


describe("building a selection", () => {
  it("adds a service that is not in it", () => {
    expect(toggle([], FBC).map((s) => s.key)).toEqual(["lab_test:1"]);
    expect(toggle([FBC], MP).map((s) => s.key)).toEqual(["lab_test:1", "lab_test:2"]);
  });

  it("removes one that is, leaving the others where they were", () => {
    expect(toggle([FBC, MP, SCAN], MP).map((s) => s.key))
      .toEqual(["lab_test:1", "billing_item:9"]);
  });

  it("is its own inverse, however many times it is pressed", () => {
    let selection = [FBC, MP];
    for (let i = 0; i < 5; i += 1) selection = toggle(selection, MP);
    // Odd number of presses: off.
    expect(selection.map((s) => s.key)).toEqual(["lab_test:1"]);
    expect(toggle(selection, MP).map((s) => s.key)).toEqual(["lab_test:1", "lab_test:2"]);
  });

  it("never mutates the selection it was given", () => {
    const before = [FBC];
    toggle(before, MP);
    expect(before).toEqual([FBC]);
  });

  it("moves the total with it", () => {
    let selection = toggle(toggle([], FBC), MP);
    expect(totalOf(selection)).toBe(5000);
    selection = toggle(selection, MP);
    expect(totalOf(selection)).toBe(3500);
  });
});

describe("what one submission sends", () => {
  it("sends the selected identities and no money at all", () => {
    expect(billServicesBody(3, [FBC, MP, SCAN])).toEqual({
      patient: 3,
      services: ["lab_test:1", "lab_test:2", "billing_item:9"],
    });
  });

  it("carries a price nowhere — the server prices the bill", () => {
    const body = billServicesBody(3, [FBC]);
    expect(JSON.stringify(body)).not.toContain("3500");
  });

  it("copes with an empty selection rather than inventing one", () => {
    expect(billServicesBody(3, []).services).toEqual([]);
    expect(billServicesBody(3, undefined).services).toEqual([]);
  });
});
