import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import Appointments from "./Appointments.jsx";

// Reception's booking workspace: patient → department → service → provider →
// reason → summary → queue. Progressive, so nothing irrelevant is on screen,
// and every list comes from the server's one read.

const RECEPTION = { id: 2, role: "reception" };

const DOCTOR = { id: 2, name: "Dara Ade", role: "doctor", role_label: "Doctor" };

const OPTIONS = {
  general: { department: 2, department_name: "Consultation",
             label: "General consultation", providers: [DOCTOR] },
  departments: [
    {
      id: 2, code: "consultation", name: "Consultation",
      services: [
        { key: "billing_item:2", id: 2, name: "Doctor Consultation", fee: "5000.00",
          billable: true, category: "consultation", providers: [DOCTOR] },
      ],
    },
    {
      id: 3, code: "eye", name: "Eye Clinic",
      services: [
        { key: "billing_item:5", id: 5, name: "Eye Consultation", fee: "5000.00",
          billable: true, category: "eye",
          providers: [{ id: 9, name: "Femi Bello", role: "ophthalmologist",
                        role_label: "Ophthalmologist / Eye Doctor" }] },
        { key: "billing_item:6", id: 6, name: "Eye Follow-up", fee: "0.00",
          billable: false, category: "eye",
          providers: [{ id: 9, name: "Femi Bello", role: "ophthalmologist",
                        role_label: "Ophthalmologist / Eye Doctor" }] },
      ],
    },
    {
      id: 4, code: "radiology", name: "Radiology / Ultrasound",
      services: [
        { key: "billing_item:7", id: 7, name: "Abdominal Ultrasound", fee: "10000.00",
          billable: true, category: "ultrasound",
          providers: [{ id: 12, name: "Bisi Ade", role: "radiology",
                        role_label: "Radiology Staff" }] },
        { key: "billing_item:8", id: 8, name: "Renal Ultrasound", fee: "8000.00",
          billable: true, category: "ultrasound",
          providers: [{ id: 12, name: "Bisi Ade", role: "radiology",
                        role_label: "Radiology Staff" }] },
        { key: "billing_item:9", id: 9, name: "Pelvic Ultrasound", fee: "6000.00",
          billable: true, category: "ultrasound",
          providers: [{ id: 12, name: "Bisi Ade", role: "radiology",
                        role_label: "Radiology Staff" }] },
      ],
    },
    { id: 5, code: "pharmacy", name: "Pharmacy", services: [] },
  ],
};

const PATIENT = {
  id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000", first_name: "John", last_name: "Doe",
  patient_number: "NMHS-P000012", phone_number: "0803 111 2222",
};

const APPOINTMENT = {
  id: 1, patient: 3, patient_name: "Doe, John", patient_number: "NMHS-P000012",
  doctor: 9, provider: 9, provider_name: "Femi Bello", doctor_name: "Femi Bello",
  department: 3, department_name: "Eye Clinic", service: 5, service_name: "Eye Consultation",
  service_fee: "5000.00", charge: 41, status: "queued", reason: "Blurred vision",
  created_at: "2026-09-15T08:00:00Z", start_time: null, end_time: null,
  billing: { billed: true, status: "unpaid", label: "UNPAID", requires_payment: true,
             amount: "5000.00", paid: "0.00", outstanding: "5000.00" },
};

function mockApi({ appointments = [], options = OPTIONS } = {}) {
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url === "/appointments/booking-options/") return Promise.resolve({ data: options });
    if (url === "/appointments/") return Promise.resolve({ data: appointments });
    if (url === "/patients/") return Promise.resolve({ data: { results: [PATIENT] } });
    return Promise.resolve({ data: [] });
  });
}

/** The booking form, as its own landmark — the queue below has filters with
 *  names of its own, and a bare "Department" would match both. */
const form = () => within(screen.getByRole("region", { name: "Queue an appointment" }));

/** Pick the patient through the one picker the application renders. */
async function choosePatient(user) {
  // The inline picker opens its list on click and renders each patient as a
  // button (the dropdown variant is the one with listbox roles).
  await user.click(await screen.findByPlaceholderText(/Search by name/i));
  await user.click(await screen.findByRole("button", { name: /Doe, John/ }));
}

