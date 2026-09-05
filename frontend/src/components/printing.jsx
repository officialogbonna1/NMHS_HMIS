import { useEffect, useRef, useState } from "react";
import { Button } from "./ui.jsx";
import { Icon } from "./icons.jsx";
import { hasRole, BILLING_ROLES } from "../auth/roles.js";
import { PatientCardSheet, PatientBillSheet, ReceiptSheet } from "./PrintDocuments.jsx";
import LabReportSheet from "./LabReportSheet.jsx";
import {
  LabRequestSheet, ReferralDocumentSheet, PrescriptionSheet, DispensingSheet,
  AdmissionSheet, VitalsRecordSheet, ClinicalSummarySheet,
} from "./DepartmentDocuments.jsx";

/**
 * What "Print" means, decided by where you are standing.
 *
 * The chart's Print button used to be `PatientCardSheet`, for everybody. That
 * is right at the front desk and wrong everywhere else: the laboratory needs
 * the request form that goes with the specimen, the ward needs the admission
 * slip, the pharmacy needs the dispensing note. A registration card printed at
 * the bench is a sheet somebody has to throw away.
 *
 * So the button asks this module instead. One registry of documents, each
 * naming the roles that may print it and the context it needs, and one
 * resolver that turns (role, page, record) into an ordered list. **The first
 * entry is what the button prints** — the department's own document, chosen
 * for them — and anything else the role may legitimately print sits behind
 * the caret rather than as a second button that looks identical.
 *
 * Three rules, and they are the reason this is a table rather than a
 * `switch` in each page:
 *
 * 1. **`roles` mirrors the backend.** Every document reads an endpoint its
 *    role already passes (`ClinicalSummarySheet` → `/patients/<id>/overview/`,
 *    ClinicalRecordAccess; `DispensingSheet` → `/prescriptions/`, doctor and
 *    pharmacist). Widening a list here does not widen anything: the API
 *    refuses, and the sheet says it could not load. The list exists so a role
 *    is never offered a button that would only fail.
 * 2. **`needs` is honesty about context.** A document that has no record to
 *    print is not offered. That is what stops "Print lab request" appearing
 *    on a chart with no laboratory order behind it.
 * 3. **A document is registered once.** The lab report is the same component
 *    whether it is printed from the bench, the chart or the overview.
 */

// The desks that hand a patient a piece of paper about themselves rather than
// about their treatment. A patient card is reception's document; a doctor
// reprinting one at the bedside was always somebody else's errand.
const DESK_ROLES = ["reception", ...BILLING_ROLES];
const CLINICAL = ["doctor"];
const LAB = ["laboratory"];
const IMAGING = ["radiology"];
const EYE = ["optometrist", "ophthalmologist"];
const WARD = ["ward_manager", "doctor", "nurse"];

