/**
 * The laboratory test-selection alert, at its own call site.
 *
 * Adding a panel to a referral puts several tests — and several charges — on a
 * patient's bill, so the workflow has always asked first. It asked with
 * `window.confirm()`: a wall of plain text with a bullet list made of `\n`,
 * the total glued onto the end of a sentence, and no way to show a price as a
 * price. This is the same question, the same decision and the same code on
 * "yes"; only the presentation changed.
 *
 * What these tests hold is that the alert is built from the **catalogue's own
 * data** — every name and figure here comes from the mocked `/lab-tests/` and
 * `/lab-panels/` responses, and nothing about the alert is written into the
 * page — and that confirming and cancelling still do exactly what they did.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const get = vi.fn();
vi.mock("../api/client", () => ({ default: { get: (...a) => get(...a) } }));

const { LabTestChooser } = await import("./ReferPatient.jsx");
const { ConfirmProvider } = await import("../components/ConfirmAlert.jsx");

// The catalogue, as the laboratory actually keeps it.
const MALARIA = { id: 1, name: "Malaria Parasite Test", charge_amount: "2500.00",
                  category_label: "Parasitology" };
const FBC = { id: 2, name: "Full Blood Count", charge_amount: "3500.00",
              category_label: "Haematology" };
const PANEL = {
  id: 9, code: "malaria-screen", name: "Malaria screen",
  description: "What the ward orders for a fever.",
  test_details: [MALARIA, FBC],
};

function show(onToggle = () => {}, chosen = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <LabTestChooser chosen={chosen} onToggle={onToggle} />
      </ConfirmProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  get.mockReset();
  get.mockImplementation((url) => Promise.resolve({
    data: url.includes("lab-panels") ? [PANEL] : [MALARIA, FBC],
  }));
});

describe("choosing a laboratory panel", () => {
  let user;
  beforeEach(() => { user = userEvent.setup(); });

  it("asks in a styled alert naming each test and its own price", async () => {
    show();
    await user.click(await screen.findByRole("button", { name: /Malaria screen/ }));

    const dialog = await screen.findByRole("dialog");
    const alert = within(dialog);
    expect(dialog.textContent).toContain("Laboratory tests selected");
    // The catalogue's names and figures, not the page's. Scoped to the dialog,
    // because the catalogue list it was opened from is still behind it.
    expect(alert.getByText("Malaria Parasite Test")).toBeTruthy();
    expect(alert.getByText("₦2,500")).toBeTruthy();
    expect(alert.getByText("Full Blood Count")).toBeTruthy();
    expect(alert.getByText("₦3,500")).toBeTruthy();
    // And what it costs altogether, which is the decision being taken.
    expect(alert.getByText("₦6,000")).toBeTruthy();
    expect(dialog.textContent).toContain("Added to the patient's bill");
  });

  it("adds every test in the panel when confirmed", async () => {
    const onToggle = vi.fn();
    show(onToggle);
    await user.click(await screen.findByRole("button", { name: /Malaria screen/ }));
    await screen.findByRole("dialog");

    await user.click(screen.getByRole("button", { name: /Add 2 tests/ }));

    await waitFor(() => expect(onToggle).toHaveBeenCalledTimes(2));
    expect(onToggle.mock.calls.map(([t]) => t.name))
      .toEqual(["Malaria Parasite Test", "Full Blood Count"]);
  });

  it("adds nothing when cancelled", async () => {
    const onToggle = vi.fn();
    show(onToggle);
    await user.click(await screen.findByRole("button", { name: /Malaria screen/ }));
    await screen.findByRole("dialog");

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("offers only the tests not already chosen, and prices just those", async () => {
    // The existing behaviour: a panel adds what is missing, never a duplicate.
    show(() => {}, [MALARIA]);
    await user.click(await screen.findByRole("button", { name: /Malaria screen/ }));
    const alert = within(await screen.findByRole("dialog"));

    expect(screen.getByRole("button", { name: /Add 1 test$/ })).toBeTruthy();
    expect(alert.getByText("Full Blood Count")).toBeTruthy();
    // The one already chosen is not offered again.
    expect(alert.queryByText("Malaria Parasite Test")).toBeNull();
    // The total is the one test being added, not the whole panel — so the
    // figure appears twice, as that test's price and as the total.
    expect(alert.getAllByText("₦3,500")).toHaveLength(2);
    expect(alert.queryByText("₦6,000")).toBeNull();
  });

  it("says so when a test has no price rather than printing ₦0", async () => {
    get.mockImplementation((url) => Promise.resolve({
      data: url.includes("lab-panels")
        ? [{ ...PANEL, test_details: [{ ...MALARIA, charge_amount: "0.00" }] }]
        : [MALARIA],
    }));
    show();
    await user.click(await screen.findByRole("button", { name: /Malaria screen/ }));
    const alert = within(await screen.findByRole("dialog"));

    expect(alert.getByText("No price set")).toBeTruthy();
  });
});
