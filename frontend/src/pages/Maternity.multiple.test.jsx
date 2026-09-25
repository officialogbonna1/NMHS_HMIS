import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import Maternity from "./Maternity.jsx";

// Multiple births in the workspace.
//
// One pregnancy → one labour → one delivery → **several newborns**. The one
// thing the screen must never do is show the first baby and stop, or merge
// two babies into one set of fields.

const MIDWIFE = { id: 5, role: "maternity_nurse" };
const PATIENT = { id: 3, first_name: "Ada", last_name: "Obi",
                  patient_number: "NMHS-P000021", phone_number: "0803 000 0000" };

const PREGNANCY = {
  id: 7, number: 1, reference: "PRG-000007", is_active: true, gestation: "38w 2d",
  edd: "2026-10-01", gravida: 1, para: 0, encounter_count: 1, last_encounter: null,
};

const baby = (order, overrides) => ({
  id: 100 + order, birth_order: order, reference: `NB-00000${order}`,
  sex: "F", sex_label: "Female", birth_weight_grams: 2400 + order * 50,
  apgar_1_min: 8, apgar_5_min: 9, apgar_10_min: null,
  status_name: "Alive and well", admitted_to_nursery: false,
  congenital_abnormalities: "", name: "", ...overrides,
});

const delivery = (babies) => ({
  id: 9, reference: "DEL-000009", delivered_at: "2026-09-20T11:00:00Z",
  delivery_type_name: "Normal vaginal delivery", outcome_name: "Live birth",
  doctor_name: "Femi Okon", midwife_name: "Grace Nwosu",
  estimated_blood_loss_ml: 400, placenta_complete: true,
  complication_names: [], newborns: babies, postpartum_visits: [],
});

const LABOUR = {
  id: 4, status: "delivered", status_label: "Delivered", stage_label: "Third stage",
  onset_label: "Spontaneous", started_at: "2026-09-20T06:00:00Z",
  ended_at: "2026-09-20T11:00:00Z", ward_name: "Maternity Ward", bed_number: "M03",
  admission: 12, observation_count: 2, has_delivery: true, pregnancy_number: 1,
  patient_name: "Obi, Ada", patient_number: "NMHS-P000021", gestation: "38w 2d",
};

function mockApi(babies) {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/maternity/lookup/") {
      return Promise.resolve({ data: {
        known: true, patient: { id: 3, name: "Obi, Ada", patient_number: "NMHS-P000021" },
        active_pregnancy: PREGNANCY, history: [PREGNANCY],
        can_continue: true, can_start: false, has_history: true, visit_types: [],
      } });
    }
    // The maternity picker reads the ward's own list, which the server
    // scopes — never the generic patient endpoint.
    if (url === "/maternity/patients/") {
      return Promise.resolve({ data: { results: [{ ...PATIENT, name: "Obi, Ada" }] } });
    }
    if (url === "/labour-episodes/") return Promise.resolve({ data: [LABOUR] });
    if (url.startsWith("/labour-episodes/")) {
      return Promise.resolve({ data: { ...LABOUR, observations: [],
                                       delivery: delivery(babies) } });
    }
    if (url.startsWith("/pregnancies/")) {
      return Promise.resolve({ data: { ...PREGNANCY, encounters: [] } });
    }
    return Promise.resolve({ data: [] });
  });
}

async function openTab(user, label) {
  await user.click(await screen.findByLabelText(/Search maternity patients/i));
  await user.click(await screen.findByRole("option", { name: /Obi, Ada/ }));
  await user.click(await screen.findByRole("button", { name: new RegExp(`^${label}`) }));
}

afterEach(() => vi.restoreAllMocks());

describe("twins in the workspace", () => {
  const twins = [baby(1, { sex: "M", sex_label: "Male" }), baby(2)];

  it("shows every baby, not only the first", async () => {
    const user = userEvent.setup();
    mockApi(twins);
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openTab(user, "Newborns");

    expect(await screen.findByText(/Baby 1 — NB-000001/)).toBeInTheDocument();
    expect(screen.getByText(/Baby 2 — NB-000002/)).toBeInTheDocument();
  });

  it("counts them on the tab and above the cards", async () => {
    const user = userEvent.setup();
    mockApi(twins);
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openTab(user, "Newborns");

    expect(await screen.findByText(/Newborns \(2\) · Twins/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Newborns\s*\(2\)/ })).toBeInTheDocument();
  });

  it("keeps each baby's own weight, Apgar and sex — never merged", async () => {
    const user = userEvent.setup();
    mockApi(twins);
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openTab(user, "Newborns");

    const first = (await screen.findByText(/Baby 1 — NB-000001/)).closest("section");
    const second = screen.getByText(/Baby 2 — NB-000002/).closest("section");
    expect(within(first).getByText("Male")).toBeInTheDocument();
    expect(within(first).getByText("2450 g")).toBeInTheDocument();
    expect(within(second).getByText("Female")).toBeInTheDocument();
    expect(within(second).getByText("2500 g")).toBeInTheDocument();
  });

  it("gives each baby its own print action", async () => {
    const user = userEvent.setup();
    mockApi(twins);
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openTab(user, "Newborns");

    await screen.findByText(/Baby 1 — NB-000001/);
    // One per card — a delivery of twins must not have to guess which you meant.
    expect(screen.getAllByRole("button", { name: /Birth record/ })).toHaveLength(2);
  });

  it("states the delivery → newborn relationship on the Deliveries tab", async () => {
    const user = userEvent.setup();
    mockApi(twins);
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openTab(user, "Deliveries");

    expect(await screen.findByText("Delivery DEL-000009")).toBeInTheDocument();
    expect(screen.getByText("2 newborns")).toBeInTheDocument();
    // And both babies are listed under it.
    expect(screen.getByText(/Baby 1 — NB-000001/)).toBeInTheDocument();
    expect(screen.getByText(/Baby 2 — NB-000002/)).toBeInTheDocument();
  });
});

describe("triplets and singletons", () => {
  it("shows three babies for triplets", async () => {
    const user = userEvent.setup();
    mockApi([baby(1), baby(2), baby(3)]);
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openTab(user, "Newborns");

    expect(await screen.findByText(/Newborns \(3\) · Triplets/)).toBeInTheDocument();
    for (const order of [1, 2, 3]) {
      expect(screen.getByText(new RegExp(`Baby ${order} — NB-00000${order}`)))
        .toBeInTheDocument();
    }
  });

  it("reads naturally for one baby", async () => {
    const user = userEvent.setup();
    mockApi([baby(1)]);
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openTab(user, "Deliveries");

    expect(await screen.findByText("1 newborn")).toBeInTheDocument();
  });

  it("keeps a different status per baby", async () => {
    const user = userEvent.setup();
    mockApi([baby(1), baby(2, { status_name: "Alive — needs special care" })]);
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openTab(user, "Newborns");

    await screen.findByText(/Baby 2 — NB-000002/);
    expect(screen.getByText("Alive and well")).toBeInTheDocument();
    expect(screen.getByText("Alive — needs special care")).toBeInTheDocument();
  });

  it("says so when a delivery has no baby against it", async () => {
    const user = userEvent.setup();
    mockApi([]);
    renderWithApp(<Maternity />, { user: MIDWIFE });
    await openTab(user, "Newborns");

    expect(await screen.findByText("No baby recorded")).toBeInTheDocument();
  });
});
