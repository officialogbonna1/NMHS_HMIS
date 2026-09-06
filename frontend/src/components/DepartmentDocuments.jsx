import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { patientNumber } from "./patientIdentity.js";
import PrintSheet, {
  SheetHeader, PatientBlock, SheetSection, SheetFooter, SheetStatus,
  Stamp, WriteInLines, Field, money,
} from "./PrintSheet.jsx";

// The documents each department actually hands over, signs or files.
//
// Reception's three (card, invoice, receipt) live in `PrintDocuments.jsx`.
// These are the rest of the hospital's: the request form that travels with a
// specimen, the imaging referral, the script, the dispensing note, the
// admission slip, the observation record and the clinician's summary.
//
// Two rules hold them together.
//
// **Every sheet fetches its own data, from the endpoint its own role already
// passes.** A pharmacist's dispensing note reads `/prescriptions/`; a
// doctor's summary reads `/patients/<id>/overview/`, which is doctor-and-
// admin only. Nothing here widens a permission — a role that cannot read the
// record cannot print the document, and the API is what refuses.
//
// **Nothing is printed that the department has no business reading.** The
// laboratory request form carries no result values, and the referral
// document's finding is omitted by the server for the front desk.

const dateTime = (value) => (value ? new Date(value).toLocaleString() : "—");
const dateOnly = (value) => (value ? new Date(value).toLocaleDateString() : "—");

/* ============================================================ laboratory */

/**
 * The laboratory request form — the sheet that goes with the specimen.
 *
 * What was asked for, by whom, on what sample, and what it costs. Never a
 * result: that is `LabReportSheet`, and the two exist separately because the
 * form is read by the desk that books the sample in and the report is read by
 * the clinician who ordered it.
 */