export const DOCUMENTS = {
  patient_card: {
    label: "Patient card",
    roles: DESK_ROLES,
    needs: ["patient"],
    render: (context, onClose) => (
      <PatientCardSheet patient={context.patient} onClose={onClose} />
    ),
  },
  invoice: {
    label: "Invoice",
    description: "What is still owed",
    roles: [...BILLING_ROLES, "pharmacist"],
    needs: ["patientId"],
    render: (context, onClose) => (
      <PatientBillSheet patientId={context.patientId} mode="invoice" onClose={onClose} />
    ),
  },
  statement: {
    label: "Statement",
    description: "The whole account",
    roles: [...BILLING_ROLES, "pharmacist"],
    needs: ["patientId"],
    render: (context, onClose) => (
      <PatientBillSheet patientId={context.patientId} mode="statement" onClose={onClose} />
    ),
  },
  receipt: {
    label: "Receipt",
    roles: [...BILLING_ROLES, "pharmacist"],
    needs: ["patient", "payment"],
    render: (context, onClose) => (
      <ReceiptSheet
        patient={context.patient}
        payment={context.payment}
        balanceAfter={context.balanceAfter}
        onClose={onClose}
      />
    ),
  },

  lab_request: {
    label: "Request form",
    description: "Travels with the specimen",
    // Exactly WORKLIST_ROLES on the backend (laboratory/views.py): the desk
    // books the sample in and bills it, so it prints the form, which carries
    // no result — that is why it is not restricted the way the report is.
    // A nurse is deliberately absent, and the endpoint agrees.
    roles: [...LAB, ...CLINICAL, "reception", "cashier", "accountant"],
    needs: ["orderId"],
    render: (context, onClose) => (
      <LabRequestSheet orderId={context.orderId} onClose={onClose} />
    ),
  },
  lab_report: {
    label: "Laboratory report",
    roles: [...LAB, ...CLINICAL],
    needs: ["orderId"],
    render: (context, onClose) => (
      <LabReportSheet orderId={context.orderId} onClose={onClose} />
    ),
  },

  referral_request: {
    label: "Request form",
    description: "The referral as it was raised",
    roles: [...IMAGING, ...EYE, ...CLINICAL, ...LAB, "nurse", "reception"],
    needs: ["routeId"],
    render: (context, onClose) => (
      <ReferralDocumentSheet routeId={context.routeId} variant="request" onClose={onClose} />
    ),
  },
  referral_report: {
    label: "Report",
    description: "The finding this unit wrote",
    // Reception is deliberately absent: it can print the form it raised, not
    // the clinical answer. The server enforces that as well (see
    // PatientRouteViewSet.document).
    roles: [...IMAGING, ...EYE, ...CLINICAL, ...LAB, "nurse"],
    needs: ["routeId"],
    render: (context, onClose) => (
      <ReferralDocumentSheet routeId={context.routeId} variant="report" onClose={onClose} />
    ),
  },

  prescription: {
    label: "Prescription",
    roles: [...CLINICAL, "pharmacist"],
    needs: ["patientId"],
    render: (context, onClose) => (
      <PrescriptionSheet
        patientId={context.patientId}
        prescriptionIds={context.prescriptionIds}
        onClose={onClose}
      />
    ),
  },
  dispensing_note: {
    label: "Dispensing note",
    description: "What actually left the shelf",
    roles: ["pharmacist", ...CLINICAL],
    needs: ["patientId"],
    render: (context, onClose) => (
      <DispensingSheet
        patientId={context.patientId}
        prescriptionIds={context.prescriptionIds}
        onClose={onClose}
      />
    ),
  },

  admission_slip: {
    label: "Admission slip",
    roles: WARD,
    needs: ["patientId"],
    render: (context, onClose) => (
      <AdmissionSheet
        admission={context.admission}
        patientId={context.patientId}
        onClose={onClose}
      />
    ),
  },

  vitals_record: {
    label: "Observation record",
    description: "Readings and nursing notes",
    roles: ["nurse", ...CLINICAL],
    needs: ["patientId"],
    render: (context, onClose) => (
      <VitalsRecordSheet patientId={context.patientId} onClose={onClose} />
    ),
  },

  clinical_summary: {
    label: "Clinical summary",
    description: "Notes, findings and medication",
    roles: CLINICAL,
    needs: ["patientId"],
    render: (context, onClose) => (
      <ClinicalSummarySheet patientId={context.patientId} onClose={onClose} />
    ),
  },
};

/**
 * The chart's Print button, by the section you are reading.
 *
 * A patient-level document, chosen by the tab: what somebody standing on that
 * tab would actually want on paper. Per-record documents — *this* laboratory
 * order, *this* scan — are printed from the row they belong to, because a
 * header button would have to guess which one you meant.
 *
 * Order matters: the first entry the role may print is what the button does.
 */
const CHART_TAB_DOCUMENTS = {
  overview: ["clinical_summary", "patient_card"],
  record: ["clinical_summary", "patient_card"],
  notes: ["clinical_summary"],
  vitals: ["vitals_record", "clinical_summary"],
  lab: ["clinical_summary"],
  ultrasound: ["clinical_summary"],
  eye: ["clinical_summary"],
  procedure: ["clinical_summary"],
  admission: ["admission_slip", "clinical_summary"],
  // Both roles read this tab and want opposite documents from it: the
  // pharmacy hands over drugs, the doctor writes the script. A tab entry may
  // be a function of the role for exactly this case.
  pharmacy: (role) => (role === "pharmacist"
    ? ["dispensing_note", "prescription"]
    : ["prescription", "dispensing_note"]),
  // The money documents are printed from the billing tab itself, off the
  // charges it already holds — see `PatientBillingTab`. The header offers
  // the identity document instead of a second copy of the same two buttons.
  billing: ["patient_card"],
};

// Tabs that print their own documents from data they already have. The
// header does not fall back to the role's default there, because that is how
// the same invoice ends up on the page twice under two different names.
const TAB_OWNS_ITS_DOCUMENTS = new Set(["billing"]);

