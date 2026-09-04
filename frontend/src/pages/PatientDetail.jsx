import { useState } from "react";
import { useParams, useNavigate, Routes, Route, Link, useLocation } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { PatientCardSheet } from "../components/PrintDocuments.jsx";
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
  const [showCard, setShowCard] = useState(false);
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuth();
  const role = user?.role;
  const isClinical = ["admin", "hospital_admin", "doctor"].includes(role);
  // Nurses take vitals; doctors read them for the patients assigned to them.
  // The Add button inside VitalsTab is what's limited to nurses.
  const canSeeVitals = ["admin", "hospital_admin", "nurse", "doctor"].includes(role);
  const canPrescribe = ["admin", "hospital_admin", "doctor"].includes(role);
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

  const { data: patient } = useQuery({
    queryKey: ["patient", id],
    queryFn: () => api.get(`/patients/${id}/`).then((r) => r.data),
  });

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

  return (
    <div className="max-w-5xl mx-auto p-6">
      {patient && (
        <div className="mb-4 flex justify-between items-start">
          <div>
            <h1 className="text-xl font-semibold">{patient.last_name}, {patient.first_name}</h1>
            <p className="text-sm text-slate-600">{patient.file_number} · DOB: {patient.birthdate ?? "—"} · Sex: {patient.sex}</p>
            <p className="text-sm text-slate-700 mt-1">{patient.short_note}</p>
          </div>
          <div className="flex shrink-0 gap-2">
            {/* Cards get lost. Anyone at the desk can reprint one. */}
            <button
              onClick={() => setShowCard(true)}
              className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              🖨 Patient card
            </button>
            {canPrescribe && <button
              onClick={() => navigate(`/patients/${id}/prescribe`)}
              className="bg-brand-600 text-white px-4 py-2 rounded-full text-sm"
            >
              + Prescribe
            </button>}
          </div>
        </div>
      )}

      {showCard && patient && <PatientCardSheet patient={patient} onClose={() => setShowCard(false)} />}

      {(isClinical || canSeeVitals || canBill || canSeePharmacy) && <div className="mb-6 flex gap-6 overflow-x-auto border-b text-sm">
        {isClinical && <TabLink to={`/patients/${id}`} active={tab === "overview"}>Overview</TabLink>}
        {isClinical && <TabLink to={`/patients/${id}/record`} active={tab === "record"}>Health Record</TabLink>}
        {isClinical && <TabLink to={`/patients/${id}/notes`} active={tab === "notes"}>Medical Notes</TabLink>}
        {canSeeVitals && <TabLink to={`/patients/${id}/vitals`} active={tab === "vitals"}>Vitals</TabLink>}
        {isClinical && <TabLink to={`/patients/${id}/lab`} active={tab === "lab"}>Lab</TabLink>}
        {isClinical && <TabLink to={`/patients/${id}/ultrasound`} active={tab === "ultrasound"}>Ultrasound</TabLink>}
        {isClinical && <TabLink to={`/patients/${id}/eye`} active={tab === "eye"}>Eye</TabLink>}
        {isClinical && <TabLink to={`/patients/${id}/procedure`} active={tab === "procedure"}>Procedures</TabLink>}
        {canSeeCare && <TabLink to={`/patients/${id}/admission`} active={tab === "admission"}>Admission</TabLink>}
        {canSeePharmacy && <TabLink to={`/patients/${id}/pharmacy`} active={tab === "pharmacy"}>Pharmacy</TabLink>}
        {canBill && <TabLink to={`/patients/${id}/billing`} active={tab === "billing"}>Billing</TabLink>}
      </div>}

      {!isClinical && !canSeeVitals && !canBill && !canSeePharmacy && <div className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm text-slate-600">This role can view only the patient’s basic registration details. Use Appointments and Routing to continue the front-desk workflow.</div>}

      {isClinical && tab === "overview" && <PatientOverview patientId={id} />}
      {isClinical && tab === "record" && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {TILES.map((t) => (
            <TileLoader key={t.key} patientId={id} tileKey={t.key} title={t.title} icon={t.icon} />
          ))}
        </div>
      )}
      {isClinical && tab === "notes" && <MedicalNotesTab patientId={id} />}
      {canSeeVitals && tab === "vitals" && <VitalsTab patientId={id} />}
      {isClinical && tab === "lab" && <LabResultsTab patientId={id} />}
      {isClinical && ["ultrasound", "eye", "procedure"].includes(tab) && (
        <ReferralResultsTab patientId={id} kind={tab} />
      )}
      {canSeeCare && tab === "admission" && <AdmissionTab patientId={id} />}
      {canSeePharmacy && tab === "pharmacy" && (
        <PharmacyTab patientId={id} canPrescribe={canPrescribe} />
      )}
      {canBill && tab === "billing" && <PatientBillingTab patientId={id} />}
    </div>
  );
}

function TabLink({ to, active, children }) {
  return (
    <Link
      to={to}
      className={`pb-3 -mb-px border-b-2 ${active ? "border-brand-600 text-brand-600 font-medium" : "border-transparent text-slate-600 hover:text-slate-800"}`}
    >
      {children}
    </Link>
  );
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
