/**
 * Reception's counter, billing a patient who arrived with a list.
 *
 * The behaviour this holds is the one the desk actually needed: tick several
 * services, untick one that was a mistake, watch the total move, and submit
 * **once**. What goes to the server is the set of catalogue keys — the prices
 * on this screen are for the person reading them, and
 * `/charges/bill-services/` prices the bill again before it raises anything.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const get = vi.fn();
const post = vi.fn();
vi.mock("../api/client", () => ({ default: { get: (...a) => get(...a), post: (...a) => post(...a) } }));

const Billing = (await import("./Billing.jsx")).default;
const { renderWithApp } = await import("../test/harness.jsx");

const RECEPTION = { id: 2, username: "rec", role: "reception", first_name: "Ngozi" };
const PATIENT = { id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000", first_name: "Ada",
                  last_name: "Obi", patient_number: "NMHS-P000012" };

const LAB = [
  { key: "lab_test:1", id: 1, name: "Full Blood Count", category: "laboratory",
    category_label: "Laboratory", source_type: "laboratory", price: "3500.00", detail: "" },
  { key: "lab_test:2", id: 2, name: "Malaria Parasite Test", category: "laboratory",
    category_label: "Laboratory", source_type: "laboratory", price: "2500.00", detail: "" },
  { key: "lab_test:3", id: 3, name: "Widal Test", category: "laboratory",
    category_label: "Laboratory", source_type: "laboratory", price: "3000.00", detail: "" },
  { key: "lab_test:4", id: 4, name: "Blood Group", category: "laboratory",
    category_label: "Laboratory", source_type: "laboratory", price: "1500.00", detail: "" },
];

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  get.mockImplementation((url, config) => {
    if (url === "/patients/") return Promise.resolve({ data: { results: [PATIENT] } });
    if (url === "/billable-services/") {
      const category = config?.params?.category;
      return Promise.resolve({ data: { results: category === "laboratory" ? LAB : [] } });
    }
    if (url === "/ledgers/") {
      return Promise.resolve({ data: { results: [{ outstanding_balance: "0.00" }] } });
    }
    return Promise.resolve({ data: { results: [] } });
  });
  post.mockImplementation((url) => {
    if (url === "/charges/bill-services/") {
      return Promise.resolve({ data: { count: 3, total: "7500.00",
                                       charges: [{ id: 11 }, { id: 12 }, { id: 13 }] } });
    }
    return Promise.resolve({ data: { id: 99, amount: "7500.00" } });
  });
});

async function openTheLaboratoryTab(user) {
  await user.click(await screen.findByPlaceholderText(/Search by name or file number/));
  await user.click(await screen.findByText(/Obi, Ada/));
  await user.click(await screen.findByRole("button", { name: "Laboratory" }));
}

const tick = (user, name) =>
  screen.findByRole("checkbox", { name: new RegExp(name) }).then((box) => user.click(box));

describe("billing several services in one go", () => {
  it("adds up what is ticked and drops it again when one is unticked", async () => {
    const user = userEvent.setup();
    renderWithApp(<Billing />, { user: RECEPTION });
    await openTheLaboratoryTab(user);

    await tick(user, "Full Blood Count");
    await tick(user, "Malaria Parasite Test");
    await tick(user, "Blood Group");
    expect(await screen.findByText("3 selected · Total ₦7,500")).toBeInTheDocument();

    await tick(user, "Malaria Parasite Test");
    expect(await screen.findByText("2 selected · Total ₦5,000")).toBeInTheDocument();
    // And the ones still ticked are still ticked.
    expect(screen.getByRole("checkbox", { name: /Full Blood Count/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Blood Group/ })).toBeChecked();
  });

  it("offers to settle the whole selection, not the first item", async () => {
    const user = userEvent.setup();
    renderWithApp(<Billing />, { user: RECEPTION });
    await openTheLaboratoryTab(user);
    await tick(user, "Full Blood Count");
    await tick(user, "Widal Test");

    expect(await screen.findByText(/2 services/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Pay in full — ₦6,500/ })).toBeInTheDocument();
  });

  it("bills every ticked service in one submission, by key", async () => {
    const user = userEvent.setup();
    renderWithApp(<Billing />, { user: RECEPTION });
    await openTheLaboratoryTab(user);
    await tick(user, "Full Blood Count");
    await tick(user, "Malaria Parasite Test");
    await tick(user, "Blood Group");

    await user.click(await screen.findByRole("button", { name: /Pay in full/ }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/charges/bill-services/", {
      patient: 3, services: ["lab_test:1", "lab_test:2", "lab_test:4"],
    }));
    // One billing call for the lot — not one per service.
    expect(post.mock.calls.filter(([url]) => url === "/charges/bill-services/")).toHaveLength(1);
    expect(post.mock.calls.some(([url]) => url === "/charges/")).toBe(false);
  });

  it("takes one payment for the total afterwards", async () => {
    const user = userEvent.setup();
    renderWithApp(<Billing />, { user: RECEPTION });
    await openTheLaboratoryTab(user);
    await tick(user, "Full Blood Count");
    await tick(user, "Widal Test");
    await user.click(await screen.findByRole("button", { name: /Pay in full/ }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/payments/",
      expect.objectContaining({ patient: 3, amount: 6500 })));
  });

  it("clears the selection once the bill is raised", async () => {
    const user = userEvent.setup();
    renderWithApp(<Billing />, { user: RECEPTION });
    await openTheLaboratoryTab(user);
    await tick(user, "Full Blood Count");
    await user.click(await screen.findByRole("button", { name: /Pay in full/ }));

    await waitFor(() => expect(screen.getByText(/Nothing selected yet/)).toBeInTheDocument());
  });
});
