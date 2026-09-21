/**
 * The application's alerts.
 *
 * Two mechanisms, both reusable, both shared by every page: `useToast()` for
 * the message that answers an action, and `useConfirm()` for the question that
 * precedes a destructive or costly one. Six places used to ask that question
 * with `window.confirm()`, which cannot be styled, cannot show a figure as a
 * figure, and blocks the thread.
 *
 * What these tests hold is the part that rots: the tones, the accessible
 * semantics (which are not decoration — an error that a screen reader
 * announces politely is an error somebody misses), that a long name wraps
 * instead of widening a phone, and that a confirmation actually resolves the
 * caller's promise either way.
 *
 * The notification system is deliberately out of scope here and untouched.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ToastProvider, useToast } from "./Toaster.jsx";
import { ConfirmProvider, useConfirm } from "./ConfirmAlert.jsx";

/* ------------------------------------------------------------------ toasts */

function Raiser({ options }) {
  const { showToast } = useToast();
  return <button onClick={() => showToast(options)}>raise</button>;
}

function showAlert(options) {
  return render(
    <ToastProvider>
      <Raiser options={options} />
    </ToastProvider>,
  );
}

describe("the alert that answers an action", () => {
  let user;
  beforeEach(() => { user = userEvent.setup(); });

  it("shows a success alert, announced politely", async () => {
    showAlert({ title: "Saved", message: "The note is on the chart.", tone: "success" });
    await user.click(screen.getByText("raise"));

    const alert = await screen.findByRole("status");
    expect(alert.textContent).toContain("Saved");
    expect(alert.textContent).toContain("The note is on the chart.");
    // Not colour alone: the tone is in the accessible text too.
    expect(alert.textContent).toContain("Success");
  });

  it("shows an error alert, announced assertively", async () => {
    showAlert({ title: "Could not dispense", message: "Insufficient stock.", tone: "error" });
    await user.click(screen.getByText("raise"));

    const alert = await screen.findByRole("alert");
    expect(alert.getAttribute("aria-live")).toBe("assertive");
    expect(alert.textContent).toContain("Could not dispense");
    expect(alert.textContent).toContain("Error");
  });

  it("shows a warning alert", async () => {
    showAlert({ title: "No vitals recorded", tone: "warning" });
    await user.click(screen.getByText("raise"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No vitals recorded");
    expect(alert.textContent).toContain("Warning");
  });

  it("shows an informational alert", async () => {
    showAlert({ title: "Queued for consultation", tone: "info" });
    await user.click(screen.getByText("raise"));

    const alert = await screen.findByRole("status");
    expect(alert.textContent).toContain("Information");
  });

  it("shows a validation alert with the server's own wording", async () => {
    // What a form raises when the API refuses a field: the message is the
    // server's (`readError` in api/errors.js), the presentation is this.
    showAlert({
      title: "Could not save",
      message: "Diagnosis: This field is required.",
      tone: "error",
    });
    await user.click(screen.getByText("raise"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Diagnosis: This field is required.");
  });

  it("carries figures beside the sentence, as rows", async () => {
    // The laboratory shape: a test and what it costs. Both from the caller.
    showAlert({
      title: "Laboratory test selected",
      details: [{ label: "Malaria Parasite Test", value: "₦2,500" }],
    });
    await user.click(screen.getByText("raise"));

    expect(await screen.findByText("Malaria Parasite Test")).toBeTruthy();
    expect(screen.getByText("₦2,500")).toBeTruthy();
    // A definition list, so the figure reads as the value of the name.
    expect(screen.getByText("Malaria Parasite Test").tagName).toBe("DT");
    expect(screen.getByText("₦2,500").tagName).toBe("DD");
  });

  it("can be dismissed with an accessible button", async () => {
    showAlert({ title: "Saved", duration: 0 });
    await user.click(screen.getByText("raise"));
    await screen.findByRole("status");

    await user.click(screen.getByRole("button", { name: /dismiss alert/i }));
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });

  it("wraps a long name instead of widening the page", async () => {
    const long = "Comprehensive Metabolic Panel with Reflex to Hepatic Function and Electrolytes";
    showAlert({ title: long, details: [{ label: long, value: "₦18,000" }] });
    await user.click(screen.getByText("raise"));

    const alert = await screen.findByRole("status");
    // The card is width-capped and the text breaks; nothing is set to nowrap.
    expect(alert.className).toMatch(/max-w-\[420px\]/);
    expect(alert.querySelector(".break-words")).not.toBeNull();
  });

  it("sits above the modal layer so a dialog cannot hide it", async () => {
    const { container } = showAlert({ title: "Saved" });
    await user.click(screen.getByText("raise"));
    const stack = container.parentElement.querySelector(".fixed.inset-x-0");
    expect(stack.className).toContain("z-[100]");
    // The stack itself never swallows clicks meant for the page.
    expect(stack.className).toContain("pointer-events-none");
  });
});

describe("an alert clears itself", () => {
  afterEach(() => { vi.useRealTimers(); });

  it("dismisses itself after its own duration", () => {
    vi.useFakeTimers();
    showAlert({ title: "Saved", duration: 1000 });

    // `fireEvent`, not `userEvent`: userEvent schedules its own waits on the
    // clock this test has just frozen, so it never gets as far as the click.
    // `fireEvent` is already wrapped in `act`, so the alert is on screen
    // synchronously and no `findBy*` (which also waits) is needed.
    fireEvent.click(screen.getByText("raise"));
    expect(screen.getByRole("status")).toBeTruthy();

    act(() => { vi.advanceTimersByTime(1100); });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("stays until dismissed when it is given no duration", () => {
    vi.useFakeTimers();
    showAlert({ title: "Take this to the cash desk", duration: 0 });
    fireEvent.click(screen.getByText("raise"));

    act(() => { vi.advanceTimersByTime(60_000); });
    // A minute later it is still there: some alerts have to be read, not
    // glimpsed, and `duration: 0` is how a caller says so.
    expect(screen.getByRole("status")).toBeTruthy();
  });
});

describe("motion, and doing without it", () => {
  it("enters with the shared animation", async () => {
    const user = userEvent.setup();
    showAlert({ title: "Saved" });
    await user.click(screen.getByText("raise"));

    const alert = await screen.findByRole("status");
    expect(alert.className).toContain("animate-toast-in");
  });

  it("is switched off for somebody who asked their system for less motion", async () => {
    // The animation is one shared class, and `index.css` already turns it off
    // under `prefers-reduced-motion`. Asserted against the stylesheet because
    // jsdom applies no media queries — what matters is that the rule exists
    // and names the class the alert actually carries.
    const fs = await import("node:fs");
    const path = await import("node:path");
    const css = fs.readFileSync(path.resolve(__dirname, "..", "index.css"), "utf8");

    const block = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(block).toContain(".animate-toast-in");
    expect(block).toMatch(/animation:\s*none/);
  });
});

/* ----------------------------------------------------------- confirmations */

function Asker({ options, onAnswer }) {
  const { ask } = useConfirm();
  return <button onClick={async () => onAnswer(await ask(options))}>ask</button>;
}

function askConfirm(options, onAnswer = () => {}) {
  return render(
    <ConfirmProvider>
      <Asker options={options} onAnswer={onAnswer} />
    </ConfirmProvider>,
  );
}

describe("the confirmation alert", () => {
  let user;
  beforeEach(() => { user = userEvent.setup(); });
  afterEach(() => { document.body.style.overflow = ""; });

  it("asks the question in a proper dialog, not a browser popup", async () => {
    askConfirm({ title: "Write off this expired stock?", message: "12 unit(s) will go." });
    await user.click(screen.getByText("ask"));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(screen.getByText("Write off this expired stock?")).toBeTruthy();
    expect(screen.getByText(/12 unit\(s\) will go\./)).toBeTruthy();
  });

  it("lists what is being agreed to, with each figure", async () => {
    // The laboratory panel: names and prices straight from the catalogue.
    askConfirm({
      title: "Laboratory tests selected",
      message: "Add all 2 tests in “Malaria screen” to this referral?",
      details: [
        { label: "Malaria Parasite Test", value: "₦2,500" },
        { label: "Full Blood Count", value: "₦3,500" },
      ],
      total: { label: "Added to the patient's bill", value: "₦6,000" },
      tone: "info",
    });
    await user.click(screen.getByText("ask"));
    await screen.findByRole("dialog");

    expect(screen.getByText("Malaria Parasite Test")).toBeTruthy();
    expect(screen.getByText("₦2,500")).toBeTruthy();
    expect(screen.getByText("Full Blood Count")).toBeTruthy();
    expect(screen.getByText("₦3,500")).toBeTruthy();
    expect(screen.getByText("₦6,000")).toBeTruthy();
    expect(screen.getByText("Information")).toBeTruthy();
  });

  it("resolves true when confirmed", async () => {
    const answered = vi.fn();
    askConfirm({ title: "Remove this entry?", confirmLabel: "Remove" }, answered);
    await user.click(screen.getByText("ask"));
    await screen.findByRole("dialog");

    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(answered).toHaveBeenCalledWith(true));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("resolves false when cancelled", async () => {
    const answered = vi.fn();
    askConfirm({ title: "Remove this entry?" }, answered);
    await user.click(screen.getByText("ask"));
    await screen.findByRole("dialog");

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(answered).toHaveBeenCalledWith(false));
  });

  it("treats Escape as 'no', the way dismissing a browser confirm does", async () => {
    const answered = vi.fn();
    askConfirm({ title: "Delete this permanently?" }, answered);
    await user.click(screen.getByText("ask"));
    await screen.findByRole("dialog");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(answered).toHaveBeenCalledWith(false));
  });

  it("defaults to the danger treatment, since most of these are destructive", async () => {
    askConfirm({ title: "Delete this permanently?" });
    await user.click(screen.getByText("ask"));
    await screen.findByRole("dialog");
    expect(screen.getByText("Warning")).toBeTruthy();
  });

  it("handles a long service name without overflowing", async () => {
    askConfirm({
      title: "Laboratory tests selected",
      details: [{
        label: "Comprehensive Metabolic Panel with Reflex to Hepatic Function",
        value: "₦18,000",
      }],
    });
    await user.click(screen.getByText("ask"));
    await screen.findByRole("dialog");

    const name = screen.getByText(/Comprehensive Metabolic Panel/);
    expect(name.className).toContain("break-words");
    expect(name.className).toContain("min-w-0");
  });
});

/* ------------------------------------------- the notification system, untouched */

describe("the notification system is not part of this", () => {
  it("has no alert component reaching into it", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const here = path.resolve(__dirname);
    for (const file of ["Toaster.jsx", "ConfirmAlert.jsx"]) {
      const source = fs.readFileSync(path.join(here, file), "utf8");
      expect(source).not.toMatch(/\/notifications|unread-count|notify\(/);
      // …and they call no API at all: an alert is what the page already knows.
      expect(source).not.toContain("api/client");
    }
  });
});
