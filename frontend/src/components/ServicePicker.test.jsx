/**
 * The picker the desk bills from, and the chooser a clinician orders from.
 *
 * The bug this replaces, seen from the screen: Reception's Laboratory tab
 * showed **one** test. Both controls now read one endpoint
 * (`/billable-services/`), so what a doctor can order and what the desk can
 * bill are the same rows at the same prices — and a catalogue of sixty-six is
 * usable: searchable, scrollable, nothing cut off.
 */
import { useState } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const get = vi.fn();
vi.mock("../api/client", () => ({ default: { get: (...a) => get(...a) } }));

const { ServicePicker, ServiceChooser } = await import("./ServicePicker.jsx");
const { toggle } = await import("./billableServices");
const { renderWithApp } = await import("../test/harness.jsx");

// The laboratory as a hospital actually keeps it — more than one test.
const LAB = [
  { key: "lab_test:1", id: 1, name: "Full Blood Count (FBC)", category: "laboratory",
    category_label: "Laboratory", source_type: "laboratory", price: "3500.00",
    detail: "Haematology · Whole blood" },
  { key: "lab_test:2", id: 2, name: "Malaria Parasite (MP)", category: "laboratory",
    category_label: "Laboratory", source_type: "laboratory", price: "1500.00",
    detail: "Parasitology · Whole blood" },
  { key: "lab_test:3", id: 3, name: "Renal Function Test (RFT)", category: "laboratory",
    category_label: "Laboratory", source_type: "laboratory", price: "6000.00",
    detail: "Chemical Pathology · Serum" },
  { key: "billing_item:7", id: 7, name: "Hepatitis B Screen", category: "laboratory",
    category_label: "Laboratory", source_type: "laboratory", price: "2000.00", detail: "" },
];

const SCANS = [
  { key: "billing_item:11", id: 11, name: "Abdominal Ultrasound", category: "ultrasound",
    category_label: "Ultrasound / Imaging", source_type: "ultrasound", price: "8000.00",
    detail: "" },
  { key: "billing_item:12", id: 12, name: "Obstetric Ultrasound (Dating)", category: "ultrasound",
    category_label: "Ultrasound / Imaging", source_type: "ultrasound", price: "8000.00",
    detail: "" },
  { key: "billing_item:13", id: 13, name: "Doppler Study (Obstetric)", category: "ultrasound",
    category_label: "Ultrasound / Imaging", source_type: "ultrasound", price: "15000.00",
    detail: "" },
];

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The picker with no selection — the cases that are about the list itself. */
function Picker({ category = "laboratory" } = {}) {
  return <ServicePicker category={category} selected={[]} onToggle={() => {}} />;
}

/**
 * The picker wired to state the way a billing screen wires it, so ticking and
 * unticking behave here exactly as they do on the counter. A small stateful
 * wrapper rather than a manual re-render: the selection has to survive the
 * component's own updates, which is the thing being tested.
 */
let selection = [];
function Selectable({ category = "laboratory" }) {
  const [selected, setSelected] = useState([]);
  selection = selected;
  return (
    <ServicePicker
      category={category}
      selected={selected}
      onToggle={(service) => setSelected((current) => toggle(current, service))}
      onClear={() => setSelected([])}
    />
  );
}

function renderSelectable(props = {}) {
  selection = [];
  renderWithApp(<Selectable {...props} />);
  return { current: () => selection };
}

beforeEach(() => {
  get.mockReset();
  get.mockImplementation((url, config) => {
    const category = config?.params?.category;
    const rows = category === "ultrasound" ? SCANS : LAB;
    const term = (config?.params?.search ?? "").toLowerCase();
    const results = term ? rows.filter((r) => r.name.toLowerCase().includes(term)) : rows;
    return Promise.resolve({ data: { results } });
  });
});

describe("Reception picking the services to bill", () => {
  it("offers every configured laboratory service, not one", async () => {
    renderWithApp(<Picker />);
    for (const service of LAB) {
      expect(await screen.findByRole("checkbox", { name: new RegExp(escape(service.name)) }))
        .toBeInTheDocument();
    }
    expect(await screen.findByText(/4 of 4 services/)).toBeInTheDocument();
  });

  it("reads the list from the shared endpoint, never the price list alone", async () => {
    renderWithApp(<Picker />);
    await screen.findByText("Full Blood Count (FBC)");
    expect(get.mock.calls[0][0]).toBe("/billable-services/");
  });

  it("shows each service's own configured price", async () => {
    renderWithApp(<Picker />);
    expect(await screen.findByText("₦3,500")).toBeInTheDocument();
    expect(await screen.findByText("₦6,000")).toBeInTheDocument();
  });

  it("filters a long catalogue as you type", async () => {
    const user = userEvent.setup();
    renderWithApp(<Picker />);
    await screen.findByText("Malaria Parasite (MP)");

    await user.type(screen.getByLabelText("Search services"), "malaria");
    await waitFor(() => expect(screen.queryByText("Full Blood Count (FBC)")).not.toBeInTheDocument());
    expect(screen.getByText("Malaria Parasite (MP)")).toBeInTheDocument();
  });

  it("says so when a category really has nothing priced", async () => {
    get.mockResolvedValue({ data: { results: [] } });
    renderWithApp(<Picker category="procedure" />);
    expect(await screen.findByText(/Nothing priced under this yet/)).toBeInTheDocument();
  });
});