export function LabRequestSheet({ orderId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["lab-request-form", orderId],
    queryFn: () => api.get(`/lab-orders/${orderId}/request-form/`).then((r) => r.data),
  });

  if (isLoading || isError || !data) {
    return <SheetStatus title="Laboratory request" isError={isError} onClose={onClose} />;
  }

  const { order, patient, tests, billing } = data;
  return (
    <PrintSheet title={`Laboratory request — ${order.order_number}`} onClose={onClose}>
      <SheetHeader
        documentTitle="Laboratory Request Form"
        reference={order.order_number}
        date={dateTime(order.created_at)}
      />
      <PatientBlock patient={patient} />

      <SheetSection title="Request">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Requested by" value={order.requested_by || "—"} strong />
          <Field label="Priority" value={order.priority} strong />
          <Field label="Report to" value={order.report_to || "—"} />
          <Field label="Order status" value={order.status} />
          <Field label="Specimen ID" value={order.specimen_id || "—"} />
          <Field label="Collected" value={dateTime(order.specimen_collected_at)} />
        </div>
        {order.clinical_notes && (
          <p className="mt-3 border-l-2 border-slate-800 pl-3 text-sm text-slate-900">
            <span className="font-semibold">Clinical details: </span>{order.clinical_notes}
          </p>
        )}
      </SheetSection>

      <SheetSection title="Tests requested">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-400 text-left text-xs uppercase tracking-wide text-slate-700">
              <th className="py-1 pr-3 font-semibold">Test</th>
              <th className="py-1 pr-3 font-semibold">Specimen</th>
              <th className="py-1 pr-3 font-semibold">Container</th>
              <th className="py-1 pr-3 font-semibold">Status</th>
              <th className="py-1 text-right font-semibold">Fee</th>
            </tr>
          </thead>
          <tbody>
            {tests.map((test) => (
              <tr key={test.id} className="border-b border-slate-200 last:border-0">
                <td className="py-1.5 pr-3 text-slate-900">
                  <span className="font-medium">{test.name}</span>
                  {test.category && (
                    <span className="block text-xs text-slate-700">{test.category}</span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-slate-900">{test.specimen_type || "—"}</td>
                <td className="py-1.5 pr-3 text-slate-900">{test.container || "—"}</td>
                <td className="py-1.5 pr-3 text-slate-900">{test.status}</td>
                <td className="py-1.5 text-right text-slate-900">{money(test.price)}</td>
              </tr>
            ))}
            {tests.length === 0 && (
              <tr><td colSpan={5} className="py-4 text-center text-slate-700">
                No tests on this request.
              </td></tr>
            )}
          </tbody>
        </table>

        {/* The money is the cash desk's, printed here only so the sheet the
            patient carries says what is owed. The bench never acts on it. */}
        {billing?.billed && (
          <div className="mt-3 ml-auto w-64 text-sm">
            <Row label="Total" value={money(billing.total)} />
            <Row label="Paid" value={money(billing.paid)} />
            <div className="mt-1 flex justify-between border-t-2 border-slate-800 pt-1 font-bold">
              <span>Outstanding</span>
              <span>{money(billing.outstanding)}</span>
            </div>
          </div>
        )}
      </SheetSection>

      <SheetSection title="For laboratory use">
        <div className="grid grid-cols-2 gap-x-8 gap-y-6 text-sm">
          <div className="border-b border-slate-400 pb-1 text-slate-700">Sample received (date / time)</div>
          <div className="border-b border-slate-400 pb-1 text-slate-700">Received by</div>
          <div className="border-b border-slate-400 pb-1 text-slate-700">Condition of sample</div>
          <div className="border-b border-slate-400 pb-1 text-slate-700">Bench / analyser</div>
        </div>
      </SheetSection>

      <SheetFooter
        signatory="Requesting clinician"
        note="This form travels with the specimen. Results are reported to the requesting clinician named above."
      />
    </PrintSheet>
  );
}

/* ================================= ultrasound / eye clinic / procedures */

// What each unit's paperwork is called. A request form headed "Patient
// Route" is a document nobody in a hospital recognises.
const REFERRAL_TITLES = {
  ultrasound: { request: "Imaging Request Form", report: "Ultrasound Report", unit: "Ultrasound / Imaging" },
  eye: { request: "Eye Clinic Request Form", report: "Eye Clinic Report", unit: "Eye Clinic" },
  procedure: { request: "Procedure Request Form", report: "Procedure Record", unit: "Procedure" },
  laboratory: { request: "Laboratory Referral", report: "Laboratory Referral Report", unit: "Laboratory" },
  vitals: { request: "Nursing Request", report: "Nursing Record", unit: "Nursing" },
  consultation: { request: "Consultation Request", report: "Consultation Record", unit: "Consultation" },
};

const referralTitles = (purpose, label) =>
  REFERRAL_TITLES[purpose] ?? { request: `${label} Request Form`, report: `${label} Report`, unit: label };

/**
 * One component, two documents, one payload — the referral form and the
 * report that answers it.
 *
 * They are two halves of the same sheet, so they are built from the same
 * fetch: a form that disagrees with the report stapled to it is worse than
 * no form. `variant` decides which half prints; "report" falls back to the
 * request when the unit has not written its finding yet, because a blank
 * report is not a document.
 *
 * The server decides whether `result` is in the payload at all — reception
 * raises referrals and can print the form, but a scan report is clinical.
 */
export function ReferralDocumentSheet({ routeId, variant = "request", onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["route-document", routeId],
    queryFn: () => api.get(`/patient-routes/${routeId}/document/`).then((r) => r.data),
  });

  if (isLoading || isError || !data) {
    return <SheetStatus title="Departmental document" isError={isError} onClose={onClose} />;
  }

  const { route, patient, result } = data;
  const titles = referralTitles(route.purpose, route.purpose_label);
  const isReport = variant === "report" && Boolean(result);
  const documentTitle = isReport ? titles.report : titles.request;

  return (
    <PrintSheet title={`${documentTitle} — ${patientNumber(patient)}`} onClose={onClose}>
      <SheetHeader
        documentTitle={documentTitle}
        reference={route.reference}
        date={dateTime(isReport ? result.recorded_at : route.created_at)}
      />

      {/* A report printed before the unit closed the work is provisional in
          exactly the way an unverified laboratory result is. */}
      {isReport && route.status !== "Completed" && <Stamp>Provisional — work not yet closed</Stamp>}

      <PatientBlock patient={patient} />

      <SheetSection title="Referral">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Unit" value={titles.unit} strong />
          <Field label="Priority" value={route.priority} strong />
          <Field label="Requested by" value={route.routed_by || "—"} />
          <Field label="Requested" value={dateTime(route.created_at)} />
          <Field label="Department" value={route.department} />
          <Field label="Seen by" value={route.assigned_to || "Not yet claimed"} />
        </div>
        <div className="mt-3 border-l-2 border-slate-800 pl-3 text-sm text-slate-900">
          <p className="font-semibold">What was asked for</p>
          <p className="whitespace-pre-wrap">{route.notes || "No clinical note was given."}</p>
        </div>
      </SheetSection>

      {isReport ? (
        <SheetSection title="Findings" note={result.title || undefined}>
          <p className="whitespace-pre-wrap text-sm text-slate-900">
            {result.text || "The report is attached to this patient's record as a document."}
          </p>
          {result.file_name && (
            <p className="mt-3 text-sm text-slate-700">
              Report document on file: <span className="font-medium">{result.file_name}</span>
            </p>
          )}
          <p className="mt-4 text-sm text-slate-700">
            Reported by {result.recorded_by || "—"} · {dateTime(result.recorded_at)}
          </p>
        </SheetSection>
      ) : (
        <SheetSection title="Findings">
          {/* The request form exists to be written on before it is typed
              up — the unit works from paper at the couch and the machine. */}
          <WriteInLines rows={6} />
        </SheetSection>
      )}

      <SheetFooter
        signatory={isReport ? "Reported by" : "Requesting clinician"}
        note={isReport
          ? "This report is filed on the patient's record and has been sent to the requesting clinician."
          : "Return this form with the findings to the requesting clinician."}
      />
    </PrintSheet>
  );
}

