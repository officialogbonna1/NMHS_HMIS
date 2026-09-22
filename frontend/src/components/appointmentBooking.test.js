import { describe, expect, it } from "vitest";

import {
  afterDepartmentChange, afterModeChange, afterServiceToggle, allowsGeneral, asChips,
  bookingBody, canQueue, departmentsOf, feeFor, GENERAL, providersFor, providersForAll,
  serviceFor, servicesFor, summaryRows, toggleService, totalFee,
} from "./appointmentBooking";

// The booking form's decisions, without the DOM. Every list here arrives from
// `/appointments/booking-options/`; nothing in the module knows a department,
// a service, a provider or a price of its own.

const EYE_DOCTOR = { id: 9, name: "Femi Bello", role: "ophthalmologist", role_label: "Ophthalmologist / Eye Doctor" };
const OPTOMETRIST = { id: 11, name: "Ada Obi", role: "optometrist", role_label: "Optometrist" };
const RADIOGRAPHER = { id: 12, name: "Bisi Ade", role: "radiology", role_label: "Radiology Staff" };

const DOCTOR = { id: 2, name: "Dara Ade", role: "doctor", role_label: "Doctor" };

const OPTIONS = {
  // The fast path the server describes: which department a general
  // consultation belongs to, and who may take one.
  general: { department: 2, department_name: "Consultation",
             label: "General consultation", providers: [DOCTOR] },
  departments: [
    { id: 2, code: "consultation", name: "Consultation", services: [
      { key: "billing_item:2", id: 2, name: "Doctor Consultation", fee: "5000.00",
        billable: true, providers: [DOCTOR] },
    ] },
    {
      id: 3, code: "eye", name: "Eye Clinic",
      services: [
        { key: "billing_item:5", id: 5, name: "Eye Consultation", fee: "5000.00",
          billable: true, providers: [EYE_DOCTOR, OPTOMETRIST] },
        { key: "billing_item:6", id: 6, name: "Eye Follow-up", fee: "0.00",
          billable: false, providers: [EYE_DOCTOR] },
      ],
    },
    {
      id: 4, code: "radiology", name: "Radiology / Ultrasound",
      services: [
        { key: "billing_item:7", id: 7, name: "Ultrasound Examination", fee: "10000.00",
          billable: true, providers: [RADIOGRAPHER] },
      ],
    },
  ],
};

const PATIENT = { id: 3, first_name: "John", last_name: "Doe", patient_number: "NMHS-P000012" };

const EYE = () => serviceFor(OPTIONS, "3", "billing_item:5");
const FOLLOW_UP = () => serviceFor(OPTIONS, "3", "billing_item:6");
const SCAN = () => serviceFor(OPTIONS, "4", "billing_item:7");

const picked = (overrides) => ({
  patient: PATIENT, departmentId: "3", serviceKey: "", services: [EYE()],
  providerId: "9", reason: "Blurred vision", ...overrides,
});

describe("narrowing the lists", () => {
  it("offers only the departments the server sent", () => {
    expect(departmentsOf(OPTIONS).map((d) => d.name))
      .toEqual(["Consultation", "Eye Clinic", "Radiology / Ultrasound"]);
  });

  it("offers no service until a department is chosen", () => {
    expect(servicesFor(OPTIONS, "")).toEqual([]);
    expect(servicesFor(OPTIONS, null)).toEqual([]);
  });

  it("offers only that department's services", () => {
    expect(servicesFor(OPTIONS, "3").map((s) => s.name))
      .toEqual(["Eye Consultation", "Eye Follow-up"]);
    expect(servicesFor(OPTIONS, "4").map((s) => s.name)).toEqual(["Ultrasound Examination"]);
  });

  it("offers only the providers eligible for the chosen service", () => {
    const eye = serviceFor(OPTIONS, "3", "billing_item:5");
    expect(providersFor(eye).map((p) => p.id)).toEqual([9, 11]);
    const scan = serviceFor(OPTIONS, "4", "billing_item:7");
    expect(providersFor(scan).map((p) => p.id)).toEqual([12]);
  });

  it("offers no provider before a service is chosen", () => {
    expect(providersFor(serviceFor(OPTIONS, "3", ""))).toEqual([]);
  });

  it("copes with a payload that has not arrived yet", () => {
    expect(departmentsOf(undefined)).toEqual([]);
    expect(servicesFor(undefined, "3")).toEqual([]);
    expect(serviceFor(undefined, "3", "billing_item:5")).toBeNull();
  });
});