// What each role reaches for when the tab has nothing of its own to offer —
// the department's default document, never the patient card by accident.
const ROLE_DEFAULT_DOCUMENTS = {
  reception: ["patient_card", "invoice"],
  cashier: ["invoice", "statement", "patient_card"],
  accountant: ["invoice", "statement", "patient_card"],
  doctor: ["clinical_summary", "prescription"],
  nurse: ["vitals_record"],
  pharmacist: ["dispensing_note", "prescription"],
  ward_manager: ["admission_slip"],
  laboratory: ["lab_request"],
  radiology: ["referral_request"],
  optometrist: ["referral_request"],
  ophthalmologist: ["referral_request"],
  // Admin is every desk at once; the card is the one document that is never
  // wrong to have.
  admin: ["patient_card", "clinical_summary"],
  hospital_admin: ["patient_card", "clinical_summary"],
};

const mayPrint = (role, key) => {
  const entry = DOCUMENTS[key];
  return Boolean(entry) && hasRole({ role }, entry.roles);
};

const hasContext = (key, context) =>
  (DOCUMENTS[key].needs ?? []).every((need) => context?.[need] != null);

/**
 * Turn a list of candidate documents into the ones this person can actually
 * print right now, in order, with no duplicates.
 */
export function resolveDocuments({ role, keys, context }) {
  const seen = new Set();
  return keys.filter((key) => {
    if (seen.has(key) || !mayPrint(role, key) || !hasContext(key, context)) return false;
    seen.add(key);
    return true;
  });
}

/**
 * What the chart's Print button offers on this tab, for this role.
 *
 * The section wins over the role — that is what makes the button contextual —
 * with one deliberate exception. **Reception keeps the patient card.** The
 * front desk's business at a chart is identity, the card is the document that
 * was there before this change, and the bill it also prints is one click away
 * in the menu and already has its own buttons on the billing counter. Every
 * other role gets its section's document first.
 */
export function chartDocuments({ role, tab, context }) {
  const entry = CHART_TAB_DOCUMENTS[tab];
  const tabKeys = typeof entry === "function" ? entry(role) : (entry ?? []);
  const roleKeys = TAB_OWNS_ITS_DOCUMENTS.has(tab) ? [] : (ROLE_DEFAULT_DOCUMENTS[role] ?? []);
  return resolveDocuments({
    role,
    keys: role === "reception" ? [...roleKeys, ...tabKeys] : [...tabKeys, ...roleKeys],
    context,
  });
}

/**
 * One button that prints the right thing, with the rest of what this role may
 * print behind a caret.
 *
 * Not a row of buttons: three buttons that all say "Print" is how somebody
 * ends up handing a patient a registration card instead of their invoice. The
 * first document is the contextual one and the button is labelled with its
 * name, so what is about to come out of the printer is readable before it is
 * pressed.
 */
export function PrintButton({
  documents, context, role, variant = "secondary", size = "md", label, className = "",
}) {
  const keys = resolveDocuments({ role, keys: documents ?? [], context });
  const [open, setOpen] = useState(false);
  const [printing, setPrinting] = useState(null);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (!boxRef.current?.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (keys.length === 0) return null;

  const primary = keys[0];
  const others = keys.slice(1);

  return (
    <div ref={boxRef} className={`relative inline-flex ${className}`}>
      <Button
        variant={variant}
        size={size}
        onClick={() => setPrinting(primary)}
        className={others.length > 0 ? "rounded-r-none" : undefined}
      >
        <Icon name="print" className="h-4 w-4" aria-hidden="true" />
        {label ?? DOCUMENTS[primary].label}
      </Button>

      {others.length > 0 && (
        <Button
          variant={variant}
          size={size}
          aria-label="Other documents"
          aria-expanded={open}
          title="Other documents"
          onClick={() => setOpen((value) => !value)}
          className="-ml-px rounded-l-none px-2"
        >
          <Icon name="chevronDown" className="h-4 w-4" aria-hidden="true" />
        </Button>
      )}

      {open && others.length > 0 && (
        <div className="absolute right-0 top-full z-20 mt-1 w-60 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
          {others.map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => { setOpen(false); setPrinting(key); }}
              className="block w-full px-3 py-2 text-left transition hover:bg-slate-50"
            >
              <span className="block text-sm font-medium text-slate-800">
                {DOCUMENTS[key].label}
              </span>
              {DOCUMENTS[key].description && (
                <span className="block text-xs text-slate-600">{DOCUMENTS[key].description}</span>
              )}
            </button>
          ))}
        </div>
      )}

      {printing && DOCUMENTS[printing].render(context, () => setPrinting(null))}
    </div>
  );
}
