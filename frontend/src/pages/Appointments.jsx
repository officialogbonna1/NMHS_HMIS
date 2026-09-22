import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import api from "../api/client";
import { errorCode, readError } from "../api/errors";
import { useAuth } from "../auth/AuthContext.jsx";
import PatientPicker from "../components/PatientPicker.jsx";
import { paymentState } from "../components/billingStatus";
import {
  Alert, Badge, Button, Card, CardBody, EmptyState, ErrorState, Field, FilterBar,
  Page, PageHeader, Section, Select, SkeletonRows, StatusDot, Table, TableWrap,
  Td, Th, THead, Tr, Input, naira,
} from "../components/ui.jsx";
import { useToast } from "../components/Toaster.jsx";
import { SelectedServices } from "../components/ServicePicker.jsx";
import {
  afterDepartmentChange, afterModeChange, afterServiceToggle, allowsGeneral, asChips,
  bookingBody, canQueue, departmentsOf, feeFor, GENERAL, providersForAll, servicesFor,
  summaryRows, toggleService,
} from "../components/appointmentBooking";

// Still a queue, not a diary.
//
// Reception picks the patient, the unit, what they are being seen for and who
// is seeing them; the provider decides *when*, and their own accept / start /
// end transitions are what stamp the times. Nothing on this page books a slot,
// and there is deliberately nowhere to type one.
//
// What the page gained is a destination: the same queue now carries an eye
// clinic appointment or a scan as readily as a consultation, because the
// service, its department, its fee and the roles that may be named on it all
// come from configuration that already existed — one catalogue, one department
// registry, one set of roles (`appointments/booking.py`).

const STATUS_TONE = {
  queued: "warning", accepted: "info", in_progress: "brand",
  completed: "success", cancelled: "neutral",
};

const STATUS_LABEL = {
  queued: "Queued", accepted: "Accepted", in_progress: "In progress",
  completed: "Completed", cancelled: "Cancelled",
};

const TRANSITION_TOAST = {
  accept: "Appointment accepted",
  cancel: "Appointment cancelled",
  start: "Appointment started",
  end: "Appointment completed",
};

// Appointments are a queue, not a diary: start_time/end_time are stamped by
// the provider's start/end actions, so an entry that hasn't been started yet
// is described by when it was raised.
function appointmentTiming(a) {
  if (a.end_time) return `Seen ${new Date(a.end_time).toLocaleString()}`;
  if (a.start_time) return `Started ${new Date(a.start_time).toLocaleTimeString()}`;
  return `Queued ${new Date(a.created_at).toLocaleString()}`;
}

const BOOKING_ROLES = ["reception", "admin", "hospital_admin"];

