import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import api from "../api/client";
import { errorCode, readError } from "../api/errors";
import { useAuth } from "../auth/AuthContext.jsx";
import { MATERNITY_ROLES } from "../auth/roles.js";
import MaternityPatientPicker from "../components/MaternityPatientPicker.jsx";
import {
  RESPONSIBILITIES, assignDepartmentBody, assignPersonBody, assignmentState, canAssign,
  changeLabel, holderOf, staffLabel,
} from "../components/maternityAssignment.js";
import {
  ACTIVE_PREGNANCY, NEW_PATIENT, NO_ACTIVE_PREGNANCY, STATE_TITLE, actionsFor,
  activePregnancy, deskState, encounterBody, formatDate, gravidaPara, lastSeen,
  pregnancySummary, previousPregnancies, visitTypes,
} from "../components/maternityPatient";
import {
  BEFORE_LABOUR, DELIVERED, IN_LABOUR, STAGE_TITLE, babiesOf, birthDescription,
  canRecordDelivery, deliveredLabour, deliveryBody, episodeActions, episodeStage,
  observationSummary, openLabour, whereSheIs,
} from "../components/maternityEpisode";
import { PrintButton } from "../components/printing.jsx";
// The one amendment component. Every record panel below renders *it* rather
// than spelling out its own correction flow.
import { RecordAmendment } from "../components/MaternityAmendment.jsx";
// The chart's own vitals view: the entry form, the history and the nursing
// note beside each reading. Rendered here rather than reimplemented.
import VitalsTab from "./VitalsTab.jsx";
import { useToast } from "../components/Toaster.jsx";
import {
  Alert, Badge, Button, Card, CardBody, EmptyState, ErrorState, Field, Input, MetaStat,
  Page, PageHeader, Section, Select, SkeletonRows, StatusDot, Tab, TabBar, Table,
  TableWrap, Td, Th, THead, Tr,
} from "../components/ui.jsx";

// The maternity desk.
//
// One question is asked before anything else — **is she known, and is she
// pregnant right now?** — and the server answers it (`/maternity/lookup/`).
// Reception is never left to work that out from memory, and because the
// answer decides which buttons exist, "start a new pregnancy" cannot be
// offered to a woman who is already in one.
//
// Everything clinical underneath is the hospital she is already in: triage is
// the existing Vitals station, the assessment is the existing consultation
// note, tests are the existing laboratory and imaging referrals, and the
// money is the existing charge. What this page adds is the thread — which
// pregnancy, and which visit of it.

const STATE_TONE = {
  [NEW_PATIENT]: "warning",
  [ACTIVE_PREGNANCY]: "success",
  [NO_ACTIVE_PREGNANCY]: "info",
};

const ENCOUNTER_TONE = {
  in_progress: "brand", completed: "success", cancelled: "neutral",
};

