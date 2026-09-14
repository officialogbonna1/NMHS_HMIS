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

// Which key holds the *browser* identity, in one place — the mirror of
// `patientNumber()` above.
//
// A patient record sends it as `uuid`; a row that merely mentions a patient (a
// queue entry, a ledger, a recorded reading, an adjustment) sends it as
// `patient_uuid`. This is the value that belongs in a `/patients/<…>` URL.
//
// It is deliberately NOT the integer `id`: that is the database's name for the
// row and it still drives every `?patient=` filter and FK write body, which is
// why both are carried side by side rather than one replacing the other.
//
// Holding a UUID is not permission to read a chart — the API applies the same
// role and assignment filter to it as to the integer pk.
export function patientUuidOf(row) {
  if (!row) return "";
  return row.uuid ?? row.patient_uuid ?? "";
}

// True when a route parameter is a UUID rather than the integer pk an older
// bookmark or an already-sent notification still carries.
export function looksLikeUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    String(value ?? ""));
}