/* ============================================================== pharmacy */

function usePrescriptions(patientId) {
  return useQuery({
    queryKey: ["prescriptions", String(patientId)],
    queryFn: () => api.get("/prescriptions/", { params: { patient: patientId, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
    enabled: Boolean(patientId),
  });
}

// A script is one patient, one prescriber, one day. Printing "every
// prescription this patient has ever had" is a chart, not a prescription.
function scopeLines(rows, ids) {
  const lines = ids?.length
    ? rows.filter((row) => ids.includes(row.id))
    : rows.filter((row) => row.status !== "cancelled");
  return [...lines].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
}

/**
 * The doctor's script — what was written, with the directions the patient
 * has to be able to read after they leave the building.
 */
export function PrescriptionSheet({ patientId, prescriptionIds, onClose }) {
  const { data, isLoading, isError } = usePrescriptions(patientId);
  if (isLoading || isError || !data) {
    return <SheetStatus title="Prescription" isError={isError} onClose={onClose} />;
  }

  const lines = scopeLines(data, prescriptionIds);
  const first = lines[0];
  const patient = first ? patientFromPrescription(first) : null;
  const pending = lines.filter((line) => line.status === "pending");

  return (
    <PrintSheet title={`Prescription — ${patientNumber(patient)}`} onClose={onClose}>
      <SheetHeader
        documentTitle="Prescription"
        reference={patientNumber(patient)}
        date={dateTime(first?.created_at)}
      />
      {pending.length > 0 && lines.length === pending.length && (
        <Stamp>Not yet dispensed</Stamp>
      )}
      <PatientBlock patient={patient} />

      <SheetSection title="Prescribed">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-400 text-left text-xs uppercase tracking-wide text-slate-700">
              <th className="py-1 pr-3 font-semibold">Drug</th>
              <th className="py-1 pr-3 font-semibold">Quantity</th>
              <th className="py-1 pr-3 font-semibold">Directions</th>
              <th className="py-1 font-semibold">Status</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.id} className="border-b border-slate-200 last:border-0">
                <td className="py-2 pr-3 font-medium text-slate-900">{line.item_name}</td>
                <td className="py-2 pr-3 text-slate-900">
                  {line.quantity}{line.item_unit ? ` ${line.item_unit}` : ""}
                </td>
                <td className="py-2 pr-3 text-slate-900">{line.dosage_instructions || "—"}</td>
                <td className="py-2 text-slate-900">
                  {line.status === "dispensed" ? `Dispensed ${dateOnly(line.dispensed_at)}` : "Awaiting dispensing"}
                </td>
              </tr>
            ))}
            {lines.length === 0 && (
              <tr><td colSpan={4} className="py-4 text-center text-slate-700">
                Nothing prescribed.
              </td></tr>
            )}
          </tbody>
        </table>
      </SheetSection>

      <SheetFooter
        signatory={first?.doctor_name ? `Prescribed by ${first.doctor_name}` : "Prescribed by"}
        note="Take exactly as directed. Bring this sheet to the pharmacy counter."
      />
    </PrintSheet>
  );
}

