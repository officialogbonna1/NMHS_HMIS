import { useQuery } from "@tanstack/react-query";
import api from "../api/client.js";
import { Badge, Card, CardBody, CardHeader, Skeleton } from "./ui.jsx";

/**
 * One admission, read the way a ward round reads it: who the patient is, where
 * they are, how long they have been there, and every bed they have been moved
 * between.
 *
 * **One component, two screens.** The Admin Discharge workspace shows it before
 * discharging (so the decision is made against the record rather than against a
 * row in a table), and Discharged Patients shows it afterwards beside the
 * completed summary. A second copy of this block would be two descriptions of
 * one admission free to disagree — the same reason `StockPanels.jsx` serves
 * both stock workspaces and `CancelServiceModal` both money desks.
 *
 * Everything here comes off the existing `AdmissionSerializer` row the caller
 * already has, plus `/bed-transfers/?admission=` for the moves. Nothing is
 * computed here that the server does not already report: `reference`,
 * `length_of_stay` and `status_label` are the serializer's own derived fields,
 * so a list and a detail view can never disagree about how long somebody has
 * been in.
 *
 * **It shows an admission, not a chart.** Demographics, where the patient is
 * and the admission diagnosis the ward already typed — no notes, no results, no
 * money. An administrator discharging a patient needs to know who and where,
 * and "Admin has access" is not a reason to open the record wider than the job.
 */
export function AdmissionDetail({ admission, title = "Admission", children }) {
  const transfers = useQuery({
    queryKey: ["bed-transfers", admission?.id],
    queryFn: async () =>
      (await api.get("/bed-transfers/", { params: { admission: admission.id } })).data,
    enabled: Boolean(admission?.id),
  });

  if (!admission) return null;

  const moves = Array.isArray(transfers.data) ? transfers.data : transfers.data?.results ?? [];
  const discharged = admission.status === "discharged";

  return (
    <Card>
      <CardHeader
        title={title}
        description={`${admission.patient_name ?? ""}${
          admission.patient_number ? ` · ${admission.patient_number}` : ""}`}
        actions={
          <Badge tone={discharged ? "success" : admission.status === "cancelled" ? "neutral" : "brand"}>
            {admission.status_label ?? admission.status}
          </Badge>
        }
      />
      <CardBody className="space-y-6">
        <Block title="Patient">
          <Fact label="Name" value={admission.patient_name} />
          <Fact label="Hospital number" value={admission.patient_number} />
          <Fact label="Sex" value={admission.patient_sex} />
          <Fact label="Age" value={admission.patient_age} />
        </Block>

        <Block title="Admission">
          <Fact label="Reference" value={admission.reference} />
          <Fact label="Ward" value={admission.ward_name} />
          <Fact label="Bed" value={admission.bed_number ? `Bed ${admission.bed_number}` : ""} />
          <Fact label="Admitted" value={dateTime(admission.admitted_at)} />
          <Fact label="Length of stay" value={admission.length_of_stay} />
          <Fact label="Attending doctor" value={admission.attending_doctor_name} />
          <Fact label="Admitted by" value={admission.admitted_by_name} />
          {discharged && <Fact label="Discharged" value={dateTime(admission.discharged_at)} />}
        </Block>

        {admission.diagnosis && (
          <Block title="Admission diagnosis" columns={1}>
            <p className="whitespace-pre-wrap text-sm text-slate-800">{admission.diagnosis}</p>
          </Block>
        )}

        {/* Where the patient has actually been. A stay that moved wards is the
            one a discharge letter gets wrong, so the moves are shown rather
            than only the bed they happen to be in now. */}
        <section className="min-w-0">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-700">
            Bed history
          </h3>
          {transfers.isLoading && <Skeleton className="mt-2 h-10 w-full" />}
          {!transfers.isLoading && moves.length === 0 && (
            <p className="mt-2 text-sm text-slate-600">
              No bed moves — the patient has been in{" "}
              {admission.ward_name ? `${admission.ward_name}, bed ${admission.bed_number}` : "one bed"}{" "}
              throughout.
            </p>
          )}
          {moves.length > 0 && (
            <ol className="mt-2 space-y-2">
              {moves.map((move) => (
                <li key={move.id} className="min-w-0 rounded-lg border border-slate-200 px-3 py-2">
                  <p className="text-sm text-slate-800">
                    <span className="font-medium">
                      {move.from_ward_name} · Bed {move.from_bed_number}
                    </span>
                    {" → "}
                    <span className="font-medium">
                      {move.to_ward_name} · Bed {move.to_bed_number}
                    </span>
                  </p>
                  <p className="mt-0.5 text-xs text-slate-600">
                    {dateTime(move.created_at)}
                    {move.transferred_by_name && ` · ${move.transferred_by_name}`}
                    {move.reason && ` · ${move.reason}`}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </section>

        {children}
      </CardBody>
    </Card>
  );
}

/**
 * The completed discharge, read back. Says **DISCHARGED** and when, because
 * that is the one thing somebody opening a historical record is checking.
 *
 * Read-only by design: `DischargeSummary` has no amendment trail and the API
 * offers no update (`http_method_names` is GET/POST), so the record stays as it
 * was signed. Editing it in place would destroy exactly the history the letter
 * is evidence of — the reasoning rule 2 applies to a locked clinical record.
 */
export function DischargeDetail({ discharge }) {
  if (!discharge) return null;
  return (
    <Card>
      <CardHeader
        title="Discharge record"
        description={`${discharge.reference ?? ""}${
          discharge.patient_name ? ` · ${discharge.patient_name}` : ""}`}
        actions={<Badge tone="success">DISCHARGED</Badge>}
      />
      <CardBody className="space-y-6">
        <Block title="Discharge">
          <Fact label="Discharge reference" value={discharge.reference} />
          <Fact label="Admission reference" value={discharge.admission_reference} />
          <Fact label="Discharged" value={dateTime(discharge.discharged_at)} />
          <Fact label="Discharged by" value={discharge.completed_by_name} />
          <Fact label="Condition on discharge" value={discharge.condition} />
          <Fact label="Follow-up" value={date(discharge.follow_up)} />
        </Block>

        <Block title="Discharge diagnosis" columns={1}>
          <p className="whitespace-pre-wrap text-sm text-slate-800">{discharge.diagnosis || "—"}</p>
        </Block>
        <Block title="Summary of the admission" columns={1}>
          <p className="whitespace-pre-wrap text-sm text-slate-800">{discharge.summary || "—"}</p>
        </Block>
        {discharge.instructions && (
          <Block title="Instructions" columns={1}>
            <p className="whitespace-pre-wrap text-sm text-slate-800">{discharge.instructions}</p>
          </Block>
        )}

        <p className="text-xs text-slate-600">
          A completed discharge is a historical record and is not edited. A patient admitted
          again is a new admission with a discharge of its own.
        </p>
      </CardBody>
    </Card>
  );
}

function Block({ title, columns = 2, children }) {
  return (
    <section className="min-w-0">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-700">{title}</h3>
      <dl className={`mt-2 grid gap-3 ${columns === 1 ? "" : "sm:grid-cols-2 lg:grid-cols-4"}`}>
        {children}
      </dl>
    </section>
  );
}

function Fact({ label, value }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="break-words text-sm text-slate-800">{value || "—"}</dd>
    </div>
  );
}

function dateTime(value) {
  return value ? new Date(value).toLocaleString() : "";
}

function date(value) {
  return value ? new Date(value).toLocaleDateString() : "";
}
