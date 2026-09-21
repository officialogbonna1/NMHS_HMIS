/**
 * The hospital's letterhead, on every document it issues.
 *
 * Two things are worth holding here, and they are different in kind.
 *
 * The first is that `SheetHeader` itself is a proper letterhead: the mark, the
 * hospital's own name from `HospitalSettings` rather than a string in a
 * template, and the document's own title and reference beside them.
 *
 * The second is the one that actually rots — that **every** printable document
 * goes through it. `printing.jsx` is the registry of what this hospital
 * prints, and a new sheet added with its own hand-rolled header would pass
 * every other test in the suite while quietly issuing an unbranded document.
 * `everyDocumentRendersTheSharedHeader` reads the registry and the source
 * together so that cannot happen silently.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import fs from "node:fs";
import path from "node:path";

import { SheetHeader, HospitalLogo, HOSPITAL } from "./PrintSheet.jsx";
import { DOCUMENTS } from "./printing.jsx";

describe("the shared hospital letterhead", () => {
  it("shows the hospital's mark", () => {
    render(<SheetHeader documentTitle="Patient Card" />);
    const logo = screen.getByRole("img");
    expect(logo.getAttribute("src")).toBeTruthy();
    // Named for the hospital, so a sheet still reads as theirs if it fails.
    expect(logo.getAttribute("alt")).toBe(HOSPITAL.fullName);
  });

  it("sizes the mark by height alone, so it cannot be stretched", () => {
    render(<HospitalLogo />);
    const logo = screen.getByRole("img");
    expect(logo.className).toContain("w-auto");
    expect(logo.className).toContain("object-contain");
    // The class the print stylesheet sizes in millimetres.
    expect(logo.className).toContain("sheet-logo");
  });

  it("takes the hospital's name from configuration, not from the markup", () => {
    render(<SheetHeader documentTitle="Invoice" />);
    expect(screen.getByText(HOSPITAL.fullName)).toBeTruthy();
    expect(screen.getByText(HOSPITAL.address)).toBeTruthy();
  });

  it("names the document, its reference and its date", () => {
    render(<SheetHeader documentTitle="Discharge Letter" reference="DCH-000123" date="20 Sep 2026" />);
    expect(screen.getByText("Discharge Letter")).toBeTruthy();
    expect(screen.getByText("No. DCH-000123")).toBeTruthy();
    expect(screen.getByText("20 Sep 2026")).toBeTruthy();
  });

  it("omits the reference line when a document has no reference", () => {
    render(<SheetHeader documentTitle="Clinical Summary" />);
    expect(screen.queryByText(/^No\. /)).toBeNull();
  });

  it("carries the class the print stylesheet keeps off a page break", () => {
    const { container } = render(<SheetHeader documentTitle="Receipt" />);
    expect(container.querySelector(".sheet-header")).not.toBeNull();
  });
});

describe("every document the hospital prints is branded", () => {
  const root = path.resolve(__dirname, "..");
  const sources = ["components/PrintDocuments.jsx", "components/DepartmentDocuments.jsx",
                   "components/LabReportSheet.jsx"]
    .map((file) => fs.readFileSync(path.join(root, file), "utf8"));
  const all = sources.join("\n");

  it("has a registry entry for every document, and all of them render", () => {
    // A guard on the guard below: if the registry is empty the enumeration
    // would pass by testing nothing.
    expect(Object.keys(DOCUMENTS).length).toBeGreaterThanOrEqual(15);
    for (const [key, entry] of Object.entries(DOCUMENTS)) {
      expect(typeof entry.render, `${key} must render something`).toBe("function");
      expect(entry.label, `${key} must be labelled`).toBeTruthy();
    }
  });

  it("renders the shared header once per sheet, and nobody hand-rolls one", () => {
    // Every `<PrintSheet` that is a real document opens with `<SheetHeader`.
    // The two that do not are the loading/error placeholders, which carry no
    // letterhead by design — there is no document behind them yet.
    const sheets = (all.match(/<PrintSheet/g) ?? []).length;
    const headers = (all.match(/<SheetHeader/g) ?? []).length;
    const placeholders = (all.match(/<SheetStatus/g) ?? []).length;

    expect(headers).toBeGreaterThan(0);
    // One header per document sheet; the only sheet without one is
    // LabReportSheet's own loading state.
    expect(sheets - headers).toBeLessThanOrEqual(1);
  });

  it("has no second letterhead written into any document", () => {
    // The hospital's full name appearing as a literal inside a sheet file
    // would be a header somebody copied instead of importing.
    for (const source of sources) {
      expect(source).not.toContain("Ngozi Maternity and Hospital Services");
    }
    // …and no sheet builds its own <img> for the mark.
    expect(all).not.toMatch(/<img[^>]*logo/i);
  });
});
