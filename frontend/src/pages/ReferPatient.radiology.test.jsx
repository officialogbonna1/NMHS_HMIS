/**
 * The doctor's radiology referral, end to end on the screen.
 *
 * The laboratory's shape, reused: refer the patient, name the examinations
 * from the configured catalogue, and the charges are raised with the order —
 * two calls, deliberately, so a catalogue hiccup cannot swallow a hand-off
 * that has already been made.
 *
 * What these hold is that the examinations offered are the **catalogue's**
 * (nothing about them is written into the page), and that choosing them is
 * what the referral actually sends on.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const get = vi.fn();
const post = vi.fn();
vi.mock("../api/client", () => ({ default: { get: (...a) => get(...a), post: (...a) => post(...a) } }));

const ReferPatient = (await import("./ReferPatient.jsx")).default;
const { renderWithApp } = await import("../test/harness.jsx");

const DOCTOR = { id: 4, username: "doc", role: "doctor", first_name: "Chidi", last_name: "Nwosu" };
const PATIENT = { id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000", first_name: "Ada",
                  last_name: "Obi", patient_number: "NMHS-P000012" };
const SCANS = [
  { key: "billing_item:11", id: 11, name: "Abdominal Ultrasound", category: "ultrasound",
    category_label: "Ultrasound / Imaging", source_type: "ultrasound", price: "8000.00", detail: "" },
  { key: "billing_item:12", id: 12, name: "Obstetric Ultrasound (Dating)", category: "ultrasound",
    category_label: "Ultrasound / Imaging", source_type: "ultrasound", price: "8000.00", detail: "" },
];

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  get.mockImplementation((url) => {
    if (url === "/patients/") return Promise.resolve({ data: { results: [PATIENT] } });
    if (url === "/billable-services/") return Promise.resolve({ data: { results: SCANS } });
    return Promise.resolve({ data: { results: [] } });
  });
  post.mockImplementation((url) => {
    if (url === "/patient-routes/refer/") {
      return Promise.resolve({ data: { id: 55, purpose: "ultrasound", notified: ["Bisi Ade"] } });
    }
    return Promise.resolve({ data: {} });
  });
});

async function pickThePatient(user) {
  await user.click(screen.getByPlaceholderText(/Search or pick a patient/));
  await user.click(await screen.findByText(/Obi, Ada/));
}

describe("referring a patient for a scan", () => {
  it("offers Radiology / Ultrasound as a destination", async () => {
    renderWithApp(<ReferPatient />, { user: DOCTOR });
    expect(await screen.findByText("Radiology / Ultrasound")).toBeInTheDocument();
  });

  it("offers the configured examinations once that destination is chosen", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReferPatient />, { user: DOCTOR });
    await pickThePatient(user);
    await user.click(screen.getByText("Radiology / Ultrasound"));

    expect(await screen.findByText(/Abdominal Ultrasound/)).toBeInTheDocument();
    expect(await screen.findByText(/Obstetric Ultrasound \(Dating\)/)).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/billable-services/",
      expect.objectContaining({ params: expect.objectContaining({ category: "ultrasound" }) }));
  });

  it("sends the referral, then puts the chosen examinations on it", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReferPatient />, { user: DOCTOR });
    await pickThePatient(user);
    await user.click(screen.getByText("Radiology / Ultrasound"));
    await user.click(await screen.findByText(/Abdominal Ultrasound/));
    await user.click(screen.getByRole("button", { name: /Send referral/ }));

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/patient-routes/55/request-services/", { services: [11] }));
    const referral = post.mock.calls.find(([url]) => url === "/patient-routes/refer/");
    expect(referral[1]).toMatchObject({ patient: 3, purpose: "ultrasound" });
    // The referral goes first: the hand-off must survive a catalogue failure.
    expect(post.mock.calls[0][0]).toBe("/patient-routes/refer/");
  });

  it("says the charge is raised with the order once an examination is chosen", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReferPatient />, { user: DOCTOR });
    await pickThePatient(user);
    await user.click(screen.getByText("Radiology / Ultrasound"));
    expect(await screen.findByText(/The counter bills the service separately/)).toBeInTheDocument();

    await user.click(await screen.findByText(/Obstetric Ultrasound \(Dating\)/));
    expect(await screen.findByText(/The charge is raised with the order/)).toBeInTheDocument();
  });

  it("still refers with no examination named — the unit can add it", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReferPatient />, { user: DOCTOR });
    await pickThePatient(user);
    await user.click(screen.getByText("Radiology / Ultrasound"));
    await user.click(screen.getByRole("button", { name: /Send referral/ }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/patient-routes/refer/",
      expect.objectContaining({ purpose: "ultrasound" })));
    expect(post.mock.calls.some(([url]) => url.includes("request-services"))).toBe(false);
  });
});
