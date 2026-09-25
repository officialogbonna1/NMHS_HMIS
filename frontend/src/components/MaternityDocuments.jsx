import { useQuery } from "@tanstack/react-query";

import api from "../api/client";
import PrintSheet, {
  SheetHeader, PatientBlock, SheetSection, SheetFooter, SheetStatus,
  WriteInLines, Field,
} from "./PrintSheet.jsx";

// The paper maternity actually hands over: the ANC card she carries, the
// labour sheet clipped to the end of the bed, the delivery note, the birth
// record for each baby, the postpartum summary, and the two discharge letters
// — one for the mother, one for the baby.
//
// Every one of them is the hospital's existing `PrintSheet`: the same header
// with the hospital's own identity, the same `PatientBlock`, the same
// sections and footer, black on white. There is no second print system and no
// maternity letterhead — rule 31's "one set of models, two interfaces" applied
// to paper.
//
// Two rules, the same ones `DepartmentDocuments.jsx` follows:
//   * each sheet fetches its own data from an endpoint the printing role
//     already passes, so a document is never a way round a permission; and
//   * an empty section is **omitted**, because a printed heading with blank
//     space under it reads as "looked, found nothing" (rule 45).

const dateTime = (value) => (value ? new Date(value).toLocaleString() : "—");
const dateOnly = (value) => (value ? new Date(value).toLocaleDateString() : "—");

/** Her identity as every other sheet in the hospital prints it. */
function motherBlock(pregnancy) {
  return {
    name: pregnancy?.patient_name,
    patient_number: pregnancy?.patient_number,
  };
}

/** A section that renders only when it has something to say. */
function Written({ title, children, when }) {
  if (!when) return null;
  return <SheetSection title={title}>{children}</SheetSection>;
}

/* ------------------------------------------------------------------ ANC */

/**
 * The antenatal card — what she carries between visits, and what the next
 * clinic reads before touching her.
 */