/**
 * The pharmacy's own record: what actually left the shelf, at what price,
 * released by whom. Signed by the person who handed it over and the person
 * who took it — which is the whole reason the counter prints anything.
 */
export function DispensingSheet({ patientId, prescriptionIds, onClose }) {
  const { data, isLoading, isError } = usePrescriptions(patientId);
  if (isLoading || isError || !data) {
    return <SheetStatus title="Dispensing note" isError={isError} onClose={onClose} />;
  }

  const lines = scopeLines(data, prescriptionIds).filter((line) => line.status === "dispensed");
  const patient = lines[0] ? patientFromPrescription(lines[0]) : null;
  const total = lines.reduce((sum, line) => sum + Number(line.dispensed_value ?? 0), 0);
  const last = lines[0];

  return (
    <PrintSheet title={`Dispensing note — ${patientNumber(patient)}`} onClose={onClose}>
      <SheetHeader
        documentTitle="Dispensing Note"
        reference={patientNumber(patient)}
        date={dateTime(last?.dispensed_at)}
      />
      <PatientBlock patient={patient} />

      <SheetSection title="Dispensed">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-400 text-left text-xs uppercase tracking-wide text-slate-700">
              <th className="py-1 pr-3 font-semibold">Drug</th>
              <th className="py-1 pr-3 font-semibold">Quantity</th>
              <th className="py-1 pr-3 font-semibold">Directions</th>
              <th className="py-1 pr-3 font-semibold">Released by</th>
              <th className="py-1 text-right font-semibold">Charged</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.id} className="border-b border-slate-200 last:border-0">
                <td className="py-2 pr-3 font-medium text-slate-900">{line.item_name}</td>
                <td className="py-2 pr-3 text-slate-900">
                  {line.quantity}{line.item_unit ? ` ${line.item_unit}` : ""}
                </td>
                <td className="py-2 pr-3 text-slate-900">{line.dosage_instructions || "—"}</td>
                <td className="py-2 pr-3 text-slate-900">
                  {line.dispensed_by_name || "—"}
                  <span className="block text-xs text-slate-700">{dateOnly(line.dispensed_at)}</span>
                </td>
                <td className="py-2 text-right text-slate-900">{money(line.dispensed_value)}</td>
              </tr>
            ))}
            {lines.length === 0 && (
              <tr><td colSpan={5} className="py-4 text-center text-slate-700">
                Nothing has been dispensed to this patient yet.
              </td></tr>
            )}
          </tbody>
        </table>

        {lines.length > 0 && (
          <div className="mt-3 ml-auto flex w-64 justify-between border-t-2 border-slate-800 pt-2 text-base font-bold">
            <span>Total charged</span>
            <span>{money(total)}</span>
          </div>
        )}
      </SheetSection>

      {/* Two signatures, because a dispensing note answers two questions:
          who released the drugs, and who took them away. */}
      <footer className="mt-10 border-t border-slate-300 pt-4 text-sm text-slate-700">
        <p className="mb-6">
          Charges are settled at the pharmacy counter or the cash desk. Keep this note with
          the medicines.
        </p>
        <div className="flex justify-between gap-8">
          <div className="w-56 border-t border-slate-500 pt-1">Dispensed by</div>
          <div className="w-56 border-t border-slate-500 pt-1 text-right">Received by (patient)</div>
        </div>
      </footer>
    </PrintSheet>
  );
}

// The prescription rows carry the patient's identity so a label does not
// cost a second request per script (see PrescriptionSerializer).
function patientFromPrescription(line) {
  return {
    name: line.patient_name,
    patient_number: line.patient_number,
    sex: line.patient_sex,
    age: line.patient_age,
  };
}

