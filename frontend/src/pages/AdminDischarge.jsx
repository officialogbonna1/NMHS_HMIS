import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "../api/client.js";
import { errorCode, errorMessage } from "../api/errors.js";
import { useToast } from "../components/Toaster.jsx";
import { PrintButton } from "../components/printing.jsx";
import { AdmissionDetail } from "../components/AdmissionDetail.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Alert, Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Field,
  FilterBar, Input, Modal, Page, PageHeader, SearchInput, Section, Select, SkeletonRows,
  StatCard, Table, TableWrap, Td, Textarea, Th, THead, Tr,
} from "../components/ui.jsx";

/**
 * The Admin Discharge workspace — Super Admin only.
 *
 * A second **door** onto the ward's discharge, never a second system: every
 * write here posts to `/admin-discharges/discharge/`, which calls the same
 * `inpatient/services.discharge_patient` the ward's own endpoint calls, onto
 * the same `DischargeSummary` row. The ward keeps its discharge exactly as it
 * was — a nurse, a ward manager, a doctor and the eye doctor discharge from
 * the bed board as before.
 *
 * The seven steps the brief asks for, in the order the screen offers them:
 * search a patient on a ward, read their admission, fill the discharge in,
 * confirm it, save it, and then print the completed letter — which is the
 * existing print registry (`printing.jsx` → `DischargeLetterSheet`), not a
 * second printing framework.
 *
 * The guard on this route is a courtesy (rule 28): `IsSuperAdmin` on the API
 * is what actually refuses, and a `hospital_admin` who reached this page by
 * typing the URL would simply be 403'd by every request it makes.
 */

const BLANK = {
  diagnosis: "", summary: "", instructions: "", follow_up: "", condition: "",
};

const CONDITIONS = [
  "Recovered", "Improved", "Unchanged", "Referred to another facility",
  "Discharged against medical advice", "Deceased",
];