export function AncSummarySheet({ pregnancyId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["pregnancy-timeline", pregnancyId],
    queryFn: () => api.get(`/pregnancies/${pregnancyId}/timeline/`).then((r) => r.data),
    enabled: Boolean(pregnancyId),
  });

  if (isLoading || isError || !data) {
    return <SheetStatus title="ANC summary" isError={isError} onClose={onClose} />;
  }
  const visits = data.encounters ?? [];

  return (
    <PrintSheet title={`ANC summary — ${data.patient_number}`} onClose={onClose}>
      <SheetHeader
        documentTitle="Antenatal Care Summary"
        reference={data.reference}
        date={dateOnly(new Date())}
      />
      <PatientBlock patient={motherBlock(data)} />

      <SheetSection title="This pregnancy">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Pregnancy" value={`#${data.number}`} strong />
          <Field label="Gestation today" value={data.gestation || "Not dated"} strong />
          <Field label="LMP" value={dateOnly(data.lmp)} />
          <Field label="EDD" value={dateOnly(data.edd)} strong />
          <Field label="Gravida" value={data.gravida ?? "—"} />
          <Field label="Para" value={data.para ?? "—"} />
        </div>
      </SheetSection>

      <SheetSection title={`Visits (${visits.length})`}>
        {visits.length === 0 ? (
          <p className="text-sm text-slate-900">No visits recorded yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="py-1">Date</th><th>Visit</th><th>Gestation</th><th>Seen by</th>
              </tr>
            </thead>
            <tbody>
              {[...visits].reverse().map((visit) => (
                <tr key={visit.id} className="border-b border-slate-300">
                  <td className="py-1">{dateOnly(visit.seen_on)}</td>
                  <td>{visit.visit_type_name}</td>
                  <td>{visit.gestation || "—"}</td>
                  <td>{visit.provider_name || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </SheetSection>

      <SheetSection title="Next visit">
        <WriteInLines rows={3} />
      </SheetSection>

      <SheetFooter signatory="Midwife / Doctor"
                   note="The mother keeps this card and brings it to every visit." />
    </PrintSheet>
  );
}

/**
 * The maternity summary — the whole pregnancy on one sheet: her dates, the
 * ANC course, the labour, the delivery, every baby and the postpartum checks.
 * The document somebody reads when she arrives and nobody remembers her.
 */
export function PregnancySummarySheet({ pregnancyId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["pregnancy-timeline", pregnancyId],
    queryFn: () => api.get(`/pregnancies/${pregnancyId}/timeline/`).then((r) => r.data),
    enabled: Boolean(pregnancyId),
  });
  const { data: labours } = useQuery({
    queryKey: ["labour-episodes", pregnancyId],
    queryFn: () => api.get("/labour-episodes/", { params: { pregnancy: pregnancyId } })
      .then((r) => r.data.results ?? r.data),
    enabled: Boolean(pregnancyId),
  });

  if (isLoading || isError || !data) {
    return <SheetStatus title="Maternity summary" isError={isError} onClose={onClose} />;
  }
  const visits = data.encounters ?? [];
  const labour = (labours ?? [])[0];

  return (
    <PrintSheet title={`Maternity summary — ${data.patient_number}`} onClose={onClose}>
      <SheetHeader documentTitle="Maternity Summary" reference={data.reference}
                   date={dateOnly(new Date())} />
      <PatientBlock patient={motherBlock(data)} />

      <SheetSection title="Pregnancy">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Pregnancy" value={`#${data.number}`} strong />
          <Field label="Status" value={data.is_active ? "Active" : "Ended"} strong />
          <Field label="LMP" value={dateOnly(data.lmp)} />
          <Field label="EDD" value={dateOnly(data.edd)} strong />
          <Field label="Gestation" value={data.gestation || "Not dated"} strong />
          <Field label="Gravida / Para"
                 value={[data.gravida != null ? `G${data.gravida}` : null,
                         data.para != null ? `P${data.para}` : null]
                   .filter(Boolean).join(" ") || "—"} />
          <Field label="ANC visits" value={visits.length} />
          <Field label="Outcome" value={data.outcome || "—"} />
        </div>
      </SheetSection>

      <Written title="Labour" when={labour}>
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Onset" value={labour?.onset_label} strong />
          <Field label="Started" value={dateTime(labour?.started_at)} />
          <Field label="Outcome" value={labour?.status_label} strong />
          <Field label="Ward / bed" value={labour?.ward_name
            ? `${labour.ward_name} · ${labour.bed_number}` : "Not admitted"} />
        </div>
      </Written>

      <SheetFooter signatory="Midwife / Doctor"
                   note="A summary of the record. The full notes stay in the case file." />
    </PrintSheet>
  );
}

/**
 * The partogram on its own sheet — every observation of the labour, in order.
 *
 * Separate from the labour record because it is the sheet that gets filled in
 * at the bedside and photocopied for the notes, and because a long labour's
 * observations run to a page of their own.
 */
export function PartogramSheet({ labourId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["labour", labourId],
    queryFn: () => api.get(`/labour-episodes/${labourId}/`).then((r) => r.data),
    enabled: Boolean(labourId),
  });

  if (isLoading || isError || !data) {
    return <SheetStatus title="Partogram" isError={isError} onClose={onClose} />;
  }
  const observations = data.observations ?? [];

  return (
    <PrintSheet title={`Partogram — ${data.patient_number}`} onClose={onClose}>
      <SheetHeader documentTitle="Partogram / Labour Observations"
                   reference={`LAB-${String(data.id).padStart(6, "0")}`}
                   date={dateTime(data.started_at)} />
      <PatientBlock patient={{ name: data.patient_name, patient_number: data.patient_number }} />

      <SheetSection title="Observations">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left">
              <th className="py-1">Time</th><th>Dilation</th><th>Contractions</th>
              <th>Duration</th><th>FHR</th><th>Membranes</th><th>Condition</th><th>By</th>
            </tr>
          </thead>
          <tbody>
            {observations.map((row) => (
              <tr key={row.id} className="border-b border-slate-300">
                <td className="py-1">{dateTime(row.observed_at)}</td>
                <td>{row.cervical_dilation_cm != null ? `${row.cervical_dilation_cm} cm` : "—"}</td>
                <td>{row.contractions_per_10min != null
                  ? `${row.contractions_per_10min}/10` : "—"}</td>
                <td>{row.contraction_duration_seconds
                  ? `${row.contraction_duration_seconds}s` : "—"}</td>
                <td>{row.fetal_heart_rate ?? "—"}</td>
                <td>{row.membranes_label || "—"}</td>
                <td>{row.maternal_condition || "—"}</td>
                <td>{row.recorded_by_name || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* Room to keep writing at the bedside after it is printed. */}
        <div className="mt-3"><WriteInLines rows={observations.length ? 4 : 10} /></div>
      </SheetSection>

      <SheetFooter signatory="Midwife in charge" />
    </PrintSheet>
  );
}

/* --------------------------------------------------------------- labour */

/** The labour sheet: the partogram as it stands, clipped to the bed. */
export function LabourSheet({ labourId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["labour", labourId],
    queryFn: () => api.get(`/labour-episodes/${labourId}/`).then((r) => r.data),
    enabled: Boolean(labourId),
  });

  if (isLoading || isError || !data) {
    return <SheetStatus title="Labour sheet" isError={isError} onClose={onClose} />;
  }
  const observations = data.observations ?? [];

  return (
    <PrintSheet title={`Labour sheet — ${data.patient_number}`} onClose={onClose}>
      <SheetHeader documentTitle="Labour Record" reference={`LAB-${String(data.id).padStart(6, "0")}`}
                   date={dateTime(data.started_at)} />

      {data.ward_name && (
        <div className="mb-6 rounded-lg border-2 border-slate-800 px-6 py-4 text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-700">
            Ward and bed
          </p>
          <p className="mt-1 text-3xl font-bold tracking-wide text-slate-900">
            {data.ward_name} · Bed {data.bed_number}
          </p>
        </div>
      )}

      <PatientBlock patient={{ name: data.patient_name, patient_number: data.patient_number }} />

      <SheetSection title="Labour">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Onset" value={data.onset_label} strong />
          <Field label="Started" value={dateTime(data.started_at)} strong />
          <Field label="Stage" value={data.stage_label} strong />
          <Field label="Gestation" value={data.gestation || "—"} />
          <Field label="Pregnancy" value={`#${data.pregnancy_number}`} />
          <Field label="Status" value={data.status_label} />
        </div>
      </SheetSection>

      <SheetSection title={`Observations (${observations.length})`}>
        {observations.length === 0 ? (
          <WriteInLines rows={8} />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="py-1">Time</th><th>Dilation</th><th>Contractions</th>
                <th>FHR</th><th>Membranes</th><th>By</th>
              </tr>
            </thead>
            <tbody>
              {observations.map((row) => (
                <tr key={row.id} className="border-b border-slate-300">
                  <td className="py-1">{dateTime(row.observed_at)}</td>
                  <td>{row.cervical_dilation_cm != null ? `${row.cervical_dilation_cm} cm` : "—"}</td>
                  <td>{row.contractions_per_10min != null
                    ? `${row.contractions_per_10min}/10 min` : "—"}</td>
                  <td>{row.fetal_heart_rate ?? "—"}</td>
                  <td>{row.membranes_label || "—"}</td>
                  <td>{row.recorded_by_name || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </SheetSection>

      <Written title="Notes" when={data.notes}>
        <p className="whitespace-pre-wrap text-sm text-slate-900">{data.notes}</p>
      </Written>

      <SheetFooter signatory="Midwife in charge" note="File with the mother's case notes." />
    </PrintSheet>
  );
}

/* ------------------------------------------------------------- delivery */

/** The delivery note — what happened, who was there, and the babies. */
export function DeliveryNoteSheet({ deliveryId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["delivery", deliveryId],
    queryFn: () => api.get(`/deliveries/${deliveryId}/`).then((r) => r.data),
    enabled: Boolean(deliveryId),
  });

  if (isLoading || isError || !data) {
    return <SheetStatus title="Delivery note" isError={isError} onClose={onClose} />;
  }
  const babies = data.newborns ?? [];

  return (
    <PrintSheet title={`Delivery note — ${data.patient_number}`} onClose={onClose}>
      <SheetHeader documentTitle="Delivery Note" reference={data.reference}
                   date={dateTime(data.delivered_at)} />
      <PatientBlock patient={{ name: data.patient_name, patient_number: data.patient_number }} />

      <SheetSection title="Delivery">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Type" value={data.delivery_type_name} strong />
          <Field label="Delivered" value={dateTime(data.delivered_at)} strong />
          <Field label="Outcome" value={data.outcome_name || "—"} strong />
          <Field label="Babies" value={babies.length} strong />
          <Field label="Doctor" value={data.doctor_name || "—"} />
          <Field label="Midwife" value={data.midwife_name || "—"} />
          <Field label="Blood loss" value={data.estimated_blood_loss_ml
            ? `${data.estimated_blood_loss_ml} ml` : "—"} />
          <Field label="Placenta" value={data.placenta_complete === true ? "Complete"
            : data.placenta_complete === false ? "Incomplete" : "—"} />
        </div>
      </SheetSection>

      <Written title="Complications" when={(data.complication_names ?? []).length > 0}>
        <p className="text-sm text-slate-900">{(data.complication_names ?? []).join(" · ")}</p>
      </Written>

      <SheetSection title={babies.length > 1 ? `Babies (${babies.length})` : "Baby"}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left">
              <th className="py-1">#</th><th>Name</th><th>Sex</th><th>Weight</th>
              <th>Apgar 1/5/10</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {babies.map((baby) => (
              <tr key={baby.id} className="border-b border-slate-300">
                <td className="py-1">{baby.birth_order}</td>
                <td>{baby.name || "—"}</td>
                <td>{baby.sex_label}</td>
                <td>{baby.birth_weight_grams ? `${baby.birth_weight_grams} g` : "—"}</td>
                <td>{[baby.apgar_1_min, baby.apgar_5_min, baby.apgar_10_min]
                  .map((score) => score ?? "—").join(" / ")}</td>
                <td>{baby.status_name || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </SheetSection>

      <Written title="Notes" when={data.notes}>
        <p className="whitespace-pre-wrap text-sm text-slate-900">{data.notes}</p>
      </Written>

      <SheetFooter signatory="Delivered by" note="File with the mother's case notes." />
    </PrintSheet>
  );
}

/** The birth record for one baby — the document the family is given. */
export function BirthRecordSheet({ newborn, onClose }) {
  if (!newborn) {
    return <SheetStatus title="Birth record" isError
                        message="No baby is selected." onClose={onClose} />;
  }
  return (
    <PrintSheet title={`Birth record — ${newborn.reference}`} onClose={onClose}>
      <SheetHeader documentTitle="Record of Birth" reference={newborn.reference}
                   date={dateOnly(new Date())} />

      <div className="mb-6 rounded-lg border-2 border-slate-800 px-6 py-4 text-center">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-700">Baby</p>
        <p className="mt-1 text-3xl font-bold tracking-wide text-slate-900">
          {newborn.name || `Baby ${newborn.birth_order}`}
        </p>
      </div>

      <SheetSection title="Birth">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Mother" value={newborn.mother_name} strong />
          <Field label="Sex" value={newborn.sex_label} strong />
          <Field label="Birth order" value={newborn.birth_order} />
          <Field label="Weight" value={newborn.birth_weight_grams
            ? `${newborn.birth_weight_grams} g` : "—"} strong />
          <Field label="Apgar 1 min" value={newborn.apgar_1_min ?? "—"} />
          <Field label="Apgar 5 min" value={newborn.apgar_5_min ?? "—"} />
          <Field label="Apgar 10 min" value={newborn.apgar_10_min ?? "—"} />
          <Field label="Status" value={newborn.status_name || "—"} />
        </div>
      </SheetSection>

      <Written title="Congenital abnormalities" when={newborn.congenital_abnormalities}>
        <p className="whitespace-pre-wrap text-sm text-slate-900">
          {newborn.congenital_abnormalities}
        </p>
      </Written>

      <SheetFooter signatory="Midwife / Doctor"
                   note="This is a hospital record of birth, not a birth certificate." />
    </PrintSheet>
  );
}

/* ----------------------------------------------------------- postpartum */

/** The postpartum summary: every check on mother and baby since delivery. */
export function PostpartumSummarySheet({ deliveryId, onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["delivery", deliveryId],
    queryFn: () => api.get(`/deliveries/${deliveryId}/`).then((r) => r.data),
    enabled: Boolean(deliveryId),
  });

  if (isLoading || isError || !data) {
    return <SheetStatus title="Postpartum summary" isError={isError} onClose={onClose} />;
  }
  const checks = data.postpartum_visits ?? [];
  const latest = checks[0];

  return (
    <PrintSheet title={`Postpartum summary — ${data.patient_number}`} onClose={onClose}>
      <SheetHeader documentTitle="Postpartum Summary" reference={data.reference}
                   date={dateOnly(new Date())} />
      <PatientBlock patient={{ name: data.patient_name, patient_number: data.patient_number }} />

      <SheetSection title="Delivery">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          <Field label="Delivered" value={dateTime(data.delivered_at)} strong />
          <Field label="Type" value={data.delivery_type_name} strong />
          <Field label="Babies" value={(data.newborns ?? []).length} />
          <Field label="Outcome" value={data.outcome_name || "—"} />
        </div>
      </SheetSection>

      {checks.length === 0 ? (
        <SheetSection title="Postpartum checks">
          <p className="text-sm text-slate-900">No postpartum check has been recorded yet.</p>
        </SheetSection>
      ) : (
        <SheetSection title={`Postpartum checks (${checks.length})`}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left">
                <th className="py-1">Seen</th><th>Mother</th><th>Bleeding</th>
                <th>Breastfeeding</th><th>By</th>
              </tr>
            </thead>
            <tbody>
              {[...checks].reverse().map((check) => (
                <tr key={check.id} className="border-b border-slate-300">
                  <td className="py-1">{dateTime(check.seen_at)}</td>
                  <td>{check.mother_condition_name || "—"}</td>
                  <td>{check.bleeding || "—"}</td>
                  <td>{check.breastfeeding_established === true ? "Established"
                    : check.breastfeeding_established === false ? "Not yet" : "—"}</td>
                  <td>{check.recorded_by_name || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </SheetSection>
      )}

      <Written title="Family planning" when={latest?.family_planning_name}>
        <p className="text-sm text-slate-900">{latest?.family_planning_name}</p>
      </Written>

      <Written title="Advice on discharge" when={latest?.discharge_advice}>
        <p className="whitespace-pre-wrap text-sm text-slate-900">{latest?.discharge_advice}</p>
      </Written>

      <SheetFooter signatory="Midwife / Doctor" />
    </PrintSheet>
  );
}

/* ------------------------------------------------------------ discharge */

/**
 * The two discharge letters, from one component.
 *
 * Mother and baby leave together and their letters carry the same delivery,
 * so writing them twice would be two descriptions of one birth free to
 * disagree — the reasoning `AdmissionDetail.jsx` follows for the ward.
 * `who` is what differs, and it is all that differs.
 */
export function MaternityDischargeLetterSheet({ deliveryId, newborn, who = "mother", onClose }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["delivery", deliveryId],
    queryFn: () => api.get(`/deliveries/${deliveryId}/`).then((r) => r.data),
    enabled: Boolean(deliveryId),
  });

  const forBaby = who === "newborn";
  const title = forBaby ? "Newborn Discharge Letter" : "Mother Discharge Letter";

  if (isLoading || isError || !data) {
    return <SheetStatus title={title} isError={isError} onClose={onClose} />;
  }
  const baby = forBaby ? (newborn ?? (data.newborns ?? [])[0]) : null;
  if (forBaby && !baby) {
    return <SheetStatus title={title} isError
                        message="No baby was recorded on this delivery." onClose={onClose} />;
  }
  const latest = (data.postpartum_visits ?? [])[0];

  return (
    <PrintSheet title={`${title} — ${data.patient_number}`} onClose={onClose}>
      <SheetHeader documentTitle={title}
                   reference={forBaby ? baby.reference : data.reference}
                   date={dateOnly(new Date())} />

      <PatientBlock patient={forBaby
        ? { name: baby.name || `Baby ${baby.birth_order}`, patient_number: baby.reference }
        : { name: data.patient_name, patient_number: data.patient_number }} />

      {forBaby ? (
        <SheetSection title="Birth">
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
            <Field label="Mother" value={data.patient_name} strong />
            <Field label="Born" value={dateTime(data.delivered_at)} strong />
            <Field label="Sex" value={baby.sex_label} strong />
            <Field label="Birth weight" value={baby.birth_weight_grams
              ? `${baby.birth_weight_grams} g` : "—"} strong />
            <Field label="Apgar 1/5" value={`${baby.apgar_1_min ?? "—"} / ${baby.apgar_5_min ?? "—"}`} />
            <Field label="Condition" value={baby.status_name || "—"} />
          </div>
        </SheetSection>
      ) : (
        <SheetSection title="Delivery">
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
            <Field label="Delivered" value={dateTime(data.delivered_at)} strong />
            <Field label="Type" value={data.delivery_type_name} strong />
            <Field label="Outcome" value={data.outcome_name || "—"} />
            <Field label="Babies" value={(data.newborns ?? []).length} strong />
            <Field label="Condition" value={latest?.mother_condition_name || "—"} />
            <Field label="Family planning" value={latest?.family_planning_name || "—"} />
          </div>
        </SheetSection>
      )}

      <Written title="Advice" when={latest?.discharge_advice && !forBaby}>
        <p className="whitespace-pre-wrap text-sm text-slate-900">{latest?.discharge_advice}</p>
      </Written>

      <Written title="Notes" when={forBaby && baby.notes}>
        <p className="whitespace-pre-wrap text-sm text-slate-900">{baby.notes}</p>
      </Written>

      <SheetSection title="Follow-up">
        {latest?.follow_up_on
          ? <p className="text-sm text-slate-900">Return on {dateOnly(latest.follow_up_on)}.</p>
          : <WriteInLines rows={3} />}
      </SheetSection>

      <SheetFooter signatory="Discharged by"
                   note="Bring this letter to every follow-up visit." />
    </PrintSheet>
  );
}
