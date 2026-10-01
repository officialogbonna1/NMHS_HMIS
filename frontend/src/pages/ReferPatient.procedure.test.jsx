/**
 * Referring a patient for a procedure (rule 59): the doctor names the priced
 * procedures from the configured catalogue, and each raises its charge with
 * the referral — imaging's two calls, so a catalogue hiccup cannot swallow a
 * hand-off already made. Plus the printed Procedure Record and the nav row.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const get = vi.fn();
const post = vi.fn();
vi.mock("../api/client", () => ({ default: { get: (...a) => get(...a), post: (...a) => post(...a) } }));

const ReferPatient = (await import("./ReferPatient.jsx")).default;
const { ReferralDocumentSheet } = await import("../components/DepartmentDocuments.jsx");
const AppShell = (await import("../components/AppShell.jsx")).default;
const { renderWithApp } = await import("../test/harness.jsx");

const DOCTOR = { id: 4, username: "doc", role: "doctor", first_name: "Chidi", last_name: "Nwosu" };
const PATIENT = { id: 3, uuid: "8b0e5f1e-1111-4000-8000-000000000000", first_name: "Michael",
                  last_name: "John", patient_number: "NMHS-P000014" };
const PROCEDURES = [
  { key: "billing_item:21", id: 21, name: "Wound Dressing", category: "procedure",
    category_label: "Procedure", source_type: "procedure", price: "2500.00", detail: "" },
  { key: "billing_item:22", id: 22, name: "Incision & Drainage", category: "procedure",
    category_label: "Procedure", source_type: "procedure", price: "5000.00", detail: "" },
];
const SCANS = [
  { key: "billing_item:11", id: 11, name: "Abdominal Ultrasound", category: "ultrasound",
    category_label: "Ultrasound / Imaging", source_type: "ultrasound", price: "8000.00", detail: "" },
];

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  get.mockImplementation((url, config) => {
    if (url === "/patients/") return Promise.resolve({ data: { results: [PATIENT] } });
    if (url === "/billable-services/") {
      return Promise.resolve({ data: { results: config?.params?.category === "ultrasound" ? SCANS : PROCEDURES } });
    }
    return Promise.resolve({ data: { results: [] } });
  });
  post.mockImplementation((url) => (url === "/patient-routes/refer/"
    ? Promise.resolve({ data: { id: 77, purpose: "procedure", notified: ["Bola Ade"] } })
    : Promise.resolve({ data: {} })));
});

async function pickThePatient(user) {
  await user.click(screen.getByPlaceholderText(/Search or pick a patient/));
  await user.click(await screen.findByText(/John, Michael/));
}

describe("referring for a procedure", () => {
  it("offers the configured procedures, from the procedure price list", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReferPatient />, { user: DOCTOR });
    await pickThePatient(user);
    await user.click(screen.getByText("Procedure"));
    expect(await screen.findByText("Which procedure?")).toBeInTheDocument();
    expect(await screen.findByText(/Incision & Drainage/)).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/billable-services/",
      expect.objectContaining({ params: expect.objectContaining({ category: "procedure" }) }));
  });

  it("sends the referral, then orders the chosen procedures on it", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReferPatient />, { user: DOCTOR });
    await pickThePatient(user);
    await user.click(screen.getByText("Procedure"));
    await user.click(await screen.findByText(/Incision & Drainage/));
    await user.click(screen.getByRole("button", { name: /Send referral/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/patient-routes/77/request-services/", { services: [22] }));
    expect(post.mock.calls[0]).toEqual(["/patient-routes/refer/",
      expect.objectContaining({ patient: 3, purpose: "procedure" })]);
  });

  it("does not carry scans chosen for imaging over to a procedure", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReferPatient />, { user: DOCTOR });
    await pickThePatient(user);
    await user.click(screen.getByText("Radiology / Ultrasound"));
    await user.click(await screen.findByText(/Abdominal Ultrasound/));
    await user.click(screen.getByText("Procedure"));
    await user.click(screen.getByRole("button", { name: /Send referral/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls.some(([url]) => url.includes("request-services"))).toBe(false);
  });
});

describe("the printed Procedure Record", () => {
  it("prints the record and the materials as a table, once", async () => {
    get.mockImplementation(() => Promise.resolve({ data: {
      route: { id: 7, purpose: "procedure", purpose_label: "Procedure", priority: "Routine",
               status: "Completed", department: "Procedure", notes: "Abscess",
               created_at: "2026-10-01T08:00:00Z", routed_by: "Chidi Nwosu",
               assigned_to: "Ifeoma Eze", reference: "REF-000007", services: [] },
      patient: { id: 3, name: "John, Michael", patient_number: "NMHS-P000014", sex: "Male", age: "40 years" },
      result: {
        text: "Procedure performed:\nI&D\n\nMaterials used:\n- Gauze swab: used 6",
        findings_text: "Procedure performed:\nI&D",
        materials: [{ name: "Gauze swab", category: "Consumable", quantity_received: "10.00",
                      quantity_used: "6.00", quantity_remaining: "3.00", wastage: "1.00", unit: "piece" }],
        recorded_by: "Ifeoma Eze", recorded_at: "2026-10-01T10:00:00Z",
      },
    } }));
    renderWithApp(<ReferralDocumentSheet routeId={7} variant="report" onClose={() => {}} />,
                  { user: DOCTOR });
    const table = await screen.findByRole("table", { name: "Materials used" });
    const cells = within(table).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toEqual(expect.arrayContaining(["Gauze swab (Consumable)", "10.00", "6.00", "3.00", "1.00", "piece"]));
    expect(screen.getAllByText(/Procedure Record/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Materials used:/)).not.toBeInTheDocument();   // not printed twice
    expect(screen.getByText(/Reported by Ifeoma Eze/)).toBeInTheDocument();
  });
});

describe("the Procedures nav row", () => {
  const navLinks = () => [...document.querySelectorAll("aside a")].map((a) => a.textContent.trim());

  it("is shown to a doctor or nurse posted to the department, and to admins", () => {
    get.mockImplementation(() => Promise.resolve({ data: { unread: 0, results: [] } }));
    for (const user of [
      { ...DOCTOR, authorized_departments: [{ id: 12, code: "procedure", name: "Procedure" }] },
      { id: 8, role: "nurse", authorized_departments: [{ id: 12, code: "procedure", name: "Procedure" }] },
      { id: 1, role: "hospital_admin", authorized_departments: [] },
    ]) {
      const { unmount } = renderWithApp(<AppShell />, { user });
      expect(navLinks()).toContain("Procedures");
      unmount();
    }
  });

  it("is shown to every one of several Procedure staff at once", () => {
    get.mockImplementation(() => Promise.resolve({ data: { unread: 0, results: [] } }));
    const PROC = [{ id: 12, code: "procedure", name: "Procedure" }];
    for (const user of [{ id: 31, role: "doctor", authorized_departments: PROC },
                        { id: 32, role: "doctor", authorized_departments: PROC },
                        { id: 33, role: "nurse", authorized_departments: PROC },
                        { id: 34, role: "nurse", authorized_departments: PROC },
                        { id: 35, role: "nurse", authorized_departments:
                          [...PROC, { id: 9, code: "theatre", name: "Theatre" }] }]) {
      const { unmount } = renderWithApp(<AppShell />, { user });
      expect(navLinks()).toContain("Procedures");
      unmount();
    }
  });

  it("is not shown to an unposted doctor, to Theatre-only staff, or to a cashier posted there", () => {
    get.mockImplementation(() => Promise.resolve({ data: { unread: 0, results: [] } }));
    for (const user of [
      { ...DOCTOR, authorized_departments: [] },
      { ...DOCTOR, authorized_departments: [{ id: 9, code: "theatre", name: "Theatre" }] },
      { id: 7, role: "nurse", authorized_departments: [{ id: 9, code: "theatre", name: "Theatre" }] },
      { id: 6, role: "cashier", authorized_departments: [{ id: 12, code: "procedure", name: "Procedure" }] },
    ]) {
      const { unmount } = renderWithApp(<AppShell />, { user });
      expect(navLinks()).not.toContain("Procedures");
      unmount();
    }
  });
});