export default function AdminDischarge() {
  // Without a role `PrintButton` resolves no documents and renders nothing —
  // see the note in DischargedPatients.jsx.
  const { user } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [ward, setWard] = useState("");
  const [viewing, setViewing] = useState(null);        // the admission being read
  const [selected, setSelected] = useState(null);      // the admission being discharged
  const [form, setForm] = useState(BLANK);
  const [confirming, setConfirming] = useState(false);
  const [blocked, setBlocked] = useState(null);        // an existing discharge, if any

  const onWard = useQuery({
    queryKey: ["dischargeable", search, ward],
    queryFn: async () =>
      (await api.get("/admin-discharges/dischargeable/", {
        // Both filters are applied in the database (`dischargeable`), so this
        // is the ward's actual list and not a page of it narrowed here.
        params: { ...(search ? { search } : {}), ...(ward ? { ward } : {}) },
      })).data,
  });

  // The wards themselves, for the filter. `/wards/` is the existing
  // configuration endpoint the bed board already reads.
  const wards = useQuery({
    queryKey: ["wards", "active"],
    queryFn: async () => (await api.get("/wards/", { params: { is_active: true } })).data,
    staleTime: 300000,
  });
  const wardRows = useMemo(() => {
    const data = wards.data;
    return Array.isArray(data) ? data : data?.results ?? [];
  }, [wards.data]);

  const rows = useMemo(() => {
    const data = onWard.data;
    return Array.isArray(data) ? data : data?.results ?? [];
  }, [onWard.data]);

  // How many wards the list currently spans — the second figure a desk reads
  // after "how many patients", and it moves with the ward filter.
  const wardCount = useMemo(
    () => new Set(rows.map((row) => row.ward_name).filter(Boolean)).size,
    [rows],
  );

  const discharge = useMutation({
    mutationFn: (payload) => api.post("/admin-discharges/discharge/", payload),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ["dischargeable"] });
      queryClient.invalidateQueries({ queryKey: ["discharged"] });
      queryClient.invalidateQueries({ queryKey: ["admissions"] });
      setConfirming(false);
      setSelected(null);
      setViewing(null);
      setForm(BLANK);
      showToast({
        title: "Patient discharged",
        message: `${response.data.patient_name} — ${response.data.reference}. `
          + "The discharge letter can be printed from Discharged patients.",
      });
    },
    onError: (error) => {
      setConfirming(false);
      // Rule 9: a discharge that already exists is shown, never repeated.
      if (errorCode(error) === "already_discharged") {
        setBlocked(error.response?.data?.discharge ?? null);
        queryClient.invalidateQueries({ queryKey: ["dischargeable"] });
        return;
      }
      showToast({ title: "Could not discharge", message: errorMessage(error), tone: "error" });
    },
  });

  const ready = form.diagnosis.trim() && form.summary.trim();

  return (
    <Page>
      {/* Title → summary → filters → the list. The counts used to sit in the
          masthead's `meta` strip, where a figure and its label read as one
          more line of prose under the description; the ward select sat up
          there too, so the page's only filter was half a masthead away from
          the list it narrows. */}
      <PageHeader
        icon="discharge"
        title="Discharge patient"
        description="Everyone currently admitted, and the workspace for closing an admission — complete the discharge record, then print the letter."
        actions={
          <Button variant="soft" onClick={() => onWard.refetch()} loading={onWard.isFetching}>
            {onWard.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {/* The two figures as figures: number first, large and tabular, with
          what it counts beneath it. Same card chrome as the finance summary,
          so the application reads as one product. */}
      <section aria-label="Ward summary" className="mb-4 grid grid-cols-2 gap-3 sm:mb-5 sm:max-w-md">
        <StatCard
          value={rows.length}
          label={`Patient${rows.length === 1 ? "" : "s"} on wards`}
          hint={ward ? "In the selected ward" : "Across every ward"}
          tone={rows.length > 0 ? "brand" : "slate"}
        />
        <StatCard
          value={wardCount}
          label={`Ward${wardCount === 1 ? "" : "s"} occupied`}
          hint="With at least one patient"
        />
      </section>

      <FilterBar title="Find a patient" columns={2} className="mb-4 sm:mb-5">
        <SearchInput
          showLabel
          value={search}
          onChange={setSearch}
          label="Search"
          placeholder="Name, hospital no. or ADM-000123…"
        />
        <Field label="Ward">
          <Select value={ward} onChange={(e) => setWard(e.target.value)}>
            <option value="">Every ward</option>
            {wardRows.map((row) => (
              <option key={row.id} value={row.id}>{row.name}</option>
            ))}
          </Select>
        </Field>
      </FilterBar>

      {blocked && (
        <Alert tone="warning" title="This admission has already been discharged">
          <p className="text-sm">
            {blocked.patient_name} was discharged on{" "}
            {blocked.discharged_at ? new Date(blocked.discharged_at).toLocaleString() : "an earlier date"}
            {blocked.completed_by_name ? ` by ${blocked.completed_by_name}` : ""} under reference{" "}
            <strong>{blocked.reference}</strong>. It has not been discharged again and no
            notification was sent.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <PrintButton
              role={user?.role}
              size="xs"
              documents={["discharge_letter"]}
              context={{ discharge: blocked, dischargeId: blocked.id }}
              label="Print existing letter"
            />
            <Button variant="secondary" size="xs" onClick={() => setBlocked(null)}>
              Dismiss
            </Button>
          </div>
        </Alert>
      )}

      {/* The heading names the list; the line under it is secondary and must
          not compete with it, so it is `slate-600` supporting copy rather than
          a second heading. Loading, empty and the table itself all sit on one
          `Card`, so the section never reads as a sparse box with a sentence
          floating in it. */}
      <Section
        title="On the ward"
        description="Everyone currently admitted. Discharging closes the admission and releases the bed."
      >
        {(onWard.isLoading || onWard.isError || rows.length === 0) && (
          <Card>
            {onWard.isLoading && <div className="p-3 sm:p-4"><SkeletonRows rows={4} /></div>}
            {onWard.isError && (
              <ErrorState
                title="Could not load the ward"
                description={errorMessage(onWard.error)}
                onRetry={() => onWard.refetch()}
              />
            )}
            {!onWard.isLoading && !onWard.isError && rows.length === 0 && (
              <EmptyState
                icon="discharge"
                title={search || ward ? "No admitted patient matches" : "Nobody is currently admitted"}
                description={search || ward
                  ? "Clear the search or choose another ward — the wards themselves may not be empty."
                  : "Patients admitted from the bed board appear here, ready to be discharged."}
                action={(search || ward)
                  ? (
                    <Button variant="secondary" size="sm"
                            onClick={() => { setSearch(""); setWard(""); }}>
                      Clear filters
                    </Button>
                  )
                  : <Button variant="secondary" size="sm" to="/admissions">Open the bed board</Button>}
              />
            )}
          </Card>
        )}
        {rows.length > 0 && (
          <Card>
            <TableWrap>
              <Table>
                <THead>
                  <Tr>
                    <Th>Patient</Th>
                    <Th>Hospital no.</Th>
                    <Th>Admission</Th>
                    <Th>Ward / bed</Th>
                    <Th>Admitted</Th>
                    <Th>Stay</Th>
                    <Th>Attending</Th>
                    <Th>Status</Th>
                    <Th className="text-right">Actions</Th>
                  </Tr>
                </THead>
                <tbody>
                  {rows.map((admission) => {
                    const open = viewing?.id === admission.id;
                    return (
                      <Tr
                        key={admission.id}
                        className={open ? "bg-brand-50/50" : undefined}
                      >
                        <Td className="font-medium text-slate-900">{admission.patient_name}</Td>
                        <Td className="whitespace-nowrap text-slate-700">{admission.patient_number}</Td>
                        <Td className="whitespace-nowrap font-medium">{admission.reference}</Td>
                        <Td className="text-slate-700">
                          <div>{admission.ward_name}</div>
                          <div className="text-xs text-slate-600">Bed {admission.bed_number}</div>
                        </Td>
                        <Td className="whitespace-nowrap text-slate-700">
                          {formatDate(admission.admitted_at)}
                        </Td>
                        <Td className="whitespace-nowrap text-slate-700">
                          {admission.length_of_stay || "—"}
                        </Td>
                        <Td className="text-slate-700">{admission.attending_doctor_name || "—"}</Td>
                        <Td>
                          <Badge tone="brand">{admission.status_label ?? admission.status}</Badge>
                        </Td>
                        <Td>
                          {/* Read it before acting on it. Discharge is the
                              consequential button, so View sits beside it as
                              the quieter one rather than behind it. */}
                          <div className="flex justify-end gap-2">
                            <Button
                              variant="secondary"
                              size="xs"
                              onClick={() => { setBlocked(null); setViewing(admission); }}
                            >
                              View
                            </Button>
                            <Button
                              size="xs"
                              onClick={() => {
                                setBlocked(null);
                                setViewing(admission);
                                setSelected(admission);
                                setForm(BLANK);
                              }}
                            >
                              Discharge
                            </Button>
                          </div>
                        </Td>
                      </Tr>
                    );
                  })}
                </tbody>
              </Table>
            </TableWrap>
          </Card>
        )}
      </Section>

      {/* Step 3 and 4: read the admission. Shown whether the administrator
          pressed View or Discharge, so the decision is always made against the
          record rather than against a row in a table. The same component
          Discharged patients renders beside a completed summary. */}
      {viewing && (
        <Section
          title="Admission"
          description="What is being discharged. Demographics and the stay — the chart itself stays where it is."
          actions={
            <div className="flex flex-wrap gap-2">
              {!selected && (
                <Button
                  size="xs"
                  onClick={() => { setSelected(viewing); setForm(BLANK); }}
                >
                  Discharge this patient
                </Button>
              )}
              <Button variant="secondary" size="xs" onClick={() => setViewing(null)}>
                Close
              </Button>
            </div>
          }
        >
          <AdmissionDetail admission={viewing} />
        </Section>
      )}

      {selected && (
        <Card className="mt-6">
          <CardHeader
            title={`Discharge ${selected.patient_name}`}
            description={`${selected.patient_number} · ${selected.ward_name} · Bed ${selected.bed_number}`}
            actions={
              <Button variant="secondary" size="xs" onClick={() => setSelected(null)}>
                Cancel
              </Button>
            }
          />
          <CardBody>
            <dl className="mb-5 grid gap-3 sm:grid-cols-3">
              <Fact label="Admitted" value={selected.admitted_at ? new Date(selected.admitted_at).toLocaleString() : "—"} />
              <Fact label="Attending doctor" value={selected.attending_doctor_name || "—"} />
              <Fact label="Admission diagnosis" value={selected.diagnosis || "—"} />
            </dl>

            <div className="grid gap-4">
              <Field label="Discharge diagnosis" required>
                <Textarea
                  rows={2}
                  value={form.diagnosis}
                  onChange={(e) => setForm({ ...form, diagnosis: e.target.value })}
                />
              </Field>
              <Field label="Summary of the admission" required>
                <Textarea
                  rows={4}
                  value={form.summary}
                  onChange={(e) => setForm({ ...form, summary: e.target.value })}
                />
              </Field>
              <Field label="Discharge instructions" hint="Medication, wound care, activity.">
                <Textarea
                  rows={3}
                  value={form.instructions}
                  onChange={(e) => setForm({ ...form, instructions: e.target.value })}
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Condition on discharge">
                  <Select
                    value={form.condition}
                    onChange={(e) => setForm({ ...form, condition: e.target.value })}
                  >
                    <option value="">Not stated</option>
                    {CONDITIONS.map((c) => <option key={c} value={c}>{c}</option>)}
                  </Select>
                </Field>
                <Field label="Follow-up date">
                  <Input
                    type="date"
                    value={form.follow_up}
                    onChange={(e) => setForm({ ...form, follow_up: e.target.value })}
                  />
                </Field>
              </div>
            </div>
          </CardBody>
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-5 py-4">
            {!ready && (
              <p className="mr-auto text-sm text-slate-600">
                A discharge diagnosis and a summary are required.
              </p>
            )}
            <Button disabled={!ready} onClick={() => setConfirming(true)}>
              Review and discharge
            </Button>
          </div>
        </Card>
      )}

      <Modal
        open={confirming}
        onClose={() => setConfirming(false)}
        title="Confirm discharge"
        description={selected ? `${selected.patient_name} · ${selected.patient_number}` : ""}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirming(false)}>Back</Button>
            <Button
              disabled={discharge.isPending}
              onClick={() => discharge.mutate({
                admission: selected.id,
                diagnosis: form.diagnosis,
                summary: form.summary,
                instructions: form.instructions,
                condition: form.condition,
                follow_up: form.follow_up || null,
              })}
            >
              {discharge.isPending ? "Discharging…" : "Confirm discharge"}
            </Button>
          </>
        }
      >
        <dl className="grid gap-2 sm:grid-cols-2">
          <Fact label="Patient" value={selected?.patient_name} />
          <Fact label="Admission" value={selected?.reference} />
          <Fact label="Ward" value={selected?.ward_name} />
          <Fact label="Bed" value={selected?.bed_number ? `Bed ${selected.bed_number}` : ""} />
        </dl>
        <p className="mt-4 text-sm text-slate-700">
          Confirming closes this admission and releases{" "}
          <strong>{selected ? `${selected.ward_name} bed ${selected.bed_number}` : "the bed"}</strong>,
          and notifies the hospital administrator. The discharge letter can be
          printed afterwards from Discharged patients.
        </p>
        <dl className="mt-4 grid gap-2">
          <Fact label="Discharge diagnosis" value={form.diagnosis} />
          {form.condition && <Fact label="Condition" value={form.condition} />}
        </dl>
      </Modal>
    </Page>
  );
}

function Fact({ label, value }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-800 break-words">{value || "—"}</dd>
    </div>
  );
}

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString() : "—";
}