afterEach(() => vi.restoreAllMocks());

describe("the booking form, step by step", () => {
  it("shows nothing but the patient step until a patient is chosen", async () => {
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });

    await screen.findByText("Queue an appointment");
    expect(form().queryByLabelText("Department")).not.toBeInTheDocument();
    expect(form().queryByRole("checkbox")).not.toBeInTheDocument();
    expect(form().queryByLabelText("Provider")).not.toBeInTheDocument();
  });

  it("offers the department once a patient is chosen, and not the service yet", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);

    expect(await form().findByLabelText("Department")).toBeInTheDocument();
    expect(form().queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("confirms the patient that was picked", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);

    // The picker shows the number too, so the confirmation block is
    // identified by what only it says: the phone and whether they have been
    // here before.
    expect(await screen.findByText("First appointment")).toBeInTheDocument();
    expect(screen.getByText("0803 111 2222", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByText("NMHS-P000012").length).toBeGreaterThan(0);
  });

  it("filters the services to the chosen department", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "3");

    expect(await form().findByRole("checkbox", { name: /Eye Consultation/ }))
      .toBeInTheDocument();
    expect(form().queryByRole("checkbox", { name: /Ultrasound/ })).not.toBeInTheDocument();
  });

  it("filters the providers to the ticked services", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "4");
    await user.click(await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ }));

    const providers = await form().findByLabelText("Provider");
    expect(within(providers).getByRole("option", { name: /Bisi Ade/ })).toBeInTheDocument();
    expect(within(providers).queryByRole("option", { name: /Femi Bello/ })).not.toBeInTheDocument();
  });

  it("shows the fee from the server and never offers to take payment", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "3");
    await user.click(await form().findByRole("checkbox", { name: /Eye Consultation/ }));

    expect(await screen.findByText("Appointment fee")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pay/i })).not.toBeInTheDocument();
    // And no box to type a price into — the amount is the catalogue's.
    expect(screen.queryByPlaceholderText(/consultation fee/i)).not.toBeInTheDocument();
  });

  it("says 'No charge' for a service with no price", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "3");
    await user.click(await form().findByRole("checkbox", { name: /Eye Follow-up/ }));

    expect(await screen.findByText("Appointment fee")).toBeInTheDocument();
    expect(screen.getAllByText("No charge").length).toBeGreaterThan(0);
  });

  it("summarises the booking before it is queued", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "3");
    await user.click(await form().findByRole("checkbox", { name: /Eye Consultation/ }));
    await user.selectOptions(await form().findByLabelText("Provider"), "9");

    const summary = (await screen.findByText("Appointment summary")).closest("div");
    expect(within(summary).getByText("Eye Clinic")).toBeInTheDocument();
    expect(within(summary).getByText("Eye Consultation")).toBeInTheDocument();
    expect(within(summary).getByText("Femi Bello")).toBeInTheDocument();
    expect(within(summary).getByText("Existing billing workflow")).toBeInTheDocument();
  });

  it("submits identities only — never a price", async () => {
    const user = userEvent.setup();
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: APPOINTMENT });
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "3");
    await user.click(await form().findByRole("checkbox", { name: /Eye Consultation/ }));
    await user.selectOptions(await form().findByLabelText("Provider"), "9");
    await user.click(screen.getByRole("button", { name: "Queue appointment" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/appointments/", {
      patient: 3, doctor: 9, reason: "", service: "billing_item:5",
    }));
  });

  it("tells reception when nothing has been configured", async () => {
    mockApi({ options: { departments: [] } });
    renderWithApp(<Appointments />, { user: RECEPTION });

    expect(await screen.findByText(/No department is open for appointments/))
      .toBeInTheDocument();
  });

  it("tells reception when a service has no eligible provider", async () => {
    const user = userEvent.setup();
    const eye = OPTIONS.departments.find((d) => d.code === "eye");
    mockApi({ options: { general: OPTIONS.general,
                         departments: [{ ...eye,
                           services: [{ ...eye.services[0], providers: [] }] }] } });
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "3");
    await user.click(await form().findByRole("checkbox", { name: /Eye Consultation/ }));

    expect(await screen.findByText(/No eligible provider is available/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Queue appointment" })).toBeDisabled();
  });

  it("tells reception when a department has nothing configured", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "5");

    expect(await screen.findByText(/Nothing bookable in this department/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Queue appointment" })).toBeDisabled();
  });

  it("offers exactly the departments the server sends, and no others", async () => {
    // Which departments take appointments is `Department.is_appointment_available`,
    // decided in Django admin and applied by the server. The page holds no
    // list of its own: it renders what `/booking-options/` returned.
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);

    const departments = await form().findByLabelText("Department");
    const offered = within(departments).getAllByRole("option")
      .map((option) => option.textContent).filter((name) => name !== "Select a department");
    expect(offered).toEqual(OPTIONS.departments.map((d) => d.name));
  });

  it("does not offer a department the server has closed", async () => {
    const user = userEvent.setup();
    // Laboratory exists in the hospital; an administrator has not opened it
    // for appointments, so the server leaves it out.
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);

    const departments = await form().findByLabelText("Department");
    expect(within(departments).queryByRole("option", { name: "Laboratory" }))
      .not.toBeInTheDocument();
    // And it cannot be chosen: there is no option to choose.
    await expect(user.selectOptions(departments, "Laboratory")).rejects.toThrow();
  });

  it("drops a department the moment the refreshed options leave it out", async () => {
    const user = userEvent.setup();
    const state = { options: OPTIONS };
    vi.spyOn(api, "get").mockImplementation((url) => {
      if (url === "/appointments/booking-options/") return Promise.resolve({ data: state.options });
      if (url === "/appointments/") return Promise.resolve({ data: [] });
      if (url === "/patients/") return Promise.resolve({ data: { results: [PATIENT] } });
      return Promise.resolve({ data: [] });
    });
    const { client } = renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    expect(within(await form().findByLabelText("Department"))
      .getByRole("option", { name: "Radiology / Ultrasound" })).toBeInTheDocument();

    // Radiology is switched off in Django admin; the next read omits it.
    state.options = { ...OPTIONS,
      departments: OPTIONS.departments.filter((d) => d.code !== "radiology") };
    await client.invalidateQueries({ queryKey: ["appointment-booking-options"] });

    await waitFor(() => expect(within(form().getByLabelText("Department"))
      .queryByRole("option", { name: "Radiology / Ultrasound" })).not.toBeInTheDocument());
  });

  it("says so plainly when the server closes a department mid-booking", async () => {
    const user = userEvent.setup();
    mockApi();
    vi.spyOn(api, "post").mockRejectedValue({
      response: { status: 400, data: { code: "department_not_available",
                                       detail: "Eye Clinic is not currently available for appointments." } },
    });
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "3");
    await user.click(await form().findByRole("checkbox", { name: /Eye Consultation/ }));
    await user.selectOptions(await form().findByLabelText("Provider"), "9");
    await user.click(screen.getByRole("button", { name: "Queue appointment" }));

    expect(await screen.findByText("That department is not taking appointments"))
      .toBeInTheDocument();
  });

  it("surfaces the server's refusal rather than its own guess", async () => {
    const user = userEvent.setup();
    mockApi();
    vi.spyOn(api, "post").mockRejectedValue({
      response: { status: 400, data: { code: "provider_not_eligible",
                                       detail: "Femi Bello cannot be booked for that." } },
    });
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "3");
    await user.click(await form().findByRole("checkbox", { name: /Eye Consultation/ }));
    await user.selectOptions(await form().findByLabelText("Provider"), "9");
    await user.click(screen.getByRole("button", { name: "Queue appointment" }));

    expect(await screen.findByText("That provider cannot take this appointment"))
      .toBeInTheDocument();
  });
});