export default function Maternity() {
  const { user } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [patient, setPatient] = useState(null);
  const [visitTypeId, setVisitTypeId] = useState("");
  const [error, setError] = useState(null);
  const canWork = MATERNITY_ROLES.includes(user?.role) || user?.role?.includes("admin");
  const mayAssign = canAssign(user);

  const lookupKey = ["maternity-lookup", patient?.id ?? null];
  const { data: lookup, isLoading, isError, refetch } = useQuery({
    queryKey: lookupKey,
    queryFn: () => api.get("/maternity/lookup/", { params: { patient: patient.id } })
      .then((r) => r.data),
    enabled: Boolean(patient?.id),
  });

  const state = deskState(lookup);
  const actions = actionsFor(lookup);
  const pregnancy = activePregnancy(lookup);
  const earlier = previousPregnancies(lookup);

  const refresh = () => queryClient.invalidateQueries({ queryKey: lookupKey });

  const startPregnancy = useMutation({
    // Identities only. The server numbers the pregnancy — the next one she
    // has not had — and refuses a second active one outright.
    mutationFn: () => api.post("/pregnancies/", { patient: patient.id }),
    onSuccess: (response) => {
      setError(null);
      refresh();
      showToast({ title: "Pregnancy started",
                  message: `${patient.first_name} · pregnancy #${response.data.number}` });
    },
    onError: (err) => setError({ code: errorCode(err),
                                 message: readError(err, "Could not start a pregnancy.") }),
  });

  const recordVisit = useMutation({
    mutationFn: () => api.post("/maternity-encounters/",
                               encounterBody({ pregnancy, visitTypeId })),
    onSuccess: (response) => {
      setError(null);
      setVisitTypeId("");
      refresh();
      showToast({ title: "Visit recorded",
                  message: `${response.data.visit_type_name} · pregnancy #${pregnancy.number}` });
    },
    onError: (err) => setError({ code: errorCode(err),
                                 message: readError(err, "Could not record this visit.") }),
  });

  return (
    <Page className="space-y-8">
      <PageHeader
        className="mb-0"
        icon="maternity"
        title="Maternity"
        subtitle="Find the mother, continue the pregnancy she is already in, and record today's visit."
      />

      <Card aria-label="Maternity desk">
        <CardBody className="space-y-5">
          <div>
            <h2 className="font-semibold text-slate-900">Find the mother</h2>
            <p className="mt-0.5 text-sm text-slate-600">
              She is registered once. Search by name, hospital number or phone — a returning
              mother is never registered again.
            </p>
          </div>

          {/* Click and the ward's mothers are there — browsing and searching
              are one request against `/maternity/patients/`, which the server
              scopes. The desk can step outside Maternity to find a woman who
              is not on the ward's list yet, which is the first half of
              putting her on it. */}
          <MaternityPatientPicker
            value={patient}
            onChange={(chosen) => { setPatient(chosen); setError(null); setVisitTypeId(""); }}
            allowBeyondMaternity={mayAssign}
          />

          {patient && mayAssign && (
            <MaternityAssignment patient={patient} onChanged={refresh} />
          )}

          {isLoading && patient && <SkeletonRows rows={3} />}
          {isError && (
            <ErrorState title="Could not read her maternity record" onRetry={refetch} />
          )}

          {lookup && state && (
            <div className="space-y-5">
              <PatientBanner lookup={lookup} state={state} pregnancy={pregnancy} />

              {error && (
                <Alert tone="danger" title={TITLE_FOR[error.code] ?? "Could not do that"}>
                  {error.message}
                </Alert>
              )}

              {/* **What happens next is shown to everyone at the desk**, and
                  only the control is gated. Reception meets her at the door,
                  so it has to be able to say "she is already in a pregnancy,
                  this is a follow-up" without being able to write it — the
                  front desk deciding that from memory is the mistake section
                  10 exists to remove. The API refuses the write either way. */}
              {actions.continuePregnancy && (
                <ContinuePregnancy
                  lookup={lookup}
                  canWork={canWork}
                  visitTypeId={visitTypeId}
                  onVisitType={setVisitTypeId}
                  onRecord={() => recordVisit.mutate()}
                  busy={recordVisit.isPending}
                />
              )}

              {actions.startPregnancy && (
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                  <p className="text-sm font-semibold text-slate-800">Start new pregnancy</p>
                  <p className="mt-0.5 text-sm text-slate-700">
                    {lookup.has_history
                      ? "Her previous pregnancies are below and stay exactly as they are."
                      : "She has no pregnancy on record here."}
                  </p>
                  {canWork ? (
                    <Button
                      className="mt-3"
                      onClick={() => startPregnancy.mutate()}
                      disabled={startPregnancy.isPending}
                    >
                      {startPregnancy.isPending ? "Starting…" : "Start new pregnancy"}
                    </Button>
                  ) : (
                    <p className="mt-2 text-sm text-slate-600">
                      A midwife or doctor opens the pregnancy record.
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
        </CardBody>
      </Card>

      {/* **The record is for the people who work it.** The workspace reads the
          visit list, the labour, the partogram, the delivery and the babies,
          and the API refuses every one of them to the front desk — so the
          page does not ask for them either. Reception keeps what it came for:
          who she is, whether she is pregnant, and which of Continue / Start
          applies. */}
      {pregnancy && canWork && (
        <Workspace pregnancy={pregnancy} canWork={canWork} role={user?.role}
                   earlier={earlier} />
      )}
      {pregnancy && !canWork && (
        <Alert tone="info" title="The clinical record is the maternity team's">
          The antenatal course, labour, delivery and newborn records are read by the
          midwives and doctors working this pregnancy.
        </Alert>
      )}
      {/* Read straight off the lookup the desk already receives — "has she
          been here before" is the front desk's own question, and the payload
          carries no clinical commentary for it (`PregnancySerializer`). */}
      {!pregnancy && earlier.length > 0 && <PreviousPregnancies pregnancies={earlier} />}
    </Page>
  );
}

/**
 * **Department, and midwife.** Two rows, because they are two decisions.
 *
 * The department is what puts her on the ward's list, and every maternity
 * nurse works from that list whether or not a name has been typed here. The
 * midwife is who is *responsible* — it narrows nothing, which is why the panel
 * says so rather than leaving "Not assigned" to read as a gap.
 *
 * Changing the midwife never changes the department: transferring a patient
 * out of Maternity is the front desk's routing screen and a decision of its
 * own (`PatientQueue`), so it is deliberately not reachable from here.
 */
function MaternityAssignment({ patient, onChanged }) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [failed, setFailed] = useState(null);

  const key = ["maternity-assignment", patient.id];
  const { data: state } = useQuery({
    queryKey: key,
    queryFn: () => api.get("/maternity/assignment/", { params: { patient: patient.id } })
      .then((r) => r.data),
  });

  const done = (title, message) => {
    setFailed(null);
    queryClient.invalidateQueries({ queryKey: key });
    queryClient.invalidateQueries({ queryKey: ["maternity-patients"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    onChanged?.();
    showToast({ title, message });
  };

  const toDepartment = useMutation({
    mutationFn: () => api.post("/maternity/assignment/", assignDepartmentBody(patient)),
    onSuccess: () => done("Assigned to Maternity",
                          `${patient.name} is on the maternity ward's list.`),
    onError: (err) => setFailed(readError(err, "Could not assign her to Maternity.")),
  });

  const where = assignmentState(state);

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4"
         role="group" aria-label="Maternity assignment">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Department
          </p>
          <h3 className="font-semibold text-slate-900">{where.title}</h3>
        </div>
        {/* The department chip, and only where there is one — repeating the
            heading back at the reader says nothing twice. */}
        {state?.in_maternity && <Badge tone="success">Maternity department</Badge>}
      </div>
      <p className="mt-1 text-sm text-slate-600">{where.detail}</p>

      {failed && <Alert tone="danger" className="mt-3">{failed}</Alert>}

      {!state?.in_maternity ? (
        <div className="mt-3">
          <Button onClick={() => toDepartment.mutate()} disabled={toDepartment.isPending}>
            Assign to Maternity
          </Button>
        </div>
      ) : (
        // **Two rows, two decisions.** Each sends its own field and its own
        // request; there is deliberately no "save both", because the server
        // refuses a body naming the pair and a screen that offered one would
        // be promising something it cannot do.
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {RESPONSIBILITIES.map((role) => (
            <Responsibility
              key={role.field}
              role={role}
              patient={patient}
              state={state}
              onDone={done}
              onError={setFailed}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * One responsibility: who holds it now, and the control that changes it.
 *
 * It says **Not assigned** rather than leaving a blank, because a blank reads
 * as missing data — and the hint says what the field does not do, which is
 * limit who can see her.
 */
function Responsibility({ role, patient, state, onDone, onError }) {
  const holder = holderOf(state, role.field);
  const [chosen, setChosen] = useState("");

  useEffect(() => { setChosen(holder ? String(holder.id) : ""); }, [holder?.id]);

  const { data: staff } = useQuery({
    queryKey: ["maternity-staff", role.roster],
    queryFn: () => api.get("/maternity/staff/", { params: { for: role.roster } })
      .then((r) => r.data),
  });

  const change = useMutation({
    mutationFn: () => api.patch("/maternity/assignment/",
                                assignPersonBody(patient, role.field, chosen)),
    onSuccess: (response) => {
      const now = holderOf(response.data, role.field);
      onDone(`${role.label} updated`,
             now
               ? `${now.name} is responsible for ${patient.name}. `
                 + "She stays on the whole maternity team's list."
               : `Nobody is named. ${patient.name} stays on the whole maternity team's list.`);
    },
    onError: (err) => onError(readError(err, `Could not change the ${role.label.toLowerCase()}.`)),
  });

  return (
    <div className="min-w-0">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {role.label}
      </p>
      <p className="font-medium text-slate-800">{holder?.name ?? role.empty}</p>
      <div className="mt-2 space-y-2">
        <Field label={`Change ${role.label.toLowerCase()}`} hint={role.hint}>
          <Select value={chosen} onChange={(e) => setChosen(e.target.value)}
                  disabled={change.isPending}
                  aria-label={`${role.label} selector`}>
            <option value="">{role.empty}</option>
            {(staff ?? []).map((person) => (
              <option key={person.id} value={person.id}>{staffLabel(person)}</option>
            ))}
          </Select>
        </Field>
        <Button variant="outline" onClick={() => change.mutate()}
                disabled={change.isPending}>
          {changeLabel(state, role.field)}
        </Button>
      </div>
    </div>
  );
}

const TITLE_FOR = {
  pregnancy_already_active: "She is already in a pregnancy",
  pregnancy_not_active: "That pregnancy has ended",
  visit_type_not_available: "That clinic is no longer offered",
};

/**
 * Who she is and where she stands, in the three states the desk can be in —
 * so nobody has to hold it in their head.
 */
function PatientBanner({ lookup, state, pregnancy }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4"
         role="group" aria-label="Maternity summary">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={STATE_TONE[state]}>{STATE_TITLE[state]}</Badge>
        <span className="font-semibold text-slate-900">{lookup.patient.name}</span>
        <span className="tabular-nums text-sm text-slate-600">
          {lookup.patient.patient_number}
        </span>
      </div>
      <p className="mt-1 text-sm text-slate-600">
        {lookup.patient.phone_number || "No phone number on file"}
      </p>

      {pregnancy && (
        <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-3">
          <Fact label="Active pregnancy" value={pregnancySummary(pregnancy)} />
          <Fact label="Gravida / Para" value={gravidaPara(pregnancy) || "Not recorded"} />
          <Fact label="Last visit" value={lastSeen(pregnancy)} />
        </dl>
      )}
    </div>
  );
}

function Fact({ label, value }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="truncate text-sm font-medium text-slate-900">{value}</dd>
    </div>
  );
}

/**
 * Today's visit, inside the pregnancy she is already in.
 *
 * Choosing the clinic is how labour and an emergency stay what they are: a
 * woman who walks in contracting is a labour assessment, not an ANC
 * follow-up, and the list comes from the hospital's own configuration.
 */
function ContinuePregnancy({ lookup, canWork, visitTypeId, onVisitType, onRecord, busy }) {
  const types = visitTypes(lookup);
  return (
    <div className="rounded-xl border border-brand-200 bg-brand-50/60 p-4">
      <p className="text-sm font-semibold text-slate-800">Continue current pregnancy</p>
      <p className="mt-0.5 text-sm text-slate-700">
        Recording a visit adds to this pregnancy. Her earlier visits are untouched.
      </p>
      {!canWork && (
        <p className="mt-2 text-sm text-slate-600">
          A midwife or doctor records today's visit.
        </p>
      )}
      {canWork && (
      <div className="mt-3 grid gap-3 sm:grid-cols-[1fr,auto] sm:items-end">
        <Field label="Today's visit">
          <Select aria-label="Visit type" value={visitTypeId}
                  onChange={(e) => onVisitType(e.target.value)}>
            <option value="">Select a visit type</option>
            {types.map((type) => (
              <option key={type.id} value={String(type.id)}>{type.name}</option>
            ))}
          </Select>
        </Field>
        <Button onClick={onRecord} disabled={!visitTypeId || busy}>
          {busy ? "Recording…" : "Record visit"}
        </Button>
      </div>
      )}
      {canWork && types.length === 0 && (
        <Alert tone="warning" title="No maternity clinics are configured" className="mt-3">
          An administrator adds them under Administration → Maternity visit types.
        </Alert>
      )}
    </div>
  );
}

/** The pregnancy's own history — every visit, in order, unchanged. */
function Timeline({ pregnancy, role }) {
  const { data, isLoading } = useQuery({
    queryKey: ["pregnancy-timeline", pregnancy.id],
    queryFn: () => api.get(`/pregnancies/${pregnancy.id}/timeline/`).then((r) => r.data),
  });
  const encounters = data?.encounters ?? [];

  return (
    <Section
      title={`Pregnancy #${pregnancy.number} — timeline`}
      description={pregnancySummary(pregnancy)}
      actions={(
        // The card she carries between visits. `role` is what resolves it —
        // a PrintButton without one renders nothing at all (rule 49).
        <PrintButton
          role={role}
          documents={["anc_summary"]}
          context={{ pregnancyId: pregnancy.id }}
        />
      )}
    >
      {isLoading && <SkeletonRows rows={3} />}
      {!isLoading && encounters.length === 0 && (
        <EmptyState
          icon="calendar"
          title="No visits recorded yet"
          description="Record today's visit above and it appears here."
        />
      )}
      {encounters.length > 0 && (
        <TableWrap>
          <Table>
            <THead>
              <Tr>
                <Th>Date</Th><Th>Visit</Th><Th>Gestation</Th><Th>Provider</Th><Th>Status</Th>
              </Tr>
            </THead>
            <tbody>
              {encounters.map((encounter) => (
                <Tr key={encounter.id}>
                  <Td className="whitespace-nowrap text-slate-700">
                    {formatDate(encounter.seen_on)}
                  </Td>
                  <Td className="font-medium text-slate-900">{encounter.visit_type_name}</Td>
                  <Td className="tabular-nums text-slate-700">{encounter.gestation || "—"}</Td>
                  <Td className="text-slate-700">{encounter.provider_name ?? "—"}</Td>
                  <Td>
                    <StatusDot tone={ENCOUNTER_TONE[encounter.status] ?? "neutral"}>
                      {encounter.status.replace("_", " ")}
                    </StatusDot>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </TableWrap>
      )}
    </Section>
  );
}

/** Earlier episodes. Read-only — nothing on this page can alter them. */
function PreviousPregnancies({ pregnancies }) {
  return (
    <Section title="Previous pregnancies" description={`${pregnancies.length} on record`}>
      <TableWrap>
        <Table>
          <THead>
            <Tr>
              <Th>Pregnancy</Th><Th>Reference</Th><Th>EDD</Th>
              <Th>Visits</Th><Th>Outcome</Th><Th>Ended</Th>
            </Tr>
          </THead>
          <tbody>
            {pregnancies.map((pregnancy) => (
              <Tr key={pregnancy.id}>
                <Td className="font-medium text-slate-900">#{pregnancy.number}</Td>
                <Td className="tabular-nums text-slate-700">{pregnancy.reference}</Td>
                <Td className="text-slate-700">{formatDate(pregnancy.edd) || "—"}</Td>
                <Td className="tabular-nums text-slate-700">{pregnancy.encounter_count}</Td>
                <Td className="text-slate-700">{pregnancy.outcome || "—"}</Td>
                <Td className="text-slate-700">{formatDate(pregnancy.ended_on) || "—"}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrap>
    </Section>
  );
}

/* =============================================================== episode */

/**
 * Labour → delivery → newborn(s) → postpartum, for the pregnancy she is in.
 *
 * One panel rather than a second page: it is the same woman, the same
 * pregnancy and the same workspace, and the stage she is at decides which one
 * action is offered. Everything underneath is the hospital's own — the ward
 * and bed are read through `inpatient.Admission`, the figures are `Vitals`,
 * the money is a `Charge` — so what this adds is the thread, not a system.
 */
function Episode({ pregnancy, canWork, role }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [error, setError] = useState(null);

  const key = ["labour-episodes", pregnancy.id];
  const { data: labours, isLoading } = useQuery({
    queryKey: key,
    queryFn: () => api.get("/labour-episodes/", { params: { pregnancy: pregnancy.id } })
      .then((r) => r.data.results ?? r.data),
  });

  const stage = episodeStage(labours);
  const actions = episodeActions(labours);
  const open = openLabour(labours);
  const closed = deliveredLabour(labours);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: key });
    queryClient.invalidateQueries({ queryKey: ["maternity-lookup", pregnancy.patient] });
  };

  const openLabourNow = useMutation({
    mutationFn: () => api.post("/labour-episodes/", { pregnancy: pregnancy.id }),
    onSuccess: () => { setError(null); refresh(); showToast({ title: "Labour opened" }); },
    onError: (err) => setError({ code: errorCode(err),
                                 message: readError(err, "Could not open a labour.") }),
  });

  if (isLoading) return <Section title="Labour and delivery"><SkeletonRows rows={3} /></Section>;

  return (
    <Section
      title="Labour and delivery"
      description={stage ? STAGE_TITLE[stage] : ""}
    >
      {error && (
        <Alert tone="danger" title={TITLE_FOR[error.code] ?? "Could not do that"}>
          {error.message}
        </Alert>
      )}

      {stage === BEFORE_LABOUR && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
          <p className="text-sm text-slate-700">
            She is not in labour. Opening one records the episode; the ward admits her
            through Admissions as it does for any patient.
          </p>
          {canWork ? (
            <Button className="mt-3" onClick={() => openLabourNow.mutate()}
                    disabled={openLabourNow.isPending}>
              {openLabourNow.isPending ? "Opening…" : "Open labour"}
            </Button>
          ) : (
            <p className="mt-2 text-sm text-slate-600">A midwife or doctor opens the labour.</p>
          )}
        </div>
      )}

      {open && <OpenLabour labour={open} canWork={canWork} role={role} onDone={refresh} />}
      {!open && closed && <AfterDelivery labour={closed} canWork={canWork} role={role}
                                         onDone={refresh} />}
    </Section>
  );
}

/** The labour in progress: where she is, the partogram, and the delivery. */
function OpenLabour({ labour, canWork, role, onDone }) {
  const { data } = useQuery({
    queryKey: ["labour", labour.id],
    queryFn: () => api.get(`/labour-episodes/${labour.id}/`).then((r) => r.data),
  });
  const observations = data?.observations ?? [];

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-brand-200 bg-brand-50/60 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-800">In labour</p>
            <p className="mt-0.5 text-sm text-slate-700">
              {labour.stage_label} · started {formatDate(labour.started_at)} ·{" "}
              {whereSheIs(labour)}
            </p>
          </div>
          {/* The sheet clipped to the end of the bed. */}
          <PrintButton role={role} documents={["labour_sheet"]}
                       context={{ labourId: labour.id }} />
        </div>
      </div>

      <TableWrap>
        <Table>
          <THead>
            <Tr><Th>Time</Th><Th>Reading</Th><Th>Condition</Th><Th>Recorded by</Th></Tr>
          </THead>
          <tbody>
            {observations.length === 0 && (
              <Tr><Td className="text-slate-600" colSpan={4}>
                No observation recorded yet.
              </Td></Tr>
            )}
            {observations.map((observation) => (
              <Tr key={observation.id}>
                <Td className="whitespace-nowrap text-slate-700">
                  {new Date(observation.observed_at).toLocaleTimeString()}
                </Td>
                <Td className="font-medium text-slate-900">
                  {observationSummary(observation) || "—"}
                </Td>
                <Td className="text-slate-700">{observation.maternal_condition || "—"}</Td>
                <Td className="text-slate-700">{observation.recorded_by_name || "—"}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrap>

      {canWork && <LabourActions labour={labour} onDone={onDone} />}
      {!canWork && (
        <p className="text-sm text-slate-600">
          A midwife or doctor records observations and the delivery.
        </p>
      )}
    </div>
  );
}

/** Recording a check, and recording the delivery. */
function LabourActions({ labour, onDone }) {
  const { showToast } = useToast();
  const [dilation, setDilation] = useState("");
  const [heartRate, setHeartRate] = useState("");
  const [deliveryType, setDeliveryType] = useState("");
  const [babies, setBabies] = useState([{ sex: "" }]);
  const [error, setError] = useState(null);

  const { data: types } = useQuery({
    queryKey: ["maternity-options", "delivery_type"],
    queryFn: () => api.get("/maternity-options/", { params: { kind: "delivery_type" } })
      .then((r) => r.data.results ?? r.data),
    staleTime: 60000,
  });

  const observe = useMutation({
    mutationFn: () => api.post(`/labour-episodes/${labour.id}/observations/`, {
      cervical_dilation_cm: dilation || undefined,
      fetal_heart_rate: heartRate || undefined,
    }),
    onSuccess: () => { setDilation(""); setHeartRate(""); setError(null); onDone(); },
    onError: (err) => setError({ code: errorCode(err),
                                 message: readError(err, "Could not record that check.") }),
  });

  const deliver = useMutation({
    // One delivery, a list of babies. Twins are two entries here — never a
    // second delivery and never a second pregnancy.
    mutationFn: () => api.post(`/labour-episodes/${labour.id}/delivery/`,
                               deliveryBody({ deliveryType, babies })),
    onSuccess: (response) => {
      setError(null);
      onDone();
      showToast({ title: "Delivery recorded",
                  message: birthDescription(response.data) });
    },
    onError: (err) => setError({ code: errorCode(err),
                                 message: readError(err, "Could not record the delivery.") }),
  });

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardBody className="space-y-3">
          <p className="text-sm font-semibold text-slate-800">Record a check</p>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Dilation (cm)">
              <Input aria-label="Dilation" type="number" min="0" max="10"
                     value={dilation} onChange={(e) => setDilation(e.target.value)} />
            </Field>
            <Field label="Fetal heart rate">
              <Input aria-label="Fetal heart rate" type="number"
                     value={heartRate} onChange={(e) => setHeartRate(e.target.value)} />
            </Field>
          </div>
          <Button onClick={() => observe.mutate()}
                  disabled={observe.isPending || (!dilation && !heartRate)}>
            {observe.isPending ? "Recording…" : "Record check"}
          </Button>
        </CardBody>
      </Card>

      <Card>
        <CardBody className="space-y-3">
          <p className="text-sm font-semibold text-slate-800">Record the delivery</p>
          <Field label="Delivery type">
            <Select aria-label="Delivery type" value={deliveryType}
                    onChange={(e) => setDeliveryType(e.target.value)}>
              <option value="">Select a delivery type</option>
              {(types ?? []).map((type) => (
                <option key={type.id} value={String(type.id)}>{type.name}</option>
              ))}
            </Select>
          </Field>

          {babies.map((baby, index) => (
            <div key={index} className="grid grid-cols-2 gap-3">
              <Field label={`Baby ${index + 1} — sex`}>
                <Select aria-label={`Baby ${index + 1} sex`} value={baby.sex}
                        onChange={(e) => setBabies((current) => current.map((row, i) =>
                          (i === index ? { ...row, sex: e.target.value } : row)))}>
                  <option value="">Select</option>
                  <option value="M">Male</option>
                  <option value="F">Female</option>
                  <option value="A">Ambiguous</option>
                </Select>
              </Field>
              <Field label="Weight (g)">
                <Input aria-label={`Baby ${index + 1} weight`} type="number"
                       value={baby.birth_weight_grams ?? ""}
                       onChange={(e) => setBabies((current) => current.map((row, i) =>
                         (i === index ? { ...row, birth_weight_grams: e.target.value } : row)))} />
              </Field>
            </div>
          ))}

          <div className="flex flex-wrap gap-2">
            {/* Twins are a second baby on this delivery. */}
            <Button variant="linkMuted" size="xs"
                    onClick={() => setBabies((current) => [...current, { sex: "" }])}>
              Add another baby
            </Button>
            {babies.length > 1 && (
              <Button variant="linkMuted" size="xs"
                      onClick={() => setBabies((current) => current.slice(0, -1))}>
                Remove last
              </Button>
            )}
          </div>

          {error && (
            <Alert tone="danger" title={TITLE_FOR[error.code] ?? "Could not do that"}>
              {error.message}
            </Alert>
          )}

          <Button onClick={() => deliver.mutate()}
                  disabled={deliver.isPending || !canRecordDelivery({ deliveryType, babies })}>
            {deliver.isPending ? "Recording…" : "Record delivery"}
          </Button>
        </CardBody>
      </Card>
    </div>
  );
}

/** After the birth: the babies, the postpartum checks and the paperwork. */
function AfterDelivery({ labour, canWork, role, onDone }) {
  const { data } = useQuery({
    queryKey: ["labour", labour.id],
    queryFn: () => api.get(`/labour-episodes/${labour.id}/`).then((r) => r.data),
  });
  const delivery = data?.delivery;
  if (!delivery) return null;
  const babies = babiesOf(delivery);

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-800">
              {delivery.delivery_type_name} · {birthDescription(delivery)}
            </p>
            <p className="mt-0.5 text-sm text-slate-700">
              Delivered {formatDate(delivery.delivered_at)}
              {delivery.midwife_name ? ` · ${delivery.midwife_name}` : ""}
            </p>
          </div>
          <PrintButton
            role={role}
            documents={["delivery_note", "postpartum_summary", "mother_discharge_letter"]}
            context={{ deliveryId: delivery.id }}
          />
        </div>
      </div>

      <TableWrap>
        <Table>
          <THead>
            <Tr><Th>#</Th><Th>Name</Th><Th>Sex</Th><Th>Weight</Th><Th>Apgar 1/5</Th>
              <Th>Status</Th><Th /></Tr>
          </THead>
          <tbody>
            {babies.map((baby) => (
              <Tr key={baby.id}>
                <Td className="tabular-nums text-slate-700">{baby.birth_order}</Td>
                <Td className="font-medium text-slate-900">
                  {baby.name || `Baby ${baby.birth_order}`}
                </Td>
                <Td className="text-slate-700">{baby.sex_label}</Td>
                <Td className="tabular-nums text-slate-700">
                  {baby.birth_weight_grams ? `${baby.birth_weight_grams} g` : "—"}
                </Td>
                <Td className="tabular-nums text-slate-700">
                  {`${baby.apgar_1_min ?? "—"} / ${baby.apgar_5_min ?? "—"}`}
                </Td>
                <Td className="text-slate-700">{baby.status_name || "—"}</Td>
                <Td>
                  {/* Per baby, because a delivery of twins would otherwise
                      have to guess which record you meant. */}
                  <PrintButton
                    role={role}
                    documents={["birth_record", "newborn_discharge_letter"]}
                    context={{ newborn: baby, deliveryId: delivery.id }}
                  />
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrap>

      {canWork && <PostpartumPanel delivery={delivery} onDone={onDone} />}
    </div>
  );
}

/** A postpartum check — a course of care, so always a new row. */
function PostpartumPanel({ delivery, onDone }) {
  const { showToast } = useToast();
  const [bleeding, setBleeding] = useState("");
  const [advice, setAdvice] = useState("");

  const record = useMutation({
    mutationFn: () => api.post(`/deliveries/${delivery.id}/postpartum/`, {
      bleeding, discharge_advice: advice,
    }),
    onSuccess: () => {
      setBleeding(""); setAdvice("");
      onDone();
      showToast({ title: "Postpartum check recorded" });
    },
  });

  const checks = delivery.postpartum_visits ?? [];

  return (
    <Card>
      <CardBody className="space-y-3">
        <p className="text-sm font-semibold text-slate-800">
          Postpartum {checks.length > 0 && `· ${checks.length} recorded`}
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Bleeding">
            <Input aria-label="Bleeding" value={bleeding}
                   onChange={(e) => setBleeding(e.target.value)}
                   placeholder="e.g. Normal lochia" />
          </Field>
          <Field label="Advice on discharge">
            <Input aria-label="Advice" value={advice}
                   onChange={(e) => setAdvice(e.target.value)} />
          </Field>
        </div>
        <Button onClick={() => record.mutate()}
                disabled={record.isPending || (!bleeding && !advice)}>
          {record.isPending ? "Recording…" : "Record postpartum check"}
        </Button>
      </CardBody>
    </Card>
  );
}

/* ============================================================= workspace */

/** The six views of one pregnancy. `Overview` is what opens. */
const TABS = [
  ["overview", "Overview"],
  // **Triage is the hospital's, reached from the ward.** The tab renders
  // `VitalsTab` — the same component the chart's Vitals tab renders — so a
  // reading taken here is an ordinary `clinical.Vitals` row, locked by the
  // same mixin and read back on the same chart. There is no maternity vitals
  // form, and there must not be one.
  ["triage", "Triage"],
  ["anc", "ANC"],
  ["labour", "Labour"],
  ["deliveries", "Deliveries"],
  ["postpartum", "Postpartum"],
  ["newborns", "Newborns"],
];

/**
 * The maternity workspace — one pregnancy, six views of it.
 *
 * Tabs rather than a stack, because by the time a woman has delivered the
 * page held her ANC course, her partogram, her delivery, her babies and her
 * postpartum checks all at once, and the midwife had to scroll past the
 * history to reach today. `TabBar` is the application's own (it scrolls
 * itself on a phone rather than wrapping into three rows).
 *
 * Every tab reads the **Phase 2 records**: there is no second store, and the
 * counts are the rows themselves — `Newborns (2)` is two `Newborn` rows, not
 * a delivery counted once.
 */
function Workspace({ pregnancy, canWork, role, earlier }) {
  const [tab, setTab] = useState("overview");
  const queryClient = useQueryClient();

  const { data: timeline } = useQuery({
    queryKey: ["pregnancy-timeline", pregnancy.id],
    queryFn: () => api.get(`/pregnancies/${pregnancy.id}/timeline/`).then((r) => r.data),
  });
  const { data: labours, isLoading: loadingLabours } = useQuery({
    queryKey: ["labour-episodes", pregnancy.id],
    queryFn: () => api.get("/labour-episodes/", { params: { pregnancy: pregnancy.id } })
      .then((r) => r.data.results ?? r.data),
  });

  // Who is responsible for her and where she is lying — one read of the
  // existing `/maternity/assignment/`, so the overview and the assignment
  // panel are never two answers to one question.
  const { data: responsibility } = useQuery({
    queryKey: ["maternity-assignment", pregnancy.patient],
    queryFn: () => api.get("/maternity/assignment/",
                           { params: { patient: pregnancy.patient } }).then((r) => r.data),
  });

  const encounters = timeline?.encounters ?? [];
  const labour = openLabour(labours) ?? deliveredLabour(labours) ?? (labours ?? [])[0] ?? null;

  const { data: labourDetail } = useQuery({
    queryKey: ["labour", labour?.id],
    queryFn: () => api.get(`/labour-episodes/${labour.id}/`).then((r) => r.data),
    enabled: Boolean(labour?.id),
  });
  const delivery = labourDetail?.delivery ?? null;

  /**
   * Reload the records after a correction, so the panel shows what it now
   * says rather than what it said when the tab opened. Every key a maternity
   * correction can move, and no more.
   */
  const refreshRecords = () => {
    for (const key of [["pregnancy-timeline", pregnancy.id],
                       ["labour-episodes", pregnancy.id],
                       ["labour", labour?.id],
                       ["maternity-lookup", pregnancy.patient]]) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  };
  const babies = babiesOf(delivery);
  const postpartum = delivery?.postpartum_visits ?? [];

  const counts = {
    anc: encounters.length,
    labour: labourDetail?.observations?.length ?? labour?.observation_count ?? 0,
    deliveries: delivery ? 1 : 0,
    postpartum: postpartum.length,
    newborns: babies.length,
  };

  return (
    <Section
      title={`Pregnancy #${pregnancy.number}`}
      description={pregnancySummary(pregnancy)}
      actions={(
        <PrintButton role={role} documents={["pregnancy_summary", "anc_summary"]}
                     context={{ pregnancyId: pregnancy.id }} />
      )}
    >
      <TabBar label="Maternity record">
        {TABS.map(([value, label]) => (
          <Tab key={value} active={tab === value} onClick={() => setTab(value)}>
            {label}
            {counts[value] > 0 && (
              <span className="ml-1.5 tabular-nums text-slate-500">({counts[value]})</span>
            )}
          </Tab>
        ))}
      </TabBar>

      <div className="mt-4 min-w-0">
        {tab === "overview" && (
          <OverviewTab pregnancy={pregnancy} encounters={encounters} labour={labour}
                       delivery={delivery} babies={babies} postpartum={postpartum}
                       earlier={earlier} responsibility={responsibility}
                       onAmended={refreshRecords} />
        )}
        {tab === "triage" && <VitalsTab patientId={pregnancy.patient} />}
        {tab === "anc" && <AncTab encounters={encounters} onAmended={refreshRecords} />}
        {tab === "labour" && (
          <LabourTab pregnancy={pregnancy} labour={labour} detail={labourDetail}
                     loading={loadingLabours} canWork={canWork} role={role}
                     onAmended={refreshRecords} />
        )}
        {tab === "deliveries" && (
          <DeliveriesTab delivery={delivery} babies={babies} labour={labour} role={role}
                         onAmended={refreshRecords} />
        )}
        {tab === "postpartum" && (
          <PostpartumTab delivery={delivery} visits={postpartum} canWork={canWork}
                         role={role} />
        )}
        {tab === "newborns" && <NewbornsTab babies={babies} delivery={delivery} role={role} />}
      </div>
    </Section>
  );
}

/** Everything at a glance — what a clinician reads before deciding anything. */
function OverviewTab({ pregnancy, encounters, labour, delivery, babies, postpartum, earlier,
                      responsibility, onAmended }) {
  const admission = responsibility?.admission;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetaStat value={`#${pregnancy.number}`} label="Pregnancy" />
        <MetaStat value={pregnancy.gestation || "Not dated"} label="Gestation" />
        <MetaStat value={formatDate(pregnancy.edd) || "—"} label="EDD" />
        <MetaStat value={encounters.length} label="ANC visits" />
      </div>

      {/* The pregnancy record's own correction controls. A correction is
          auditable and the identity facts are not offered — both decided by
          the server and read off the record (`can_amend`, `amendable_fields`). */}
      <RecordAmendment endpoint="pregnancies" record={pregnancy} label="pregnancy"
                       onAmended={onAmended} />

      <TableWrap>
        <Table>
          <THead><Tr><Th>Stage</Th><Th>Where it stands</Th></Tr></THead>
          <tbody>
            <Tr>
              <Td className="font-medium text-slate-900">Pregnancy</Td>
              <Td><StatusDot tone={pregnancy.is_active ? "success" : "neutral"}>
                {pregnancy.is_active ? "Active" : "Ended"}
              </StatusDot></Td>
            </Tr>
            <Tr>
              <Td className="font-medium text-slate-900">Labour</Td>
              <Td className="text-slate-700">
                {labour ? `${labour.status_label} · ${labour.stage_label}` : "Not in labour"}
              </Td>
            </Tr>
            <Tr>
              {/* The ward's own record, derived on every read — a bed
                  transfer made on the bed board moves it with no maternity
                  write at all. `whereSheIs(labour)` is the labour's own view
                  of it and stays the fallback for a closed episode. */}
              <Td className="font-medium text-slate-900">Admission</Td>
              <Td className="text-slate-700">
                {admission?.admitted
                  ? `Admitted · ${admission.ward} · Bed ${admission.bed}`
                  : (whereSheIs(labour) || "Not admitted")}
              </Td>
            </Tr>
            <Tr>
              <Td className="font-medium text-slate-900">Assigned nurse</Td>
              <Td className="text-slate-700">
                {responsibility?.assigned_nurse?.name ?? "Not assigned"}
              </Td>
            </Tr>
            <Tr>
              <Td className="font-medium text-slate-900">Assigned doctor</Td>
              <Td className="text-slate-700">
                {responsibility?.assigned_doctor?.name ?? "Not assigned"}
              </Td>
            </Tr>
            <Tr>
              <Td className="font-medium text-slate-900">Delivery</Td>
              <Td className="text-slate-700">
                {delivery
                  ? `${delivery.delivery_type_name} · ${formatDate(delivery.delivered_at)}`
                  : "Not delivered"}
              </Td>
            </Tr>
            <Tr>
              <Td className="font-medium text-slate-900">Newborns</Td>
              <Td className="text-slate-700">
                {delivery ? birthDescription(delivery) : "—"}
              </Td>
            </Tr>
            <Tr>
              <Td className="font-medium text-slate-900">Postpartum</Td>
              <Td className="text-slate-700">
                {postpartum.length
                  ? `${postpartum.length} check${postpartum.length === 1 ? "" : "s"}`
                  : "None recorded"}
              </Td>
            </Tr>
          </tbody>
        </Table>
      </TableWrap>

      {earlier.length > 0 && <PreviousPregnancies pregnancies={earlier} />}
    </div>
  );
}

/** The ANC course — the same encounters Phase 1 records, read-only. */
function AncTab({ encounters, onAmended }) {
  if (encounters.length === 0) {
    return <EmptyState icon="calendar" title="No ANC visit recorded yet"
                       description="Record today's visit above and it appears here." />;
  }
  return (
    <TableWrap>
      <Table>
        <THead>
          <Tr><Th>Date</Th><Th>Visit</Th><Th>Gestation</Th><Th>Provider</Th><Th>Status</Th>
              <Th>Record</Th></Tr>
        </THead>
        <tbody>
          {encounters.map((encounter) => (
            <Tr key={encounter.id}>
              <Td className="whitespace-nowrap text-slate-700">
                {formatDate(encounter.seen_on)}
              </Td>
              <Td className="font-medium text-slate-900">{encounter.visit_type_name}</Td>
              <Td className="tabular-nums text-slate-700">{encounter.gestation || "—"}</Td>
              <Td className="text-slate-700">{encounter.provider_name ?? "—"}</Td>
              <Td><StatusDot tone={ENCOUNTER_TONE[encounter.status] ?? "neutral"}>
                {encounter.status.replace("_", " ")}
              </StatusDot></Td>
              {/* Per attendance, because a correction belongs to the visit it
                  corrects — a header control would have to guess which. */}
              <Td>
                <RecordAmendment endpoint="maternity-encounters" record={encounter}
                                 label="visit" onAmended={onAmended} />
              </Td>
            </Tr>
          ))}
        </tbody>
      </Table>
    </TableWrap>
  );
}

/** The labour episode and its partogram — a historical list, never an edit. */
function LabourTab({ pregnancy, labour, detail, loading, canWork, role, onAmended }) {
  if (loading) return <SkeletonRows rows={3} />;
  if (!labour) {
    return (
      <div className="space-y-3">
        <EmptyState icon="activity" title="Not in labour"
                    description="No labour episode has been opened for this pregnancy." />
        {canWork && <OpenLabourButton pregnancy={pregnancy} />}
      </div>
    );
  }
  const observations = detail?.observations ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4">
        <dl className="grid min-w-0 flex-1 gap-x-6 gap-y-2 sm:grid-cols-3">
          <Fact label="Onset" value={labour.onset_label} />
          <Fact label="Stage" value={labour.stage_label} />
          <Fact label="Status" value={labour.status_label} />
          <Fact label="Started" value={formatDate(labour.started_at)} />
          <Fact label="Ended" value={labour.ended_at ? formatDate(labour.ended_at) : "—"} />
          <Fact label="Ward / bed" value={whereSheIs(labour)} />
        </dl>
        {/* The episode's own correction controls. The partogram beneath is a
            historical list and stays one — a later check is a new row, never
            an edit to an earlier one. */}
        <div className="w-full">
          {/* `detail` carries the amendment state; the list row does not. */}
          <RecordAmendment endpoint="labour-episodes" record={detail ?? labour}
                           label="labour" onAmended={onAmended} />
        </div>
        <PrintButton role={role} documents={["labour_sheet", "partogram"]}
                     context={{ labourId: labour.id }} />
      </div>

      <TableWrap>
        <Table>
          <THead>
            <Tr><Th>Time</Th><Th>Dilation</Th><Th>Contractions</Th><Th>FHR</Th>
              <Th>Membranes</Th><Th>Recorded by</Th></Tr>
          </THead>
          <tbody>
            {observations.length === 0 && (
              <Tr><Td colSpan={6} className="text-slate-600">
                No observation recorded yet.
              </Td></Tr>
            )}
            {observations.map((row) => (
              <Tr key={row.id}>
                <Td className="whitespace-nowrap text-slate-700">
                  {new Date(row.observed_at).toLocaleString()}
                </Td>
                <Td className="tabular-nums text-slate-900">
                  {row.cervical_dilation_cm != null ? `${row.cervical_dilation_cm} cm` : "—"}
                </Td>
                <Td className="tabular-nums text-slate-700">
                  {row.contractions_per_10min != null
                    ? `${row.contractions_per_10min}/10 min` : "—"}
                </Td>
                <Td className="tabular-nums text-slate-700">{row.fetal_heart_rate ?? "—"}</Td>
                <Td className="text-slate-700">{row.membranes_label || "—"}</Td>
                <Td className="text-slate-700">{row.recorded_by_name || "—"}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrap>
    </div>
  );
}

/** Pregnancy → Labour → Delivery → Newborn(s), stated as that chain. */
function DeliveriesTab({ delivery, babies, labour, role, onAmended }) {
  if (!delivery) {
    return <EmptyState icon="calendar" title="No delivery recorded"
                       description="A delivery is recorded by closing the labour." />;
  }
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-semibold text-slate-900">
              Delivery {delivery.reference}
            </p>
            <p className="mt-0.5 text-sm text-slate-700">
              {delivery.delivery_type_name}
              {delivery.outcome_name ? ` · ${delivery.outcome_name}` : ""}
            </p>
            {/* The count is the babies themselves — twins read as two. */}
            <p className="mt-1 text-sm font-semibold text-slate-800">
              {babies.length} newborn{babies.length === 1 ? "" : "s"}
            </p>
            <div className="mt-2">
              <RecordAmendment endpoint="deliveries" record={delivery} label="delivery"
                               onAmended={onAmended} />
            </div>
          </div>
          <PrintButton
            role={role}
            documents={["delivery_note", "mother_discharge_letter"]}
            context={{ deliveryId: delivery.id }}
          />
        </div>

        <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-3">
          <Fact label="Delivered" value={formatDate(delivery.delivered_at)} />
          <Fact label="Doctor" value={delivery.doctor_name || "—"} />
          <Fact label="Midwife" value={delivery.midwife_name || "—"} />
          <Fact label="Blood loss" value={delivery.estimated_blood_loss_ml
            ? `${delivery.estimated_blood_loss_ml} ml` : "—"} />
          <Fact label="Placenta" value={delivery.placenta_complete === true ? "Complete"
            : delivery.placenta_complete === false ? "Incomplete" : "—"} />
          <Fact label="Labour" value={labour ? labour.onset_label : "—"} />
        </dl>

        {(delivery.complication_names ?? []).length > 0 && (
          <p className="mt-2 text-sm text-slate-800">
            <span className="font-semibold">Complications: </span>
            {delivery.complication_names.join(" · ")}
          </p>
        )}
      </div>

      {/* Each baby separately — never merged into one card. */}
      <NewbornsTab babies={babies} delivery={delivery} role={role} heading={false} />
    </div>
  );
}

/**
 * Every baby of this delivery, one card each.
 *
 * A multiple birth is several `Newborn` rows against one `Delivery` (rule
 * 56), so this lists all of them and never only the first — and each keeps
 * its own birth order, weight and Apgar, which is what stops Baby 1 and
 * Baby 2 being confused on the ward.
 */
function NewbornsTab({ babies, delivery, role, heading = true }) {
  if (!delivery) {
    return <EmptyState icon="calendar" title="No delivery recorded"
                       description="Babies are recorded with the delivery." />;
  }
  if (babies.length === 0) {
    return <EmptyState icon="calendar" title="No baby recorded"
                       description="This delivery has no newborn against it." />;
  }
  return (
    <div className="space-y-3">
      {heading && (
        <p className="text-sm font-semibold text-slate-800">
          Newborns ({babies.length}) · {birthDescription(delivery)}
        </p>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {babies.map((baby) => (
          <Card key={baby.id}>
            <CardBody className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-semibold text-slate-900">
                    Baby {baby.birth_order} — {baby.reference}
                  </p>
                  <p className="text-sm text-slate-600">{baby.name || "Not named yet"}</p>
                </div>
                <Badge tone="info">{baby.sex_label}</Badge>
              </div>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2">
                <Fact label="Birth order" value={baby.birth_order} />
                <Fact label="Weight" value={baby.birth_weight_grams
                  ? `${baby.birth_weight_grams} g` : "—"} />
                <Fact label="Apgar 1 / 5 / 10"
                      value={[baby.apgar_1_min, baby.apgar_5_min, baby.apgar_10_min]
                        .map((score) => score ?? "—").join(" / ")} />
                <Fact label="Status" value={baby.status_name || "—"} />
                <Fact label="Born" value={formatDate(delivery.delivered_at)} />
                <Fact label="Nursery" value={baby.admitted_to_nursery ? "Admitted" : "With mother"} />
              </dl>
              {baby.congenital_abnormalities && (
                <p className="text-sm text-slate-700">
                  <span className="font-semibold">Abnormalities: </span>
                  {baby.congenital_abnormalities}
                </p>
              )}
              <PrintButton
                role={role}
                documents={["birth_record", "newborn_discharge_letter"]}
                context={{ newborn: baby, deliveryId: delivery.id }}
              />
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  );
}

/** The mother's postpartum course — one delivery, several checks. */
function PostpartumTab({ delivery, visits, canWork, role }) {
  if (!delivery) {
    return <EmptyState icon="calendar" title="No delivery recorded"
                       description="Postpartum care follows a delivery." />;
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-semibold text-slate-800">
          {visits.length} check{visits.length === 1 ? "" : "s"} since delivery
        </p>
        <PrintButton role={role} documents={["postpartum_summary"]}
                     context={{ deliveryId: delivery.id }} />
      </div>

      {visits.length === 0 ? (
        <EmptyState icon="calendar" title="No postpartum check yet"
                    description="Record one below and it appears here." />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <Tr><Th>Seen</Th><Th>Mother</Th><Th>Bleeding</Th><Th>Breastfeeding</Th>
                <Th>Family planning</Th><Th>Recorded by</Th></Tr>
            </THead>
            <tbody>
              {visits.map((visit) => (
                <Tr key={visit.id}>
                  <Td className="whitespace-nowrap text-slate-700">
                    {new Date(visit.seen_at).toLocaleString()}
                  </Td>
                  <Td className="text-slate-900">{visit.mother_condition_name || "—"}</Td>
                  <Td className="text-slate-700">{visit.bleeding || "—"}</Td>
                  <Td className="text-slate-700">
                    {visit.breastfeeding_established === true ? "Established"
                      : visit.breastfeeding_established === false ? "Not yet" : "—"}
                  </Td>
                  <Td className="text-slate-700">{visit.family_planning_name || "—"}</Td>
                  <Td className="text-slate-700">{visit.recorded_by_name || "—"}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </TableWrap>
      )}

      {canWork && <PostpartumPanel delivery={delivery} onDone={() => {}} />}
    </div>
  );
}

/** Opening a labour from the Labour tab's empty state. */
function OpenLabourButton({ pregnancy }) {
  const queryClient = useQueryClient();
  const open = useMutation({
    mutationFn: () => api.post("/labour-episodes/", { pregnancy: pregnancy.id }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["labour-episodes", pregnancy.id] }),
  });
  return (
    <Button onClick={() => open.mutate()} disabled={open.isPending}>
      {open.isPending ? "Opening…" : "Open labour"}
    </Button>
  );
}