/* ========================================================= ward / admission */

/**
 * The admission slip: where the patient is, who put them there and under
 * whom. Ward and bed are the largest thing on it for the same reason they
 * are on the chart's Admission tab — it is what somebody reads before
 * walking to the ward.
 */
export function AdmissionSheet({ admission, patientId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admissions", String(patientId)],
    queryFn: () => api.get("/admissions/", { params: { patient: patientId } })
      .then((r) => r.data.results ?? r.data),
    enabled: !admission && Boolean(patientId),
  });

  if (!admission && (isLoading || isError)) {
    return <SheetStatus title="Admission slip" isError={isError} onClose={onClose} />;
  }

  const rows = data ?? [];
  const record = admission ?? rows.find((row) => row.status === "admitted") ?? rows[0];
  if (!record) {
    return (
      <SheetStatus
        title="Admission slip" isError
        message="This patient has never been admitted, so there is no slip to print."
        onClose={onClose}
      />
    );
  }

  const patient = {
    name: record.patient_name,
    patient_number: record.patient_number,
    sex: record.patient_sex,
    age: record.patient_age,
  };
  const discharged = record.status === "discharged";

  return (
    <PrintSheet title={`Admission slip — ${patientNumber(record)}`} onClose={onClose}>
      <SheetHeader
        documentTitle={discharged ? "Discharge Record" : "Admission Slip"}
        reference={`ADM-${String(record.id).padStart(6, "0")}`}
        date={dateTime(record.admitted_at)}
      />

      {/* The bed is what the sheet is carried to the ward for. */}
      <div className="mb-6 rounded-lg border-2 border-slate-800 px-6 py-4 text-center">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-700">Ward and bed</p>
        <p className="mt-1 text-3xl font-bold tracking-wide text-slate-900">
          {record.ward_name} · Bed {record.bed_number}
        </p>
      </div>

      <PatientBlock patient={patient} />

      <SheetSection title="Admission">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Admitted" value={dateTime(record.admitted_at)} strong />
          <Field label="Under" value={record.attending_doctor_name || "—"} strong />
          <Field label="Admitted by" value={record.admitted_by_name || "—"} />
          <Field
            label="Discharged"
            value={record.discharged_at ? dateTime(record.discharged_at) : "Still on the ward"}
          />
        </div>
        {record.diagnosis && (
          <p className="mt-3 border-l-2 border-slate-800 pl-3 text-sm text-slate-900">
            <span className="font-semibold">Diagnosis on admission: </span>{record.diagnosis}
          </p>
        )}
      </SheetSection>

      <SheetSection title="Ward instructions">
        <WriteInLines rows={5} />
      </SheetSection>

      <SheetFooter
        signatory={discharged ? "Discharged by" : "Admitting officer"}
        note="Keep this slip with the patient's ward notes."
      />
    </PrintSheet>
  );
}

/* ================================================== nursing / observations */

const VITAL_COLUMNS = [
  ["Temp °C", (v) => v.temperature_c],
  ["Pulse", (v) => v.heart_rate],
  ["Resp", (v) => v.respiratory_rate],
  ["BP", (v) => (v.bp_systolic && v.bp_diastolic ? `${v.bp_systolic}/${v.bp_diastolic}` : null)],
  ["SpO₂ %", (v) => v.sao2],
  ["Weight kg", (v) => v.weight_kg],
  ["Height cm", (v) => v.height_cm],
  ["Glucose", (v) => v.glucose_level],
];

/**
 * The nursing observation record — the readings and the notes written
 * alongside them, in the order they were taken.
 *
 * It is the ward's own chart: readings lock on save, so what prints is what
 * was recorded, and a correction shows as a later row rather than an edit.
 */