describe("the existing consultation path, kept fast", () => {
  it("offers a general consultation under Consultation and raises no charge", async () => {
    const user = userEvent.setup();
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: APPOINTMENT });
    renderWithApp(<Appointments />, { user: RECEPTION });

    // Patient → Consultation → the doctor → queue. No service to name, and
    // no fee raised: the booking reception has always made.
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "2");
    await user.click(await form().findByRole("checkbox", { name: "General consultation" }));
    await user.selectOptions(await form().findByLabelText("Doctor"), "2");
    await user.click(screen.getByRole("button", { name: "Queue appointment" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/appointments/", {
      patient: 3, doctor: 2, reason: "",
    }));
  });

  it("offers the doctors on the general path, not every provider", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "2");
    await user.click(await form().findByRole("checkbox", { name: "General consultation" }));

    const doctors = await form().findByLabelText("Doctor");
    expect(within(doctors).getByRole("option", { name: /Dara Ade/ })).toBeInTheDocument();
    expect(within(doctors).queryByRole("option", { name: /Femi Bello/ })).not.toBeInTheDocument();
  });

  it("says no charge for it, and no fee panel claims otherwise", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "2");
    await user.click(await form().findByRole("checkbox", { name: "General consultation" }));
    await user.selectOptions(await form().findByLabelText("Doctor"), "2");

    const summary = (await screen.findByText("Appointment summary")).closest("div");
    expect(within(summary).getByText("General consultation")).toBeInTheDocument();
    expect(within(summary).getByText("No charge")).toBeInTheDocument();
    expect(within(summary).getByText("Nothing to collect")).toBeInTheDocument();
  });

  it("still offers the priced consultation service beside it", async () => {
    const user = userEvent.setup();
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: APPOINTMENT });
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "2");
    await user.click(await form().findByRole("checkbox", { name: /Doctor Consultation/ }));
    await user.selectOptions(await form().findByLabelText("Provider"), "2");
    await user.click(screen.getByRole("button", { name: "Queue appointment" }));

    // Reception chooses whether the visit is billed; both are one click.
    await waitFor(() => expect(post).toHaveBeenCalledWith("/appointments/", {
      patient: 3, doctor: 2, reason: "", service: "billing_item:2",
    }));
  });

  it("offers no general option outside the consultation department", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "3");
    await form().findByRole("checkbox", { name: /Eye Consultation/ });

    expect(form().queryByRole("checkbox", { name: "General consultation" }))
      .not.toBeInTheDocument();
  });
});