describe("ticking and unticking services", () => {
  it("adds a service that is not selected", () => {
    const next = toggleService([EYE()], FOLLOW_UP());
    expect(next.map((s) => s.key)).toEqual(["billing_item:5", "billing_item:6"]);
  });

  it("removes one that is, leaving the others where they were", () => {
    const next = toggleService([EYE(), FOLLOW_UP()], EYE());
    expect(next.map((s) => s.key)).toEqual(["billing_item:6"]);
  });

  it("is its own inverse, however many times it is pressed", () => {
    let selection = [];
    for (let i = 0; i < 5; i += 1) selection = toggleService(selection, EYE());
    expect(selection.map((s) => s.key)).toEqual(["billing_item:5"]);
  });

  it("never mutates the selection it was given", () => {
    const before = [EYE()];
    toggleService(before, FOLLOW_UP());
    expect(before).toHaveLength(1);
  });

  it("moves the running total with it", () => {
    expect(totalFee([])).toBe(0);
    expect(totalFee([EYE()])).toBe(5000);
    expect(totalFee([EYE(), FOLLOW_UP()])).toBe(5000);
    expect(totalFee([EYE(), SCAN()])).toBe(15000);
  });
});

describe("what a change clears behind it", () => {
  it("clears the services and the provider when the department changes", () => {
    const next = afterDepartmentChange(picked({}), "4");
    expect(next).toMatchObject({ departmentId: "4", serviceKey: "", providerId: "" });
    expect(next.services).toEqual([]);
    expect(next.patient).toBe(PATIENT);
    expect(next.reason).toBe("Blurred vision");
  });

  it("keeps a provider who can still do everything that is ticked", () => {
    // The eye doctor works both eye services, so adding the second must not
    // silently drop the person reception already chose.
    const next = afterServiceToggle(OPTIONS, picked({}), FOLLOW_UP());
    expect(next.services).toHaveLength(2);
    expect(next.providerId).toBe("9");
  });

  it("drops a provider who cannot do one of them", () => {
    // The optometrist does not work Eye Follow-up.
    const next = afterServiceToggle(OPTIONS, picked({ providerId: "11" }), FOLLOW_UP());
    expect(next.providerId).toBe("");
  });

  it("clears the ticked services when switching to the general consultation", () => {
    const next = afterModeChange(picked({}), GENERAL);
    expect(next.serviceKey).toBe(GENERAL);
    expect(next.services).toEqual([]);
    expect(next.providerId).toBe("");
  });
});

describe("who may be named on a basket", () => {
  it("offers only the people eligible for every ticked service", () => {
    expect(providersForAll([EYE()], OPTIONS, "").map((p) => p.id)).toEqual([9, 11]);
    // The optometrist drops out once Eye Follow-up is added.
    expect(providersForAll([EYE(), FOLLOW_UP()], OPTIONS, "").map((p) => p.id)).toEqual([9]);
  });

  it("offers nobody before anything is ticked", () => {
    expect(providersForAll([], OPTIONS, "")).toEqual([]);
  });
});

describe("the fee, which is not a payment", () => {
  it("quotes the catalogue's own figure", () => {
    const fee = feeFor([SCAN()]);
    expect(fee.amount).toBe(10000);
    expect(fee.billable).toBe(true);
    expect(fee.label).toContain("10,000");
  });

  it("combines several into one total", () => {
    const fee = feeFor([EYE(), SCAN()]);
    expect(fee.amount).toBe(15000);
    expect(fee.count).toBe(2);
    expect(fee.label).toContain("15,000");
    expect(fee.note).toMatch(/One bill per service/);
  });

  it("says no charge for a service with no price, rather than ₦0", () => {
    const fee = feeFor([FOLLOW_UP()]);
    expect(fee.billable).toBe(false);
    expect(fee.label).toBe("No charge");
  });

  it("never claims anything has been paid", () => {
    expect(feeFor([EYE()]).note).toMatch(/settles it at Reception or the cash desk/);
    expect(feeFor([EYE()]).note).not.toMatch(/paid/i);
  });

  it("says nothing at all before a service is chosen", () => {
    expect(feeFor([]).known).toBe(false);
    expect(feeFor(null).known).toBe(false);
  });
});

