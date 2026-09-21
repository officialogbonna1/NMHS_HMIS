/**
 * The representative documents, rendered and inspected.
 *
 * The branding test beside this one proves the *header* is right and that
 * every sheet imports it. This one is the other half: it actually renders one
 * document from each department the hospital prints for and looks at what
 * comes out — the letterhead, the hospital's name, the document's own title
 * and reference — because a shared component can be correct while a sheet
 * passes it nothing.
 *
 * The fetching sheets (the laboratory report, the discharge letter) get a
 * stubbed `api`, so the assertion is about the rendered document rather than
 * about a loading placeholder.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const get = vi.fn();
vi.mock("../api/client", () => ({ default: { get: (...a) => get(...a) } }));
vi.mock("../api/client.js", () => ({ default: { get: (...a) => get(...a) } }));

const { PatientCardSheet, BillSheet, ReceiptSheet } = await import("./PrintDocuments.jsx");
const { ConsultationNoteSheet, AdmissionSheet, DischargeLetterSheet, DispensingSheet } =
  await import("./DepartmentDocuments.jsx");
const { default: LabReportSheet } = await import("./LabReportSheet.jsx");
const { HOSPITAL } = await import("./PrintSheet.jsx");

function show(ui) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** Every document must carry the mark and the hospital's own name. */
async function expectLetterhead(title) {
  const logo = await screen.findByAltText(HOSPITAL.fullName);
  expect(logo.className).toContain("sheet-logo");
  expect(logo.getAttribute("src")).toBeTruthy();
  expect(screen.getByText(HOSPITAL.fullName)).toBeTruthy();
  if (title) expect(screen.getByText(title)).toBeTruthy();
}

const patient = {
  id: 3, uuid: "0c6f1f2e-0000-4000-8000-000000000001",
  patient_number: "NMHS-P000003", first_name: "Ada", last_name: "Okoro",
  sex: "F", age_display: "31 years", phone_number: "08030000000",
};

beforeEach(() => {
  get.mockReset();
});

describe("a document from each department carries the letterhead", () => {
  it("Patient card", async () => {
    show(<PatientCardSheet patient={patient} onClose={() => {}} />);
    await expectLetterhead("Patient Registration");
    // Its own content is untouched by the branding.
    expect(screen.getAllByText("NMHS-P000003").length).toBeGreaterThan(0);
  });

  it("Bill / invoice", async () => {
    show(
      <BillSheet
        patient={patient}
        charges={[{ id: 1, description: "Consultation fee", amount: "5000.00",
                    status: "unpaid", created_at: "2026-09-20T09:00:00Z" }]}
        onClose={() => {}}
      />,
    );
    await expectLetterhead("Invoice");
    expect(screen.getByText("Consultation fee")).toBeTruthy();
  });

  it("Payment receipt", async () => {
    show(
      <ReceiptSheet
        patient={patient}
        payment={{ id: 9, amount: "2000.00", method: "cash", channel: "front_desk",
                   created_at: "2026-09-20T09:30:00Z", received_by_name: "Ngozi Cashier" }}
        balanceAfter="3000.00"
        onClose={() => {}}
      />,
    );
    await expectLetterhead("Receipt");
  });

  it("Clinical / consultation note", async () => {
    show(
      <ConsultationNoteSheet
        note={{ id: 4, visit_time: "2026-09-20T08:00:00Z", reason_for_visit: "Blurred vision",
                diagnosis: "Refractive error", plan: "Refraction", note_text: "",
                doctor_name: "Dr Smith", amendments: [] }}
        patient={patient}
        onClose={() => {}}
      />,
    );
    await expectLetterhead();
    expect(screen.getByText(/Refractive error/)).toBeTruthy();
  });

  it("Ward / admission slip", async () => {
    show(
      <AdmissionSheet
        admission={{ id: 2, patient_name: "Okoro, Ada", patient_number: "NMHS-P000003",
                     patient_sex: "Female", patient_age: "31 years", ward_name: "Maternity",
                     bed_number: "M1", status: "admitted", admitted_at: "2026-09-18T10:00:00Z",
                     attending_doctor_name: "Dr Smith", admitted_by_name: "Nurse Ada" }}
        patientId={3}
        onClose={() => {}}
      />,
    );
    await expectLetterhead("Admission Slip");
    // "Maternity" is in the hospital's name too, so the ward is one of several.
    expect(screen.getAllByText(/Maternity/).length).toBeGreaterThan(1);
  });

  it("Laboratory report", async () => {
    get.mockResolvedValue({ data: {
      order: { order_number: "LAB-000007", created_at: "2026-09-20T07:00:00Z",
               verified_at: "2026-09-20T08:00:00Z", status: "verified",
               requested_by_name: "Dr Smith", verified_by_name: "Sci Ada",
               tested_by_name: "Sci Ada", specimen: "Whole blood" },
      patient: { name: "Okoro, Ada", patient_number: "NMHS-P000003",
                 sex: "Female", age: "31 years" },
      sections: [],
    } });
    show(<LabReportSheet orderId={7} onClose={() => {}} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await expectLetterhead();
  });

  it("Pharmacy dispensing note", async () => {
    get.mockResolvedValue({ data: { results: [{
      id: 5, item_name: "Paracetamol", quantity: 10, status: "dispensed",
      patient_name: "Okoro, Ada", patient_file_number: "NMHS-P000003",
      dispensed_at: "2026-09-20T09:00:00Z", dispensed_by_name: "Pharm Ada",
      dosage_instructions: "Two at night", unit_label: "tablet",
    }] } });
    show(<DispensingSheet patientId={3} prescriptionIds={[5]} onClose={() => {}} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await expectLetterhead();
  });

  it("Discharge letter", async () => {
    get.mockResolvedValue({ data: {
      reference: "DCH-000012",
      patient: { name: "Okoro, Ada", patient_number: "NMHS-P000003",
                 uuid: patient.uuid, sex: "Female", age: "31 years",
                 phone: "08030000000", address: "Aba" },
      admission: { reference: "ADM-000002", ward: "Maternity", bed: "M1",
                   admitted_at: "2026-09-18T10:00:00Z", discharged_at: "2026-09-20T09:00:00Z",
                   status: "discharged", admission_diagnosis: "Fever",
                   attending_doctor: "Dr Smith", admitted_by: "Nurse Ada" },
      discharge: { diagnosis: "Pneumonia, resolved", summary: "Uneventful stay.",
                   instructions: "Complete the course.", follow_up: null,
                   condition: "Recovered", completed_by: "Ngozi Admin",
                   completed_by_number: "NMHS-S000001", completed_at: "2026-09-20T09:05:00Z" },
    } });
    show(<DischargeLetterSheet dischargeId={12} onClose={() => {}} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await expectLetterhead("Discharge Letter");
    expect(screen.getByText("No. DCH-000012")).toBeTruthy();
    expect(screen.getByText(/Pneumonia, resolved/)).toBeTruthy();
  });
});