describe("the queue list", () => {
  it("is department-aware without becoming a report", async () => {
    mockApi({ appointments: [APPOINTMENT] });
    renderWithApp(<Appointments />, { user: RECEPTION });

    const row = (await screen.findByText("Doe, John")).closest("tr");
    expect(within(row).getByText("Eye Clinic")).toBeInTheDocument();
    expect(within(row).getByText("Eye Consultation")).toBeInTheDocument();
    expect(within(row).getByText("Femi Bello")).toBeInTheDocument();
    expect(within(row).getByText("Queued")).toBeInTheDocument();
  });

  it("shows the fee and the payment state as two different things", async () => {
    mockApi({ appointments: [APPOINTMENT] });
    renderWithApp(<Appointments />, { user: RECEPTION });

    const row = (await screen.findByText("Doe, John")).closest("tr");
    expect(within(row).getByText("₦5,000")).toBeInTheDocument();
    // A fee is not a payment: the row says UNPAID beside it.
    expect(within(row).getByText("UNPAID")).toBeInTheDocument();
  });

  it("shows no payment state for an appointment that raised no bill", async () => {
    mockApi({ appointments: [{ ...APPOINTMENT, charge: null, service_fee: "0.00",
      billing: { billed: false, status: "unbilled", label: "NOT BILLED",
                 requires_payment: false, amount: "0.00", paid: "0.00", outstanding: "0.00" } }] });
    renderWithApp(<Appointments />, { user: RECEPTION });

    const row = (await screen.findByText("Doe, John")).closest("tr");
    expect(within(row).queryByText("UNPAID")).not.toBeInTheDocument();
  });

  it("filters by department and by status", async () => {
    const user = userEvent.setup();
    mockApi({ appointments: [
      APPOINTMENT,
      { ...APPOINTMENT, id: 2, patient_name: "Obi, Ada", department: 4,
        department_name: "Radiology / Ultrasound", service_name: "Ultrasound Examination",
        status: "completed" },
    ] });
    renderWithApp(<Appointments />, { user: RECEPTION });

    await screen.findByText("Doe, John");
    await user.selectOptions(screen.getByLabelText("Filter by department"), "4");
    expect(screen.queryByText("Doe, John")).not.toBeInTheDocument();
    expect(screen.getByText("Obi, Ada")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Filter by department"), "");
    await user.selectOptions(screen.getByLabelText("Filter by status"), "queued");
    expect(screen.getByText("Doe, John")).toBeInTheDocument();
    expect(screen.queryByText("Obi, Ada")).not.toBeInTheDocument();
  });

  it("finds a patient by hospital number", async () => {
    const user = userEvent.setup();
    mockApi({ appointments: [APPOINTMENT,
      { ...APPOINTMENT, id: 2, patient_name: "Obi, Ada", patient_number: "NMHS-P000099" }] });
    renderWithApp(<Appointments />, { user: RECEPTION });

    await screen.findByText("Doe, John");
    await user.type(screen.getByPlaceholderText(/Patient, number/), "P000099");
    expect(screen.getByText("Obi, Ada")).toBeInTheDocument();
    expect(screen.queryByText("Doe, John")).not.toBeInTheDocument();
  });
});

describe("the provider working their queue", () => {
  const EYE_DOCTOR = { id: 9, role: "ophthalmologist" };

  it("offers the existing transitions on their own row", async () => {
    mockApi({ appointments: [APPOINTMENT] });
    renderWithApp(<Appointments />, { user: EYE_DOCTOR });

    expect(await screen.findByRole("button", { name: "Accept" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("offers Start once it is accepted, and End once it is under way", async () => {
    mockApi({ appointments: [{ ...APPOINTMENT, status: "accepted" }] });
    const { unmount } = renderWithApp(<Appointments />, { user: EYE_DOCTOR });
    expect(await screen.findByRole("button", { name: "Start" })).toBeInTheDocument();
    unmount();

    mockApi({ appointments: [{ ...APPOINTMENT, status: "in_progress" }] });
    renderWithApp(<Appointments />, { user: EYE_DOCTOR });
    expect(await screen.findByRole("button", { name: "End" })).toBeInTheDocument();
  });

  it("calls the existing transition endpoint", async () => {
    const user = userEvent.setup();
    mockApi({ appointments: [APPOINTMENT] });
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: APPOINTMENT });
    renderWithApp(<Appointments />, { user: EYE_DOCTOR });

    await user.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/appointments/1/accept/"));
  });

  it("offers a provider no booking form", async () => {
    mockApi({ appointments: [APPOINTMENT] });
    renderWithApp(<Appointments />, { user: EYE_DOCTOR });

    await screen.findByText("My appointments");
    expect(screen.queryByText("Queue an appointment")).not.toBeInTheDocument();
  });

  it("offers no transitions on somebody else's row", async () => {
    mockApi({ appointments: [APPOINTMENT] });
    renderWithApp(<Appointments />, { user: { id: 77, role: "radiology" } });

    await screen.findByText("Doe, John");
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });
});

describe("booking several services at once", () => {
  const openImaging = async (user) => {
    await choosePatient(user);
    await user.selectOptions(await form().findByLabelText("Department"), "4");
  };
  const tick = (user, name) =>
    user.click(form().getByRole("checkbox", { name: new RegExp(name) }));

  it("offers every configured service in the department, each with its price", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);

    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    for (const name of ["Abdominal Ultrasound", "Renal Ultrasound", "Pelvic Ultrasound"]) {
      expect(form().getByRole("checkbox", { name: new RegExp(name) })).toBeInTheDocument();
    }
    expect(form().getByText("₦10,000")).toBeInTheDocument();
    expect(form().getByText("₦8,000")).toBeInTheDocument();
  });

  it("selects one and totals it", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    await tick(user, "Abdominal Ultrasound");

    expect(form().getByText(/1 selected · Total ₦10,000/)).toBeInTheDocument();
  });

  it("selects several and combines the fee", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    await tick(user, "Abdominal Ultrasound");
    await tick(user, "Renal Ultrasound");
    await tick(user, "Pelvic Ultrasound");

    expect(form().getByText(/3 selected · Total ₦24,000/)).toBeInTheDocument();
  });

  it("unselects one and the total drops immediately, keeping the rest", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    await tick(user, "Abdominal Ultrasound");
    await tick(user, "Renal Ultrasound");
    await tick(user, "Pelvic Ultrasound");
    await tick(user, "Renal Ultrasound");

    expect(form().getByText(/2 selected · Total ₦16,000/)).toBeInTheDocument();
    expect(form().getByRole("checkbox", { name: /Renal Ultrasound/ })).not.toBeChecked();
    expect(form().getByRole("checkbox", { name: /Abdominal Ultrasound/ })).toBeChecked();
  });

  it("removes one from the selected chips as well", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    await tick(user, "Abdominal Ultrasound");
    await tick(user, "Renal Ultrasound");

    await user.click(form().getByRole("button", { name: "Remove Renal Ultrasound" }));
    expect(form().getByText(/1 selected · Total ₦10,000/)).toBeInTheDocument();
    expect(form().getByRole("checkbox", { name: /Renal Ultrasound/ })).not.toBeChecked();
  });

  it("clears the whole selection in one press", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    await tick(user, "Abdominal Ultrasound");
    await tick(user, "Renal Ultrasound");

    await user.click(form().getByRole("button", { name: "Clear all" }));
    expect(form().queryByText(/selected · Total/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Queue appointment" })).toBeDisabled();
  });

  it("names every service in the summary and counts them", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    await tick(user, "Abdominal Ultrasound");
    await tick(user, "Renal Ultrasound");
    await user.selectOptions(await form().findByLabelText("Provider"), "12");

    const summary = (await screen.findByText("Appointment summary")).closest("div");
    expect(within(summary).getByText("Services (2)")).toBeInTheDocument();
    expect(within(summary).getByText("Abdominal Ultrasound + Renal Ultrasound"))
      .toBeInTheDocument();
    expect(within(summary).getByText("₦18,000")).toBeInTheDocument();
  });

  it("submits every ticked identity and no price at all", async () => {
    const user = userEvent.setup();
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: APPOINTMENT });
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    await tick(user, "Abdominal Ultrasound");
    await tick(user, "Pelvic Ultrasound");
    await user.selectOptions(await form().findByLabelText("Provider"), "12");
    await user.click(screen.getByRole("button", { name: "Queue appointment" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/appointments/", {
      patient: 3, doctor: 12, reason: "",
      service: ["billing_item:7", "billing_item:9"],
    }));
  });

  it("offers no provider until something is ticked", async () => {
    const user = userEvent.setup();
    mockApi();
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });

    expect(form().queryByLabelText("Provider")).not.toBeInTheDocument();
    await tick(user, "Abdominal Ultrasound");
    expect(await form().findByLabelText("Provider")).toBeInTheDocument();
  });

  it("keeps the selection while the booking is in flight", async () => {
    const user = userEvent.setup();
    mockApi();
    let settle;
    vi.spyOn(api, "post").mockImplementation(() => new Promise((r) => { settle = r; }));
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    await tick(user, "Abdominal Ultrasound");
    await user.selectOptions(await form().findByLabelText("Provider"), "12");
    await user.click(screen.getByRole("button", { name: "Queue appointment" }));

    // The button is spent while the request is out, so a double-click cannot
    // become a second booking.
    const queueing = await screen.findByRole("button", { name: "Queueing…" });
    expect(queueing).toBeDisabled();
    settle({ data: APPOINTMENT });
  });

  it("clears the form once the booking lands", async () => {
    const user = userEvent.setup();
    mockApi();
    vi.spyOn(api, "post").mockResolvedValue({ data: APPOINTMENT });
    renderWithApp(<Appointments />, { user: RECEPTION });
    await openImaging(user);
    await form().findByRole("checkbox", { name: /Abdominal Ultrasound/ });
    await tick(user, "Abdominal Ultrasound");
    await user.selectOptions(await form().findByLabelText("Provider"), "12");
    await user.click(screen.getByRole("button", { name: "Queue appointment" }));

    await waitFor(() =>
      expect(form().queryByLabelText("Department")).not.toBeInTheDocument());
  });
});