describe("selecting, unselecting and the running total", () => {
  it("ticks one service and says what it comes to", async () => {
    const user = userEvent.setup();
    renderSelectable();
    await user.click(await screen.findByRole("checkbox", { name: /Full Blood Count/ }));
    expect(await screen.findByText("1 selected · Total ₦3,500")).toBeInTheDocument();
  });

  it("ticks several and totals them", async () => {
    const user = userEvent.setup();
    renderSelectable();
    for (const name of [/Full Blood Count/, /Malaria Parasite/, /Hepatitis B/]) {
      await user.click(await screen.findByRole("checkbox", { name }));
    }
    expect(await screen.findByText("3 selected · Total ₦7,000")).toBeInTheDocument();
  });

  it("unticks one and the total drops immediately, keeping the rest", async () => {
    const user = userEvent.setup();
    const { current } = renderSelectable();
    for (const name of [/Full Blood Count/, /Malaria Parasite/, /Hepatitis B/]) {
      await user.click(await screen.findByRole("checkbox", { name }));
    }
    await user.click(screen.getByRole("checkbox", { name: /Malaria Parasite/ }));

    expect(await screen.findByText("2 selected · Total ₦5,500")).toBeInTheDocument();
    expect(current().map((s) => s.name))
      .toEqual(["Full Blood Count (FBC)", "Hepatitis B Screen"]);
    // The others are still ticked — nobody starts the selection again.
    expect(screen.getByRole("checkbox", { name: /Full Blood Count/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Malaria Parasite/ })).not.toBeChecked();
  });

  it("survives ticking and unticking the same service repeatedly", async () => {
    const user = userEvent.setup();
    const { current } = renderSelectable();
    const box = () => screen.getByRole("checkbox", { name: /Renal Function/ });
    await screen.findByText("Renal Function Test (RFT)");
    for (let i = 0; i < 3; i += 1) {
      await user.click(box());
      await user.click(box());
    }
    expect(current()).toEqual([]);
    await user.click(box());
    expect(current().map((s) => s.key)).toEqual(["lab_test:3"]);
  });

  it("removes one from the selected chips without touching the list", async () => {
    const user = userEvent.setup();
    const { current } = renderSelectable();
    await user.click(await screen.findByRole("checkbox", { name: /Full Blood Count/ }));
    await user.click(await screen.findByRole("checkbox", { name: /Malaria Parasite/ }));

    await user.click(screen.getByRole("button", { name: "Remove Malaria Parasite (MP)" }));
    expect(current().map((s) => s.key)).toEqual(["lab_test:1"]);
    expect(await screen.findByText("1 selected · Total ₦3,500")).toBeInTheDocument();
  });

  it("clears the whole selection in one press", async () => {
    const user = userEvent.setup();
    const { current } = renderSelectable();
    await user.click(await screen.findByRole("checkbox", { name: /Full Blood Count/ }));
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(current()).toEqual([]);
    expect(screen.getByText(/Nothing selected yet/)).toBeInTheDocument();
  });

  it("keeps a selection made before the list was searched", async () => {
    const user = userEvent.setup();
    const { current } = renderSelectable();
    await user.click(await screen.findByRole("checkbox", { name: /Full Blood Count/ }));
    await user.type(screen.getByLabelText("Search services"), "malaria");
    await waitFor(() => expect(screen.queryByRole("checkbox", { name: /Full Blood Count/ }))
      .not.toBeInTheDocument());
    // Off the visible list, still on the bill.
    expect(current().map((s) => s.key)).toEqual(["lab_test:1"]);
    expect(screen.getByText("1 selected · Total ₦3,500")).toBeInTheDocument();
  });
});

describe("a clinician ordering examinations", () => {
  it("offers the configured ultrasound examinations with their prices", async () => {
    renderWithApp(
      <ServiceChooser category="ultrasound" chosen={[]} onToggle={() => {}}
                      title="Which examination?" blurb="Pick what you want performed." />,
    );
    for (const scan of SCANS) {
      expect(await screen.findByText(new RegExp(scan.name.replace(/[()]/g, "\\$&")))).toBeInTheDocument();
    }
  });

  it("reads the same endpoint Reception bills from", async () => {
    renderWithApp(
      <ServiceChooser category="ultrasound" chosen={[]} onToggle={() => {}}
                      title="Which examination?" blurb="" />,
    );
    await screen.findByText(/Abdominal Ultrasound/);
    expect(get.mock.calls[0][0]).toBe("/billable-services/");
    expect(get.mock.calls[0][1].params.category).toBe("ultrasound");
  });

  it("totals what the chosen examinations will add to the bill", async () => {
    renderWithApp(
      <ServiceChooser category="ultrasound" chosen={[SCANS[0], SCANS[2]]} onToggle={() => {}}
                      title="Which examination?" blurb="" />,
    );
    expect(await screen.findByText(/2 examinations · ₦23,000 will be added/)).toBeInTheDocument();
  });

  it("toggles one on and off", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    renderWithApp(
      <ServiceChooser category="ultrasound" chosen={[]} onToggle={onToggle}
                      title="Which examination?" blurb="" />,
    );
    await user.click(await screen.findByText(/Doppler Study/));
    expect(onToggle).toHaveBeenCalledWith(expect.objectContaining({ key: "billing_item:13" }));
  });
});
