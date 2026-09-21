/**
 * The document watermark.
 *
 * The hospital's own mark, very faint, behind every printable document — a
 * visual sign that a sheet is an NMHS record. It is an authenticity *marking*
 * and nothing more: it proves nothing cryptographically, and these tests do
 * not pretend otherwise.
 *
 * What is worth holding: that it is applied once, centrally, so every document
 * inherits it; that it uses the **same** asset as the letterhead rather than a
 * second copy; that it sits behind the content and never over it; and that the
 * loading and error placeholders do *not* get it, since there is no document
 * behind them to authenticate.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import fs from "node:fs";
import path from "node:path";

import PrintSheet, { SheetStatus, SheetHeader, HOSPITAL } from "./PrintSheet.jsx";

const printArea = (container) => container.querySelector("#print-area");

describe("the shared printable wrapper carries the watermark", () => {
  it("marks the print area and supplies the image as a custom property", () => {
    const { container } = render(
      <PrintSheet title="Invoice" onClose={() => {}}><p>Body</p></PrintSheet>,
    );
    const area = printArea(container);
    expect(area.className).toContain("sheet-watermarked");
    // The mechanism is a CSS custom property, not an <img> in the flow.
    expect(area.style.getPropertyValue("--sheet-watermark")).toMatch(/^url\(/);
  });

  it("uses the same logo asset as the letterhead, not a second copy", () => {
    const { container } = render(
      <PrintSheet title="Invoice" onClose={() => {}}>
        <SheetHeader documentTitle="Invoice" />
      </PrintSheet>,
    );
    const property = printArea(container).style.getPropertyValue("--sheet-watermark");
    const headerLogo = screen.getByAltText(HOSPITAL.fullName).getAttribute("src");
    expect(property).toBe(`url(${headerLogo})`);
  });

  it("adds no image element to the document flow", () => {
    const { container } = render(
      <PrintSheet title="Invoice" onClose={() => {}}><p>Body</p></PrintSheet>,
    );
    // The only <img> a document may carry is the letterhead; a sheet with no
    // header has none at all.
    expect(container.querySelectorAll("#print-area img").length).toBe(0);
  });

  it("puts the document content in its own layer above the mark", () => {
    const { container } = render(
      <PrintSheet title="Invoice" onClose={() => {}}><p>Body text</p></PrintSheet>,
    );
    const content = printArea(container).querySelector(".sheet-content");
    expect(content).not.toBeNull();
    expect(content.textContent).toContain("Body text");
  });

  it("does not disturb what the document says", () => {
    render(
      <PrintSheet title="Receipt" onClose={() => {}}>
        <SheetHeader documentTitle="Receipt" reference="PAY-000009" />
        <p>Amount paid: ₦2,000.00</p>
      </PrintSheet>,
    );
    // "Receipt" is both the preview's title bar and the document's own title.
    expect(screen.getAllByText("Receipt").length).toBe(2);
    expect(screen.getByText("No. PAY-000009")).toBeTruthy();
    expect(screen.getByText("Amount paid: ₦2,000.00")).toBeTruthy();
  });
});

describe("a sheet that is not a document is not watermarked", () => {
  it("leaves the loading and error placeholder unmarked", () => {
    for (const isError of [false, true]) {
      const { container, unmount } = render(
        <SheetStatus title="Laboratory report" isError={isError} onClose={() => {}} />,
      );
      const area = printArea(container);
      expect(area.className).not.toContain("sheet-watermarked");
      expect(area.style.getPropertyValue("--sheet-watermark")).toBe("");
      unmount();
    }
  });

  it("can be switched off explicitly", () => {
    const { container } = render(
      <PrintSheet title="Loading" onClose={() => {}} watermark={false}><p>Loading…</p></PrintSheet>,
    );
    expect(printArea(container).className).not.toContain("sheet-watermarked");
  });
});

describe("every registered document inherits it", () => {
  const components = path.resolve(__dirname);
  const sources = ["PrintDocuments.jsx", "DepartmentDocuments.jsx", "LabReportSheet.jsx"]
    .map((file) => fs.readFileSync(path.join(components, file), "utf8"));
  const all = sources.join("\n");

  it("watermarks by default, so no document has to ask", () => {
    const sheet = fs.readFileSync(path.join(components, "PrintSheet.jsx"), "utf8");
    expect(sheet).toMatch(/watermark\s*=\s*true/);
  });

  it("is switched off only on the two placeholders", () => {
    // `SheetStatus` and LabReportSheet's own loading state. Any growth in this
    // number means a real document has silently lost its mark.
    const optedOut = (all.match(/watermark=\{false\}/g) ?? []).length
      + (fs.readFileSync(path.join(components, "PrintSheet.jsx"), "utf8")
          .match(/watermark=\{false\}/g) ?? []).length;
    expect(optedOut).toBe(2);
  });

  it("is never hand-rolled into an individual document", () => {
    expect(all).not.toContain("sheet-watermarked");
    expect(all).not.toContain("--sheet-watermark");
  });
});

describe("the asset", () => {
  it("exists once, in the source tree, and is not duplicated", () => {
    const assets = path.resolve(__dirname, "..", "assets");
    const images = fs.readdirSync(assets)
      .filter((name) => /\.(jpe?g|png|svg|webp|gif)$/i.test(name));
    expect(images).toEqual(["Ngozi Maternity and Hospital Services.jpg"]);
  });

  it("is imported once, by the shared print sheet alone", () => {
    const components = path.resolve(__dirname);
    const importing = fs.readdirSync(components)
      .filter((name) => name.endsWith(".jsx") && !name.includes(".test."))
      .filter((name) => fs.readFileSync(path.join(components, name), "utf8")
        .includes("assets/Ngozi Maternity and Hospital Services.jpg"));
    expect(importing).toEqual(["PrintSheet.jsx"]);
  });
});

describe("the print stylesheet", () => {
  const css = fs.readFileSync(path.resolve(__dirname, "..", "index.css"), "utf8");

  it("draws the mark with a pseudo-element behind the content", () => {
    expect(css).toMatch(/\.sheet-watermarked::before/);
    expect(css).toMatch(/background-image:\s*var\(--sheet-watermark\)/);
    expect(css).toMatch(/\.sheet-watermarked\s*>\s*\.sheet-content\s*\{[^}]*z-index:\s*1/);
  });

  it("keeps it faint enough to read through", () => {
    const opacities = [...css.matchAll(/opacity:\s*([\d.]+)/g)].map((m) => Number(m[1]));
    expect(opacities.some((value) => value > 0 && value <= 0.1)).toBe(true);
  });

  it("keeps the aspect ratio and never stretches the mark", () => {
    // Width given, height `auto` — constraining both is how a logo is skewed.
    expect(css).toMatch(/background-size:\s*110mm auto/);
    expect(css).not.toMatch(/background-size:\s*100%\s+100%/);
  });

  it("forces the mark to print, since Chrome drops background graphics", () => {
    expect(css).toMatch(/print-color-adjust:\s*exact/);
  });

  it("takes no space and intercepts no clicks", () => {
    expect(css).toMatch(/pointer-events:\s*none/);
    expect(css).toMatch(/\.sheet-watermarked::before\s*\{[^}]*position:\s*absolute/);
  });
});