export function VitalsRecordSheet({ patientId, onClose }) {
  const patientQuery = useQuery({
    queryKey: ["patient", String(patientId)],
    queryFn: () => api.get(`/patients/${patientId}/`).then((r) => r.data),
  });
  const vitalsQuery = useQuery({
    queryKey: ["vitals", patientId],
    queryFn: () => api.get("/vitals/", { params: { patient: patientId, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });
  const notesQuery = useQuery({
    queryKey: ["nursing-notes", patientId],
    queryFn: () => api.get("/nursing-notes/", { params: { patient: patientId, page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
  });

  const isLoading = patientQuery.isLoading || vitalsQuery.isLoading || notesQuery.isLoading;
  const isError = patientQuery.isError || vitalsQuery.isError || notesQuery.isError;
  if (isLoading || isError) {
    return <SheetStatus title="Observation record" isError={isError} onClose={onClose} />;
  }

  const patient = patientQuery.data;
  // The most recent twenty: an observation record is the current stay, not
  // the patient's whole history of readings.
  const readings = (vitalsQuery.data ?? []).slice(0, 20);
  const notes = (notesQuery.data ?? []).slice(0, 10);
  // Only the columns something was actually recorded in — a chart of empty
  // columns is one nobody reads across.
  const columns = VITAL_COLUMNS.filter(([, read]) => readings.some((r) => read(r) != null));

  return (
    <PrintSheet title={`Observation record — ${patientNumber(patient)}`} onClose={onClose}>
      <SheetHeader documentTitle="Nursing Observation Record" reference={patientNumber(patient)} />
      <PatientBlock patient={patient} />

      <SheetSection title="Vital signs">
        {readings.length === 0 ? (
          <p className="text-sm text-slate-700">No readings have been recorded.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-400 text-left text-xs uppercase tracking-wide text-slate-700">
                <th className="py-1 pr-3 font-semibold">Date / time</th>
                {columns.map(([label]) => (
                  <th key={label} className="py-1 pr-3 font-semibold">{label}</th>
                ))}
                <th className="py-1 font-semibold">Recorded by</th>
              </tr>
            </thead>
            <tbody>
              {readings.map((reading) => (
                <tr key={reading.id} className="border-b border-slate-200 last:border-0">
                  <td className="py-1.5 pr-3 text-slate-900">{dateTime(reading.visit_time)}</td>
                  {columns.map(([label, read]) => (
                    <td key={label} className="py-1.5 pr-3 text-slate-900">{read(reading) ?? "—"}</td>
                  ))}
                  <td className="py-1.5 text-slate-900">{reading.recorded_by_name || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </SheetSection>

      {notes.length > 0 && (
        <SheetSection title="Nursing notes">
          {notes.map((note) => (
            <div key={note.id} className="mb-3 break-inside-avoid border-l-2 border-slate-800 pl-3">
              <p className="text-xs text-slate-700">
                {dateTime(note.created_at)} · {note.nurse_name || "—"}
              </p>
              {note.complaint && (
                <p className="text-sm font-semibold text-slate-900">{note.complaint}</p>
              )}
              <p className="whitespace-pre-wrap text-sm text-slate-900">{note.observation}</p>
            </div>
          ))}
        </SheetSection>
      )}

      <SheetFooter signatory="Recorded by" />
    </PrintSheet>
  );
}

/* ============================================================== clinical */

/**
 * The clinician's summary — what a patient is handed when they are referred
 * onward, and what goes in the folder after a consultation.
 *
 * Built from the chart's own `/patients/<id>/overview/`, which is gated on
 * ClinicalRecordAccess: reception and the cash desk cannot fetch it, so they
 * can never print it either. Deliberately a summary and not the chart — the
 * three most recent notes, current medication, the latest reading and what
 * the departments have answered.
 */
export function ClinicalSummarySheet({ patientId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["patient-overview", String(patientId)],
    queryFn: () => api.get(`/patients/${patientId}/overview/`).then((r) => r.data),
  });

  if (isLoading || isError || !data) {
    return <SheetStatus title="Clinical summary" isError={isError} onClose={onClose} />;
  }

  const { patient, alerts, latest_vitals: latest, consultation_notes: notes, medications,
          prescriptions, conditions, visits } = data;
  // The departments' answers, newest visit first — the results half of the
  // summary, which is what makes it worth carrying to another clinician.
  const results = (visits ?? [])
    .flatMap((visit) => visit.routes ?? [])
    .filter((route) => route.result);

  return (
    <PrintSheet title={`Clinical summary — ${patientNumber(patient)}`} onClose={onClose}>
      <SheetHeader documentTitle="Clinical Summary" reference={patientNumber(patient)} />
      <PatientBlock patient={patient} />

      {/* Allergies lead, as they do on the chart: they change what the
          clinician reading this sheet is allowed to give. */}
      {alerts?.length > 0 && (
        <section className="mb-6 border-2 border-slate-800 px-4 py-3">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-700">Allergies</p>
          <ul className="mt-1 text-sm text-slate-900">
            {alerts.map((alert, index) => (
              <li key={index}>
                <span className="font-semibold">{alert.label}</span>
                {alert.detail && ` — ${alert.detail}`}
                {alert.is_dangerous && " (severe)"}
              </li>
            ))}
          </ul>
        </section>
      )}

      {latest && (
        <SheetSection title="Latest observations" note={dateTime(latest.visit_time)}>
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
            {latest.readings.map((reading) => (
              <Field key={reading.label} label={reading.label}
                     value={`${reading.value} ${reading.unit ?? ""}`.trim()} />
            ))}
          </div>
          <p className="mt-2 text-xs text-slate-700">Recorded by {latest.recorded_by || "—"}</p>
        </SheetSection>
      )}

      {conditions?.length > 0 && (
        <SheetSection title="Known conditions">
          <ul className="text-sm text-slate-900">
            {conditions.map((condition) => (
              <li key={condition.id}>
                {condition.name}
                {condition.date_diagnosed && ` — diagnosed ${dateOnly(condition.date_diagnosed)}`}
              </li>
            ))}
          </ul>
        </SheetSection>
      )}

      {notes?.length > 0 && (
        <SheetSection title="Consultation notes">
          {notes.slice(0, 3).map((note) => (
            <div key={note.id} className="mb-4 break-inside-avoid border-l-2 border-slate-800 pl-3">
              <p className="text-xs text-slate-700">
                {dateTime(note.visit_time)} · {note.doctor || "—"}
              </p>
              <p className="text-sm font-semibold text-slate-900">{note.reason_for_visit}</p>
              {note.note_text && (
                <p className="whitespace-pre-wrap text-sm text-slate-900">{note.note_text}</p>
              )}
              {note.diagnosis && (
                <p className="text-sm text-slate-900"><span className="font-semibold">Diagnosis: </span>{note.diagnosis}</p>
              )}
              {note.plan && (
                <p className="text-sm text-slate-900"><span className="font-semibold">Plan: </span>{note.plan}</p>
              )}
            </div>
          ))}
        </SheetSection>
      )}

      {results.length > 0 && (
        <SheetSection title="Investigations reported">
          <ul className="text-sm text-slate-900">
            {results.slice(0, 8).map((route) => (
              <li key={route.id} className="mb-1">
                <span className="font-semibold">{route.purpose}: </span>{route.result}
                <span className="block text-xs text-slate-700">
                  {route.result_by || "—"} · {dateTime(route.result_at)}
                </span>
              </li>
            ))}
          </ul>
        </SheetSection>
      )}

      {(medications?.length > 0 || prescriptions?.length > 0) && (
        <SheetSection title="Medication">
          <ul className="text-sm text-slate-900">
            {medications?.map((medication) => (
              <li key={`m${medication.id}`}>
                {[medication.name, medication.strength, medication.dose_frequency]
                  .filter(Boolean).join(" · ")}
              </li>
            ))}
            {prescriptions?.filter((p) => p.status !== "cancelled").map((prescription) => (
              <li key={`p${prescription.id}`}>
                {prescription.item} ×{prescription.quantity}
                {prescription.dosage_instructions && ` — ${prescription.dosage_instructions}`}
                <span className="text-xs text-slate-700"> ({prescription.status_label})</span>
              </li>
            ))}
          </ul>
        </SheetSection>
      )}

      <SheetFooter
        signatory="Attending clinician"
        note="A summary of the record as it stands today. It supports a clinical judgement; it does not make one."
      />
    </PrintSheet>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between py-0.5">
      <span className="text-slate-700">{label}</span>
      <span className="text-slate-900">{value}</span>
    </div>
  );
}