export default function Appointments() {
  const { user } = useAuth();
  const { showToast } = useToast();
  const canBook = BOOKING_ROLES.includes(user?.role);
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ status: "", department: "", search: "" });

  const { data: appointments, isLoading, isError, refetch } = useQuery({
    queryKey: ["appointments"],
    queryFn: () => api.get("/appointments/").then((r) => r.data.results ?? r.data),
  });

  const transition = useMutation({
    mutationFn: ({ id, action }) => api.post(`/appointments/${id}/${action}/`),
    onSuccess: (response, { action }) => {
      queryClient.invalidateQueries({ queryKey: ["appointments"] });
      showToast({ title: TRANSITION_TOAST[action], message: response.data.patient_name });
    },
    onError: (error) => {
      showToast({
        title: "Could not update appointment",
        message: readError(error, "Please try again."),
        tone: "error",
      });
    },
  });

  const rows = appointments ?? [];
  // The queue's own filters — department, status and a name or number. Read
  // off rows that are already loaded, because this is a working queue rather
  // than a report: nothing here aggregates, and nothing here is a second
  // reporting page.
  const departments = useMemo(() => {
    const seen = new Map();
    for (const row of rows) {
      if (row.department && row.department_name) seen.set(row.department, row.department_name);
    }
    return [...seen.entries()];
  }, [rows]);

  const visible = useMemo(() => {
    const term = filters.search.trim().toLowerCase();
    return rows.filter((row) => {
      if (filters.status && row.status !== filters.status) return false;
      if (filters.department && String(row.department) !== filters.department) return false;
      if (!term) return true;
      return [row.patient_name, row.patient_number, row.service_name, row.provider_name]
        .some((value) => value?.toLowerCase().includes(term));
    });
  }, [rows, filters]);

  return (
    <Page className="space-y-8">
      <PageHeader
        className="mb-0"
        icon="calendar"
        title="Appointments"
        subtitle="A queue, not a diary — reception picks the patient, the unit and the provider, and the provider's own transitions record the times."
      />

      {canBook && (
        <BookAppointmentForm
          onDone={() => queryClient.invalidateQueries({ queryKey: ["appointments"] })}
        />
      )}

      <Section
        title={canBook ? "All appointments" : "My appointments"}
        description={`${visible.length} of ${rows.length} shown`}
      >
        <FilterBar columns={3}>
          <Field label="Find in the queue">
            <Input
              value={filters.search}
              onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
              placeholder="Patient, number, service or provider…"
            />
          </Field>
          <Field label="Filter by department">
            <Select
              value={filters.department}
              onChange={(e) => setFilters((f) => ({ ...f, department: e.target.value }))}
            >
              <option value="">All departments</option>
              {departments.map(([id, name]) => (
                <option key={id} value={String(id)}>{name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Filter by status">
            <Select
              value={filters.status}
              onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
            >
              <option value="">Any status</option>
              {Object.entries(STATUS_LABEL).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </Select>
          </Field>
        </FilterBar>

        {isLoading && <SkeletonRows rows={4} />}
        {isError && <ErrorState description="Could not load the queue." onRetry={refetch} />}
        {!isLoading && !isError && visible.length === 0 && (
          <EmptyState
            icon="calendar"
            title={rows.length ? "Nothing matches those filters" : "No appointments yet"}
            description={rows.length
              ? "Clear the search or pick another department."
              : "Queue a patient above and they appear here straight away."}
          />
        )}

        {visible.length > 0 && (
          <TableWrap>
            <Table>
              <THead>
                <Tr>
                  <Th>Patient</Th>
                  <Th>Department</Th>
                  <Th>Service</Th>
                  <Th>Provider</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Fee</Th>
                  <Th>Payment</Th>
                  <Th>Queued</Th>
                  <Th />
                </Tr>
              </THead>
              <tbody>
                {visible.map((appointment) => (
                  <QueueRow
                    key={appointment.id}
                    appointment={appointment}
                    user={user}
                    onTransition={(action) => transition.mutate({ id: appointment.id, action })}
                    busy={transition.isPending}
                  />
                ))}
              </tbody>
            </Table>
          </TableWrap>
        )}
      </Section>
    </Page>
  );
}

function QueueRow({ appointment, user, onTransition, busy }) {
  // A fee is not a payment. `paymentState` is the one reading of a service's
  // money every unit in the hospital shows (rule 54), so a queue row can never
  // imply a bill is settled because it has a figure beside it.
  const payment = paymentState(appointment.billing);
  const mine = appointment.provider === user?.id || appointment.doctor === user?.id;
  const isAdmin = ["admin", "hospital_admin"].includes(user?.role);

  return (
    <Tr>
      <Td>
        <span className="block font-medium text-slate-900">{appointment.patient_name}</span>
        <span className="block text-xs tabular-nums text-slate-500">
          {appointment.patient_number}
        </span>
      </Td>
      <Td className="text-slate-700">{appointment.department_name ?? "—"}</Td>
      <Td className="text-slate-700">{appointment.service_name || "General consultation"}</Td>
      <Td className="text-slate-700">{appointment.provider_name ?? "—"}</Td>
      <Td>
        <StatusDot tone={STATUS_TONE[appointment.status] ?? "neutral"}>
          {STATUS_LABEL[appointment.status] ?? appointment.status}
        </StatusDot>
      </Td>
      <Td className="text-right tabular-nums text-slate-800">
        {payment.billed ? naira(payment.amount) : "—"}
      </Td>
      <Td>
        {payment.billed
          ? <Badge tone={payment.tone}>{payment.label}</Badge>
          : <span className="text-sm text-slate-500">—</span>}
      </Td>
      <Td className="text-sm text-slate-600">{appointmentTiming(appointment)}</Td>
      <Td>
        {/* The provider works their own row — the same transitions, in the
            same order, that the doctor has always had. An eye doctor or a
            sonographer holding an appointment gets them because the row is
            theirs, not because a role was listed here. */}
        {(mine || isAdmin) && (
          <div className="flex flex-wrap items-center justify-end gap-1">
            {appointment.status === "queued" && (
              <>
                <Button variant="link" size="xs" disabled={busy}
                        onClick={() => onTransition("accept")}>Accept</Button>
                <Button variant="linkDanger" size="xs" disabled={busy}
                        onClick={() => onTransition("cancel")}>Cancel</Button>
              </>
            )}
            {appointment.status === "accepted" && (
              <>
                <Button variant="link" size="xs" disabled={busy}
                        onClick={() => onTransition("start")}>Start</Button>
                <Button variant="linkDanger" size="xs" disabled={busy}
                        onClick={() => onTransition("cancel")}>Cancel</Button>
              </>
            )}
            {appointment.status === "in_progress" && (
              <Button variant="link" size="xs" disabled={busy}
                      onClick={() => onTransition("end")}>End</Button>
            )}
          </div>
        )}
      </Td>
    </Tr>
  );
}

/* ============================================================== booking */

const EMPTY = {
  patient: null, departmentId: "", serviceKey: "", services: [], providerId: "", reason: "",
};

function BookAppointmentForm({ onDone }) {
  const { showToast } = useToast();
  const [selection, setSelection] = useState(EMPTY);
  const [error, setError] = useState(null);

  // One read: the units that take appointments, what each offers at the
  // catalogue's own price, and who may be named on it. The form holds no copy
  // of any of those lists — ticking a service in Django admin is the whole of
  // adding one.
  const { data: options, isLoading, isError, refetch } = useQuery({
    queryKey: ["appointment-booking-options"],
    queryFn: () => api.get("/appointments/booking-options/").then((r) => r.data),
    staleTime: 60000,
  });

  const services = servicesFor(options, selection.departmentId);
  const selected = selection.serviceKey === GENERAL ? [] : selection.services;
  const providers = providersForAll(selected, options, selection.serviceKey);
  const fee = feeFor(selected);
  const chose = selection.serviceKey === GENERAL || selected.length > 0;
  // The fast path: reception picks Consultation and goes straight to the
  // doctor. No service, no fee, no charge — the booking this page has always
  // made, kept one click away rather than behind a service nobody needs to
  // name (and a fee nobody asked to raise).
  const general = allowsGeneral(options, selection.departmentId);

  const book = useMutation({
    // Identities only. The server prices the service from the catalogue,
    // resolves the department from it and raises the charge itself.
    mutationFn: () => api.post("/appointments/", bookingBody(selection)),
    onSuccess: (response) => {
      setSelection(EMPTY);
      setError(null);
      onDone();
      const booked = response.data;
      showToast({
        title: "Appointment queued",
        message: `${booked.patient_name} · ${booked.service_name || "Consultation"}`
          + ` with ${booked.provider_name}`,
      });
    },
    onError: (err) => setError({ code: errorCode(err), message: readError(err, "Could not queue this appointment.") }),
  });

  const ready = canQueue(selection);

  return (
    <Card aria-label="Queue an appointment">
      <CardBody className="space-y-5">
        <div>
          <h2 className="font-semibold text-slate-900">Queue an appointment</h2>
          <p className="mt-0.5 text-sm text-slate-600">
            The provider is notified straight away and accepts, starts and ends the visit from
            their side — there is no time to set here.
          </p>
        </div>

        {isLoading && <SkeletonRows rows={3} />}
        {isError && (
          <ErrorState
            title="Could not load the booking options"
            description="The departments and services could not be read."
            onRetry={refetch}
          />
        )}

        {!isLoading && !isError && departmentsOf(options).length === 0 && (
          <Alert tone="warning" title="No appointment departments available">
            There are currently no departments available for booking.
          </Alert>
        )}

        {!isLoading && !isError && (
          <div className="space-y-5">
            <Step number={1} label="Patient">
              <PatientPicker
                value={selection.patient}
                onChange={(patient) => setSelection((s) => ({ ...s, patient }))}
                // Reception works from whatever the patient can tell them, so
                // the box says so. The search is `/patients/`'s own — name,
                // hospital number and phone — and a returning patient is
                // found rather than registered again.
                placeholder="Search by name, hospital number or phone…"
              />
              {selection.patient && <PatientContext patient={selection.patient} />}
            </Step>

            {selection.patient && departmentsOf(options).length > 0 && (
              <Step number={2} label="Department">
                <Select
                  aria-label="Department"
                  value={selection.departmentId}
                  onChange={(e) => setSelection((s) => afterDepartmentChange(s, e.target.value))}
                >
                  <option value="">Select a department</option>
                  {departmentsOf(options).map((department) => (
                    <option key={department.id} value={String(department.id)}>
                      {department.name}
                    </option>
                  ))}
                </Select>
              </Step>
            )}

            {selection.departmentId && (
              <Step number={3} label="Appointment service">
                {general && (
                  <div className="mb-3">
                    <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-slate-200 bg-white p-3 hover:bg-slate-50">
                      <input
                        type="checkbox"
                        aria-label="General consultation"
                        checked={selection.serviceKey === GENERAL}
                        onChange={() => setSelection((s) =>
                          afterModeChange(s, s.serviceKey === GENERAL ? "" : GENERAL))}
                        className="mt-0.5 h-5 w-5 shrink-0 rounded border-slate-400 text-brand-600 focus:ring-brand-500"
                      />
                      <span className="min-w-0">
                        <span className="block text-sm font-medium text-slate-900">
                          General consultation — no charge
                        </span>
                        <span className="block text-xs text-slate-600">
                          The booking reception has always made: no service named, no bill raised.
                        </span>
                      </span>
                    </label>
                  </div>
                )}

                {selection.serviceKey !== GENERAL && (
                  services.length === 0 ? (
                    <Alert tone="warning" title="Nothing bookable in this department">
                      No service here has been marked bookable as an appointment. An administrator
                      ticks one on the Billing Catalog; nothing is invented for a department that
                      has none.
                    </Alert>
                  ) : (
                    <ServiceChoices
                      services={services}
                      selected={selected}
                      onToggle={(service) =>
                        setSelection((s) => afterServiceToggle(options, s, service))}
                      onClear={() => setSelection((s) => ({ ...s, services: [], providerId: "" }))}
                    />
                  )
                )}
              </Step>
            )}

            {chose && (
              <Step number={4} label={selection.serviceKey === GENERAL ? "Doctor" : "Provider"}>
                {providers.length === 0 ? (
                  <Alert tone="warning" title="No eligible provider is available">
                    Nobody on the staff list can be booked for this service yet. An administrator
                    assigns somebody with the right role, or adds them to the department.
                  </Alert>
                ) : (
                  <Select
                    // The label follows the step: on the general path this is
                    // the doctor, which is the word reception has always used.
                    aria-label={selection.serviceKey === GENERAL ? "Doctor" : "Provider"}
                    value={selection.providerId}
                    onChange={(e) => setSelection((s) => ({ ...s, providerId: e.target.value }))}
                  >
                    <option value="">
                      {selection.serviceKey === GENERAL ? "Select a doctor" : "Select a provider"}
                    </option>
                    {providers.map((person) => (
                      <option key={person.id} value={String(person.id)}>
                        {person.name} — {person.role_label}
                      </option>
                    ))}
                  </Select>
                )}
              </Step>
            )}

            {selection.patient && (
              <Step number={5} label="Reason">
                <Input
                  aria-label="Reason"
                  value={selection.reason}
                  onChange={(e) => setSelection((s) => ({ ...s, reason: e.target.value }))}
                  placeholder="Reason for visit (optional)"
                />
              </Step>
            )}

            {selected.length > 0 && <FeeNotice fee={fee} />}

            {ready && <BookingSummary options={options} selection={selection} />}

            {error && (
              <Alert tone="danger" title={TITLE_FOR[error.code] ?? "Could not queue this appointment"}>
                {error.message}
              </Alert>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={() => book.mutate()} disabled={!ready || book.isPending}>
                {book.isPending ? "Queueing…" : "Queue appointment"}
              </Button>
              {(selection.patient || selection.departmentId) && (
                <Button variant="linkMuted" size="xs"
                        onClick={() => { setSelection(EMPTY); setError(null); }}>
                  Start over
                </Button>
              )}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/**
 * The services a department offers, as rows you tick.
 *
 * A checkbox per service with its own price, a selected state you can see at a
 * glance, and — from `SelectedServices`, the component the billing counter and
 * the pharmacy till already render — the chips, the count, the running total
 * and Clear all. One implementation of "what is on this ticket", not a third.
 *
 * The prices here are for the person reading them; `POST /appointments/`
 * re-resolves every key against the catalogue and prices the booking itself.
 */
function ServiceChoices({ services, selected, onToggle, onClear }) {
  const picked = (service) => selected.some((s) => s.key === service.key);
  return (
    <div className="min-w-0">
      <div className="max-h-72 min-w-0 divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-200">
        {services.map((service) => {
          const on = picked(service);
          return (
            <label
              key={service.key}
              className={`flex w-full min-w-0 cursor-pointer items-center gap-3 px-3 py-3 text-sm ${
                on ? "bg-brand-50 text-brand-900" : "text-slate-800 hover:bg-slate-50"
              }`}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => onToggle(service)}
                className="h-5 w-5 shrink-0 rounded border-slate-400 text-brand-600 focus:ring-brand-500"
              />
              <span className="min-w-0 flex-1 truncate font-medium">{service.name}</span>
              {Number(service.fee) > 0
                ? <span className="shrink-0 font-semibold tabular-nums text-slate-800">
                    {naira(Number(service.fee))}
                  </span>
                : <span className="shrink-0 text-xs text-slate-600">No charge</span>}
            </label>
          );
        })}
      </div>
      <SelectedServices
        selected={asChips(selected)}
        onToggle={(chip) => onToggle(services.find((s) => s.key === chip.key) ?? chip)}
        onClear={onClear}
      />
    </div>
  );
}

// The refusals the server can answer with, each its own sentence because they
// are different conversations: one is a configuration problem, one is a
// staffing problem, and one is a patient already in a queue.
const TITLE_FOR = {
  // The department was closed for appointments after this screen loaded.
  department_not_available: "That department is not taking appointments",
  service_not_bookable: "That service cannot be booked",
  services_span_departments: "One appointment goes to one department",
  provider_not_eligible: "That provider cannot take this appointment",
  already_queued: "This patient is already in that queue",
};

function Step({ number, label, children }) {
  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white">
          {number}
        </span>
        <span className="text-sm font-semibold text-slate-800">{label}</span>
      </div>
      <div className="pl-8">{children}</div>
    </div>
  );
}

/**
 * Enough to confirm the right person was picked, and no more.
 *
 * Name, hospital number and phone are the identifiers reception already reads
 * on every screen; nothing clinical belongs on a booking form.
 */
function PatientContext({ patient }) {
  const { data: history } = useQuery({
    queryKey: ["appointments", "for-patient", patient.id],
    queryFn: () => api.get("/appointments/", { params: { patient: patient.id } })
      .then((r) => r.data.results ?? r.data),
    staleTime: 30000,
  });
  const previous = (history ?? []).filter((row) => row.status === "completed");
  const returning = (history ?? []).length > 0;
  const last = previous[previous.length - 1];

  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-900">
          {[patient.last_name, patient.first_name].filter(Boolean).join(", ")}
        </span>
        <span className="tabular-nums text-sm text-slate-600">{patient.patient_number}</span>
        <Badge tone={returning ? "info" : "success"}>
          {returning ? "Returning patient" : "First appointment"}
        </Badge>
      </div>
      <p className="mt-1 text-sm text-slate-600">
        {patient.phone_number || "No phone number on file"}
        {last && ` · last seen ${new Date(last.end_time ?? last.created_at).toLocaleDateString()}`}
        {last?.department_name && ` at ${last.department_name}`}
      </p>
    </div>
  );
}

/**
 * What it costs, and what the hospital does about it — said separately on
 * purpose. A fee is not a payment and queueing does not take money: the bill
 * is raised with the appointment and settled at the counter like any other.
 */
function FeeNotice({ fee }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-semibold text-slate-700">
          {fee.count > 1 ? `Appointment fee · ${fee.count} services` : "Appointment fee"}
        </span>
        <span className="text-lg font-semibold tabular-nums text-slate-900">{fee.label}</span>
      </div>
      <p className="mt-1 text-sm text-slate-600">{fee.note}</p>
    </div>
  );
}

function BookingSummary({ options, selection }) {
  const rows = summaryRows(options, selection);
  return (
    <div className="rounded-xl border border-brand-200 bg-brand-50/60 p-4">
      <p className="text-sm font-semibold text-slate-800">Appointment summary</p>
      <dl className="mt-2 grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {rows.map((row) => (
          <div key={row.label} className="min-w-0">
            <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {row.label}
            </dt>
            <dd className="truncate text-sm font-medium text-slate-900">{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
