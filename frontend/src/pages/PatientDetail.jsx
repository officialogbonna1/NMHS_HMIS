import { useEffect, useState } from "react";
import { useParams, useNavigate, Routes, Route, useLocation } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { looksLikeUuid, patientNumber } from "../components/patientIdentity.js";
import { PrintButton, chartDocuments } from "../components/printing.jsx";
import HealthRecordTile from "../components/HealthRecordTile.jsx";
import GenericTileModal from "../components/GenericTileModal.jsx";
import { TILE_CONFIGS } from "../components/tileConfig.js";
import MedicalNotesTab from "./MedicalNotesTab.jsx";
import VitalsTab from "./VitalsTab.jsx";
import PatientBillingTab from "./PatientBillingTab.jsx";
import PatientOverview from "./PatientOverview.jsx";
import LabResultsTab from "./LabResultsTab.jsx";
import ReferralResultsTab from "./ReferralResultsTab.jsx";
import AdmissionTab from "./AdmissionTab.jsx";
import PharmacyTab from "./PharmacyTab.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { CLINICAL_ROLES, hasRole, isSuperAdmin } from "../auth/roles.js";
import DeletePatientModal from "../components/DeletePatientModal.jsx";
import { Page, Skeleton, Breadcrumb, Button, TabBar, Tab, ErrorState } from "../components/ui.jsx";
import { Icon } from "../components/icons.jsx";

const TILES = [
  { key: "allergies", title: "Allergies", icon: "⚠️" },
  { key: "medications", title: "Medication", icon: "💊" },
  { key: "conditions", title: "Medical Conditions", icon: "❤️" },
  { key: "devices", title: "Medical Devices", icon: "🖥" },
  { key: "surgical-history", title: "Surgical History", icon: "🩺" },
  { key: "family-history", title: "Family Medical History", icon: "👪" },
  { key: "social-history", title: "Social History", icon: "🥤" },
  { key: "vaccinations", title: "Vaccinations", icon: "💉" },
  { key: "medical-tests", title: "Medical Tests & Diagnostics", icon: "🧪" },
];