describe("what gets submitted", () => {
  it("sends identities and no money", () => {
    const body = bookingBody(picked({}));
    expect(body).toEqual({
      patient: 3, doctor: 9, reason: "Blurred vision", service: "billing_item:5",
    });
  });

  it("sends every ticked identity when several are chosen", () => {
    const body = bookingBody(picked({ services: [EYE(), FOLLOW_UP()] }));
    expect(body.service).toEqual(["billing_item:5", "billing_item:6"]);
  });

  it("carries no amount, no fee and no department", () => {
    const body = bookingBody(picked({}));
    for (const key of ["amount", "fee", "price", "service_fee", "department", "charge"]) {
      expect(body).not.toHaveProperty(key);
    }
  });

  it("omits the service entirely for a plain consultation booking", () => {
    // The path reception has always used: patient, doctor, reason.
    const body = bookingBody(picked({ departmentId: "", serviceKey: "", services: [] }));
    expect(body).not.toHaveProperty("service");
    expect(body).toEqual({ patient: 3, doctor: 9, reason: "Blurred vision" });
  });

  it("will not submit without a patient, a provider and something to book", () => {
    expect(canQueue(picked({}))).toBe(true);
    expect(canQueue(picked({ patient: null }))).toBe(false);
    expect(canQueue(picked({ providerId: "" }))).toBe(false);
    expect(canQueue(picked({ services: [] }))).toBe(false);
    // Except the general consultation, which names no service by design.
    expect(canQueue(picked({ services: [], serviceKey: GENERAL }))).toBe(true);
  });

  it("hands the chips the key the shared component reads", () => {
    expect(asChips([EYE()])[0]).toMatchObject({ key: "billing_item:5", price: "5000.00" });
  });
});

describe("the summary", () => {
  it("describes exactly what is about to be submitted", () => {
    const rows = Object.fromEntries(summaryRows(OPTIONS, picked({})).map((r) => [r.label, r.value]));
    expect(rows.Patient).toBe("Doe, John · NMHS-P000012");
    expect(rows.Department).toBe("Eye Clinic");
    expect(rows.Service).toBe("Eye Consultation");
    expect(rows.Provider).toBe("Femi Bello");
    expect(rows.Fee).toContain("5,000");
    expect(rows.Payment).toBe("Existing billing workflow");
  });

  it("names every service and counts them when several are booked", () => {
    const rows = Object.fromEntries(summaryRows(OPTIONS, picked({
      services: [EYE(), FOLLOW_UP()],
    })).map((r) => [r.label, r.value]));
    expect(rows["Services (2)"]).toBe("Eye Consultation + Eye Follow-up");
    expect(rows.Fee).toContain("5,000");
  });

  it("says there is nothing to collect on a free service", () => {
    const rows = Object.fromEntries(
      summaryRows(OPTIONS, picked({ services: [FOLLOW_UP()] })).map((r) => [r.label, r.value]));
    expect(rows.Fee).toBe("No charge");
    expect(rows.Payment).toBe("Nothing to collect");
  });

  it("updates as the selection changes", () => {
    const scan = picked({ departmentId: "4", services: [SCAN()], providerId: "12" });
    const rows = Object.fromEntries(summaryRows(OPTIONS, scan).map((r) => [r.label, r.value]));
    expect(rows.Department).toBe("Radiology / Ultrasound");
    expect(rows.Service).toBe("Ultrasound Examination");
    expect(rows.Provider).toBe("Bisi Ade");
    expect(rows.Fee).toContain("10,000");
  });
});


describe("the general consultation, which names no service", () => {
  it("is offered under the consultation department and nowhere else", () => {
    expect(allowsGeneral(OPTIONS, "2")).toBe(true);
    expect(allowsGeneral(OPTIONS, "3")).toBe(false);
    expect(allowsGeneral(OPTIONS, "4")).toBe(false);
  });

  it("offers the hospital's doctors, which is who the old form offered", () => {
    expect(providersFor(null, OPTIONS, GENERAL).map((p) => p.id)).toEqual([2]);
  });

  it("has no service and therefore no fee", () => {
    expect(serviceFor(OPTIONS, "2", GENERAL)).toBeNull();
    expect(feeFor([]).known).toBe(false);
  });

  it("submits patient, doctor and reason — exactly as it always did", () => {
    const body = bookingBody({ patient: PATIENT, departmentId: "2", serviceKey: GENERAL,
                               services: [], providerId: "2", reason: "Fever" });
    expect(body).toEqual({ patient: 3, doctor: 2, reason: "Fever" });
    expect(body).not.toHaveProperty("service");
  });

  it("reads as a general consultation with nothing to collect", () => {
    const rows = Object.fromEntries(summaryRows(OPTIONS, {
      patient: PATIENT, departmentId: "2", serviceKey: GENERAL, services: [],
      providerId: "2", reason: "",
    }).map((r) => [r.label, r.value]));
    expect(rows.Department).toBe("Consultation");
    expect(rows.Service).toBe("General consultation");
    expect(rows.Provider).toBe("Dara Ade");
    expect(rows.Fee).toBe("No charge");
    expect(rows.Payment).toBe("Nothing to collect");
  });

  it("is cleared when the department changes", () => {
    const next = afterDepartmentChange(
      { patient: PATIENT, departmentId: "2", serviceKey: GENERAL, services: [],
        providerId: "2", reason: "" },
      "3",
    );
    expect(next.serviceKey).toBe("");
    expect(next.services).toEqual([]);
    expect(next.providerId).toBe("");
  });
});
