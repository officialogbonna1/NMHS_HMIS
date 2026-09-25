import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { PATIENT, renderWithApp } from "../test/harness.jsx";
import PatientQueue from "./PatientQueue.jsx";

// Reception's own routing form — the existing one, not a second page. Sending
// a patient to Ophthalmology is `purpose: "eye"` on a `PatientRoute`, which is
// what decides who is notified and whose queue it lands in
// (`workflow.views.PURPOSE_ROLE`). The department it is raised against is
// paired with it so the two cannot drift apart at the desk.

const RECEPTION = { id: 1, role: "reception", username: "front" };

const DEPARTMENTS = [
  { id: 10, code: "clinicals", name: "Clinicals (Nursing)", is_active: true },
  { id: 11, code: "consultation", name: "Consultation", is_active: true },
  { id: 12, code: "eye", name: "Eye Clinic", is_active: true },
  { id: 13, code: "laboratory", name: "Laboratory", is_active: true },
  { id: 15, code: "maternity", name: "Maternity", is_active: true },
  { id: 14, code: "retired-unit", name: "Old Annexe", is_active: false },
];

const EYE_STAFF = [
  { id: 21, first_name: "John", last_name: "Doe", role: "ophthalmologist" },
  { id: 22, first_name: "Mary", last_name: "Smith", role: "optometrist" },
];
const NURSES = [{ id: 31, first_name: "Ada", last_name: "Bello", role: "nurse" }];
const MIDWIVES = [
  { id: 41, first_name: "Grace", last_name: "Nwosu", role: "maternity_nurse" },
  { id: 42, first_name: "Ada", last_name: "Okafor", role: "maternity_nurse" },
];

beforeEach(() => {
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url === "/departments/") return Promise.resolve({ data: DEPARTMENTS });
    if (url === "/patients/") return Promise.resolve({ data: [PATIENT] });
    if (url === "/users/") {
      const role = config?.params?.role;
      const byRole = { nurse: NURSES, maternity_nurse: MIDWIVES };
      const data = byRole[role] ?? EYE_STAFF.filter((u) => u.role === role);
      return Promise.resolve({ data });
    }
    return Promise.resolve({ data: [] });
  });
});
afterEach(() => vi.restoreAllMocks());

const render = () => renderWithApp(<PatientQueue />, { user: RECEPTION });
const sendFor = () => screen.getByLabelText("Send for");

// The one patient picker, driven the way a receptionist drives it.
async function pickPatient(user) {
  await waitFor(() => expect(sendFor()).toBeInTheDocument());
  // The inline picker: a combobox whose caret opens the list it filters.
  await user.click(screen.getByRole("button", { name: /open patient list/i }));
  await user.click(await screen.findByRole("button", { name: /Obi, Ada/ }));
}
const department = () => screen.getByLabelText(/Route to department/);