// Mirrors the manual's Health Record screen: tabs for Health Record /
// Medical Notes / Vitals, with the nine tiles (Allergies, Medications, etc).
export default function PatientDetail() {
  // Three identifiers, three jobs — see `components/patientIdentity.js`.
  //
  //   routeParam   what the browser is showing. A UUID normally; the integer pk
  //                when an old bookmark or an already-sent notification is
  //                being followed, which is why it is not assumed to be either.
  //   patientUuid  the browser and patient-API identity — every chart link and
  //                every `/patients/<…>/` fetch below.
  //   patientId    the integer pk. Still the internal identity: it keys the
  //                caches other pages invalidate, and it is what the nested
  //                endpoints' `?patient=` filters and FK write bodies take.
  //
  // The chart resolves the pair once, here, and hands each tab the one it
  // actually needs, rather than every tab guessing.
  const { patientUuid: routeParam } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuth();
  const role = user?.role;
  // The clinicians — the general doctor and the eye doctor — read the chart.
  // Which patients they may open is the API's decision, not this list's.
  const isClinical = hasRole(user, CLINICAL_ROLES);
  // Nurses take vitals; clinicians read them for the patients assigned to
  // them. The Add button inside VitalsTab is what's limited to nurses.
  const canSeeVitals = hasRole(user, [...CLINICAL_ROLES, "nurse"]);
  const canPrescribe = hasRole(user, CLINICAL_ROLES);
  const canBill = ["admin", "hospital_admin", "cashier", "accountant", "reception"].includes(role);
  // The ward and the units' answers are clinical reading, so they follow the
  // same roles as the rest of the chart. A ward nurse arguably needs both at
  // the bedside — but `/prescriptions/` is doctor/pharmacist only, and a tab
  // that 403s is worse than no tab. Widening that permission is the change
  // to make if the ward asks for it, not a client-side guess.
  const canSeeCare = isClinical;
  // The pharmacy dispensed the drugs, so the pharmacy can read them back.
  // It is the only chart tab a pharmacist gets: they need "what did I give
  // this patient?", not the clinical record behind it.
  const canSeePharmacy = isClinical || role === "pharmacist";
  // Permanently deleting a patient is the Super Admin's alone — deliberately
  // not `hasRole`, which lets every ADMIN_ROLE through. Hiding the button is
  // housekeeping; `IsSuperAdmin` on the API is the control.
  const canDelete = isSuperAdmin(user);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  // `/api/patients/<x>/` resolves a UUID or the legacy integer pk, so one
  // fetch serves both kinds of URL — and it is the API, not this component,
  // that decides whether this role may see this patient.
  const { data: patient, isError } = useQuery({
    queryKey: ["patient", routeParam],
    queryFn: () => api.get(`/patients/${routeParam}/`).then((r) => r.data),
    retry: false,
  });

  const patientId = patient?.id;
  const patientUuid = patient?.uuid ?? routeParam;

  // An old `/patients/13` bookmark, or a notification raised before the URL
  // moved, still opens the chart — and then quietly becomes the UUID form, so
  // the address bar and everything copied out of it are canonical from here
  // on. `replace` keeps the back button pointing where the reader came from.
  useEffect(() => {
    if (!patient?.uuid || looksLikeUuid(routeParam)) return;
    navigate(location.pathname.replace(`/patients/${routeParam}`,
                                       `/patients/${patient.uuid}`) + location.search,
             { replace: true });
  }, [patient?.uuid, routeParam, location.pathname, location.search, navigate]);

  // Everything a department has sent back about this patient, each on its
  // own tab so a doctor can go straight to the answer they are chasing
  // rather than scrolling one long chart.
  const SECTION_TABS = ["notes", "vitals", "billing", "record", "lab", "ultrasound",
                        "eye", "procedure", "admission", "pharmacy"];
  const matched = SECTION_TABS.find((t) => location.pathname.endsWith(`/${t}`));
  const tab = matched
    // A doctor opens on the overview — the whole chart at a glance — and
    // steps into the individual sections from there.
    ?? (isClinical ? "overview"
      : canSeeVitals ? "vitals"
      : canBill ? "billing"
      : canSeePharmacy ? "pharmacy"
      : "record");

  const initials = `${patient?.first_name?.[0] ?? ""}${patient?.last_name?.[0] ?? ""}`.toUpperCase();

  return (
    <Page width="default">
      {/* Two levels only, and the second is the patient in front of you — a
          chart is always reached from the list, and CHART_ROLES is a subset of
          PATIENT_LOOKUP_ROLES, so the link is never a dead end. */}
      <Breadcrumb
        items={[
          { label: "Patients", to: "/patients" },
          { label: patient ? `${patient.last_name}, ${patient.first_name}` : "Patient" },
        ]}
      />

      {/* The identity band. Whoever opens this chart has to be certain, in one
          glance, which patient they are looking at — so the name, the file
          number and the age/sex sit together and wrap as a block instead of
          being squeezed by the buttons beside them. */}
      {patient ? (
        <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
          {/* Stacked on a phone. As one wrapping row the identity block —
              `flex-1`, so free to shrink below its content — was crushed to a
              sliver by the buttons, which sat on top of the patient's name. */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
            <div className="flex min-w-0 items-start gap-3 sm:flex-1">
              <span aria-hidden="true" className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-brand-100 text-base font-semibold text-brand-700">
                {initials || "?"}
              </span>
              <div className="min-w-0">
                <h1 className="truncate text-lg font-semibold text-slate-900 sm:text-xl">
                  {patient.last_name}, {patient.first_name}
                </h1>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-600">
                  {patientNumber(patient) && (
                    <span className="rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
                      Patient No. {patientNumber(patient)}
                    </span>
                  )}
                  <span>{patient.sex === "M" ? "Male" : patient.sex === "F" ? "Female" : patient.sex}</span>
                  {patient.age_display && <><span aria-hidden="true" className="text-slate-400">·</span><span>{patient.age_display}</span></>}
                  <span aria-hidden="true" className="text-slate-400">·</span>
                  <span>DOB {patient.birthdate ?? "—"}</span>
                </div>
                {patient.short_note && (
                  <p className="mt-1.5 text-sm text-slate-700">{patient.short_note}</p>
                )}
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              {/* Print means something different in every department, so the
                  button asks `components/printing.jsx` what this role, on
                  this tab, would actually want on paper: the card at the
                  front desk, the clinical summary in a consulting room, the
                  observation record at the vitals station, the dispensing
                  note at the pharmacy, the bill at the counter. Anything else
                  the role may print is behind the caret — never a second
                  button that prints the same card. */}
              <PrintButton
                role={role}
                documents={chartDocuments({ role, tab, context: { patientId, patient } })}
                context={{ patientId, patient }}
              />
              {canPrescribe && (
                <Button onClick={() => navigate(`/patients/${patientUuid}/prescribe`)}>
                  <Icon name="plus" className="h-4 w-4" aria-hidden="true" />
                  Prescribe
                </Button>
              )}
              {canDelete && (
                <Button variant="dangerOutline" onClick={() => setConfirmingDelete(true)}>
                  Delete patient
                </Button>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
          <div className="flex items-start gap-3">
            <Skeleton className="h-12 w-12 shrink-0 rounded-full" />
            <div className="min-w-0 flex-1 space-y-2">
              <Skeleton className="h-5 w-56" />
              <Skeleton className="h-4 w-72" />
            </div>
          </div>
        </div>
      )}

      {(isClinical || canSeeVitals || canBill || canSeePharmacy) && <TabBar label="Chart sections">
        {isClinical && <TabLink to={`/patients/${patientUuid}`} active={tab === "overview"}>Overview</TabLink>}
        {isClinical && <TabLink to={`/patients/${patientUuid}/record`} active={tab === "record"}>Health Record</TabLink>}
        {isClinical && <TabLink to={`/patients/${patientUuid}/notes`} active={tab === "notes"}>Medical Notes</TabLink>}
        {canSeeVitals && <TabLink to={`/patients/${patientUuid}/vitals`} active={tab === "vitals"}>Vitals</TabLink>}
        {isClinical && <TabLink to={`/patients/${patientUuid}/lab`} active={tab === "lab"}>Lab</TabLink>}
        {isClinical && <TabLink to={`/patients/${patientUuid}/ultrasound`} active={tab === "ultrasound"}>Ultrasound</TabLink>}
        {isClinical && <TabLink to={`/patients/${patientUuid}/eye`} active={tab === "eye"}>Eye</TabLink>}
        {isClinical && <TabLink to={`/patients/${patientUuid}/procedure`} active={tab === "procedure"}>Procedures</TabLink>}
        {canSeeCare && <TabLink to={`/patients/${patientUuid}/admission`} active={tab === "admission"}>Admission</TabLink>}
        {canSeePharmacy && <TabLink to={`/patients/${patientUuid}/pharmacy`} active={tab === "pharmacy"}>Pharmacy</TabLink>}
        {canBill && <TabLink to={`/patients/${patientUuid}/billing`} active={tab === "billing"}>Billing</TabLink>}
      </TabBar>}

      {!isClinical && !canSeeVitals && !canBill && !canSeePharmacy && <div className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-600">This role can view only the patient’s basic registration details. Use Appointments and Routing to continue the front-desk workflow.</div>}

      {/* A tab cannot open before the integer pk is known: the nested
          endpoints behind these panels filter on `?patient=<pk>` and the
          caches other pages invalidate are keyed on it. One skeleton while
          the chart resolves the URL, rather than eleven components each
          guessing what the address bar means. */}
      {isError ? (
        <ErrorState
          title="This chart could not be opened"
          description="The address may be wrong, or this patient is not one you have access to. Try finding them on the Patients page."
        />
      ) : !patientId ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full rounded-xl" />
          <Skeleton className="h-24 w-full rounded-xl" />
        </div>
      ) : (
        <>
      {isClinical && tab === "overview" && (
        <PatientOverview patientId={patientId} patientUuid={patientUuid} />
      )}
      {isClinical && tab === "record" && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {TILES.map((t) => (
            <TileLoader key={t.key} patientId={patientId} tileKey={t.key} title={t.title} icon={t.icon} />
          ))}
        </div>
      )}
      {isClinical && tab === "notes" && <MedicalNotesTab patientId={patientId} />}
      {canSeeVitals && tab === "vitals" && <VitalsTab patientId={patientId} />}
      {isClinical && tab === "lab" && <LabResultsTab patientId={patientId} />}
      {isClinical && ["ultrasound", "eye", "procedure"].includes(tab) && (
        <ReferralResultsTab patientId={patientId} patientUuid={patientUuid} kind={tab} />
      )}
      {canSeeCare && tab === "admission" && <AdmissionTab patientId={patientId} />}
      {canSeePharmacy && tab === "pharmacy" && (
        <PharmacyTab patientId={patientId} patientUuid={patientUuid} canPrescribe={canPrescribe} />
      )}
      {canBill && tab === "billing" && (
        <PatientBillingTab patientId={patientId} patientUuid={patientUuid} />
      )}
        </>
      )}

      {confirmingDelete && patient && (
        <DeletePatientModal patient={patient} onClose={() => setConfirmingDelete(false)} />
      )}
    </Page>
  );
}

function TabLink({ to, active, children }) {
  return <Tab to={to} active={active}>{children}</Tab>;
}

function TileLoader({ patientId, tileKey, title, icon }) {
  // `editing` is either null (closed), "new", or the item being edited —
  // one modal serves both, so adding a tile still means one config entry.
  const [editing, setEditing] = useState(null);
  const config = { key: tileKey, ...TILE_CONFIGS[tileKey] };
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: [tileKey, patientId],
    queryFn: () => api.get(`/${config.endpoint}/`, { params: { patient: patientId } }).then((r) => r.data.results ?? r.data),
  });

  const remove = useMutation({
    mutationFn: (item) => api.delete(`/${config.endpoint}/${item.id}/`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [tileKey, patientId] });
      queryClient.invalidateQueries({ queryKey: ["patient-overview", String(patientId)] });
    },
  });

  return (
    <>
      <HealthRecordTile
        title={title}
        icon={icon}
        items={data}
        renderItem={(item) => item[config.nameField] ?? item.name}
        renderSummary={config.summary}
        isDanger={config.isDanger}
        onAdd={() => setEditing("new")}
        onSelect={(item) => setEditing(item)}
        onDelete={(item) => {
          if (confirm(`Remove ${item[config.nameField] ?? item.name}?`)) remove.mutate(item);
        }}
      />
      {editing && (
        <GenericTileModal
          patientId={patientId}
          config={config}
          item={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}
