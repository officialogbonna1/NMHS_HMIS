// Which key the hospital number lives under, in one place.
//
// A patient carries two identifiers and they do different jobs (see
// `apps/core/identifiers.py` on the backend):
//
//   patient.id / patient.uuid    address a record — never shown to anybody
//   patient.patient_number       `NMHS-P000001`, what a person reads and quotes
//
// `patient_number` is the field. `file_number` is the same string under the
// name it had before staff had numbers of their own, and a patient arriving
// nested inside something else — a queue row, a prescription line, a ledger —
// carries it as `patient_file_number`. All three hold the same value; this is
// which one to look in, not a choice between identifiers.
export function patientNumber(p) {
  if (!p) return "";
  return p.patient_number ?? p.file_number ?? p.patient_file_number ?? "";
}