describe("the front desk routing a patient to Ophthalmology", () => {
  it("offers Ophthalmology as somewhere to send the patient", async () => {
    render();
    await waitFor(() => expect(sendFor()).toBeInTheDocument());
    expect([...sendFor().options].map((o) => o.textContent))
      .toContain("Ophthalmology / Eye clinic");
  });

  it("pairs the department with what the patient is being sent for, either way round", async () => {
    const user = userEvent.setup();
    render();
    await waitFor(() => expect(department().options.length).toBeGreaterThan(1));

    await user.selectOptions(sendFor(), "eye");
    expect(department()).toHaveValue("12");

    // Nursing has a department of its own, so a vitals route is filed where
    // the patient actually goes instead of against whichever unit was least
    // wrong. It is not a revenue department — a nurse raises no charge.
    await user.selectOptions(sendFor(), "vitals");
    expect(department()).toHaveValue("10");

    // And back: choosing the Consultation department sets the purpose with it.
    await user.selectOptions(department(), "11");
    expect(sendFor()).toHaveValue("consultation");

    // A department whose work is not on this form leaves the purpose alone —
    // reception picking "Laboratory" does not become a lab order.
    await user.selectOptions(department(), "13");
    expect(sendFor()).toHaveValue("consultation");
  });

  it("offers only eye clinicians for Ophthalmology, named by their role", async () => {
    const user = userEvent.setup();
    render();
    await waitFor(() => expect(sendFor()).toBeInTheDocument());
    await user.selectOptions(sendFor(), "eye");

    const assign = await screen.findByLabelText(/Assign to eye clinician/);
    await waitFor(() => expect(assign.options.length).toBe(3));
    expect([...assign.options].map((o) => o.textContent.trim())).toEqual([
      "Anyone in the eye clinic", "John Doe — Ophthalmologist", "Mary Smith — Optometrist",
    ]);
    // A nurse is not on that list — the server refuses one either way.
    expect(screen.queryByText(/Ada Bello/)).not.toBeInTheDocument();
  });

  it("offers the nursing department for vitals, named for the desk", async () => {
    render();
    await waitFor(() => expect(department().options.length).toBeGreaterThan(1));
    expect([...department().options].map((o) => o.textContent))
      .toContain("Clinicals (Nursing)");
  });

  it("never offers a department that has been retired", async () => {
    render();
    await waitFor(() => expect(department().options.length).toBeGreaterThan(1));
    expect([...department().options].map((o) => o.textContent)).not.toContain("Old Annexe");
  });

  it("opens the visit and routes it to Ophthalmology in the patient's name", async () => {
    const user = userEvent.setup();
    const post = vi.spyOn(api, "post").mockImplementation((url) =>
      Promise.resolve({ data: url === "/visits/" ? { id: 77 } : { id: 88 } }));
    render();
    await pickPatient(user);
    await user.selectOptions(sendFor(), "eye");
    await user.selectOptions(await screen.findByLabelText(/Assign to eye clinician/), "21");
    await user.selectOptions(screen.getByLabelText("Priority"), "urgent");
    await user.click(screen.getByRole("button", { name: "Route patient" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/patient-routes/", expect.objectContaining({
      visit: 77, department: 12, purpose: "eye", assigned_to: 21, priority: "urgent",
    })));
    // The visit is the existing workflow's, opened on the patient chosen here.
    expect(post).toHaveBeenCalledWith("/visits/", expect.objectContaining({ patient: PATIENT.id }));
  });

  it("says what the server objected to rather than 'check the fields'", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post").mockImplementation((url) => (
      url === "/visits/"
        ? Promise.resolve({ data: { id: 77 } })
        : Promise.reject({ response: {
            data: { assigned_to: ["Ada Bello cannot take Eye clinic work."] } } })));
    render();
    await pickPatient(user);
    await user.selectOptions(sendFor(), "eye");
    await user.click(screen.getByRole("button", { name: "Route patient" }));

    expect(await screen.findByText(/Ada Bello cannot take Eye clinic work\./))
      .toBeInTheDocument();
  });
});


// **Putting a mother in Maternity's care is this form**, not a maternity
// assignment screen of its own. The department is what the ward sees her by;
// naming a midwife is optional and narrows nothing.
describe("the front desk routing a patient to Maternity", () => {
  it("offers Maternity as somewhere to send the patient", async () => {
    render();
    await waitFor(() => expect(sendFor()).toBeInTheDocument());
    expect([...sendFor().options].map((o) => o.textContent)).toContain("Maternity");
  });

  it("pairs it with the Maternity department, either way round", async () => {
    const user = userEvent.setup();
    render();
    await waitFor(() => expect(department().options.length).toBeGreaterThan(1));

    await user.selectOptions(sendFor(), "maternity");
    expect(department()).toHaveValue("15");

    await user.selectOptions(sendFor(), "vitals");
    await user.selectOptions(department(), "15");
    expect(sendFor()).toHaveValue("maternity");
  });

  it("offers midwives and nobody else", async () => {
    const user = userEvent.setup();
    render();
    await waitFor(() => expect(sendFor()).toBeInTheDocument());
    await user.selectOptions(sendFor(), "maternity");

    const assign = await screen.findByLabelText(/Assign to maternity nurse/);
    await waitFor(() => expect(assign.options.length).toBe(3));
    expect([...assign.options].map((o) => o.textContent.trim())).toEqual([
      "The maternity ward — every midwife sees her",
      "Grace Nwosu — Maternity",
      "Ada Okafor — Maternity",
    ]);
  });

  it("says that naming nobody still puts her in front of the whole ward", async () => {
    const user = userEvent.setup();
    render();
    await waitFor(() => expect(sendFor()).toBeInTheDocument());
    await user.selectOptions(sendFor(), "maternity");

    const assign = await screen.findByLabelText(/Assign to maternity nurse/);
    expect(assign).toHaveValue("");
    expect(within(assign).getByText("The maternity ward — every midwife sees her"))
      .toBeInTheDocument();
  });
});
