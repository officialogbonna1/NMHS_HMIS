import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";
import PatientPicker from "../components/PatientPicker.jsx";
import { readError } from "../api/errors";
import { useAuth } from "../auth/AuthContext.jsx";

const PURPOSE_LABEL = {
  vitals: "Vitals",
  consultation: "Consultation",
  procedure: "Procedure",
  investigation: "Investigation",
  other: "Other",
};

const PRIORITY_TONE = {
  emergency: "bg-red-100 text-red-700",
  urgent: "bg-amber-100 text-amber-800",
  routine: "bg-slate-100 text-slate-600",
};

// Front desk creates a Visit + routes it to a department; the destination
// department's queue is scoped server-side (see workflow.views.work_routes_for).
export default function PatientQueue() {
  const { user } = useAuth();
  const canRoute = ["reception", "admin", "hospital_admin"].includes(user?.role);

  const { data: routes, isLoading } = useQuery({
    queryKey: ["patient-routes"],
    queryFn: () => api.get("/patient-routes/").then((r) => r.data.results ?? r.data),
  });

  // Anyone a route can be sent to gets the transitions that say the work is
  // under way or over. Nursing does the same from the vitals station, which
  // is also where the vitals rule is explained.
  const canWork = ["doctor", "laboratory", "radiology", "optometrist", "ophthalmologist"]
    .includes(user?.role);

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-8">
      <h1 className="text-2xl font-semibold">Patient Queue</h1>

      {canRoute && <NewRouteForm />}

      <section>
        <h2 className="font-medium text-slate-800 mb-1">Active queue</h2>
        <p className="text-xs text-slate-600 mb-3">
          The department the patient was sent to marks their own work started and done. From here you can call a
          visit off if the patient leaves or was routed by mistake.
        </p>
        {isLoading && <p className="text-slate-600">Loading…</p>}
        {!isLoading && (routes ?? []).length === 0 && (
          <p className="text-slate-600">No patients waiting.</p>
        )}
        <div className="grid gap-2">
          {(routes ?? []).map((route) => (
            <RouteRow key={route.id} route={route} canRoute={canRoute} canWork={canWork} />
          ))}
        </div>
      </section>
    </div>
  );
}

function RouteRow({ route, canRoute, canWork }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  // The front desk raises and calls off routes; the clinician the patient was
  // sent to is the one who says the work started or finished.
  const cancelRoute = useMutation({
    mutationFn: () => api.post(`/patient-routes/${route.id}/cancel/`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["patient-routes"] }),
  });

  const transition = useMutation({
    mutationFn: (action) => api.post(`/patient-routes/${route.id}/${action}/`),
    onSuccess: () => {
      setError("");
      queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (err) => setError(readError(err, "Could not update this patient.")),
  });

  return (
    <div className="border rounded-lg p-4 flex items-center justify-between gap-4">
      <div>
        <div className="flex items-center gap-2">
          <span className="font-medium">{route.patient_name ?? `Visit #${route.visit}`}</span>
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-brand-100 text-brand-700">
            {PURPOSE_LABEL[route.purpose] ?? route.purpose}
          </span>
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${PRIORITY_TONE[route.priority]}`}>
            {route.priority}
          </span>
          <span className="text-xs text-slate-500">{route.status.replace("_", " ")}</span>
        </div>
        <p className="text-sm text-slate-600 mt-1">
          {route.department_name ?? "Department"} · {route.assigned_to_name ?? "Unassigned"}
        </p>
        {route.notes && <p className="text-sm text-slate-700 mt-1">{route.notes}</p>}
        {error && <p className="text-sm text-red-600 mt-1">{error}</p>}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {canWork && route.status === "queued" && (
          <button
            onClick={() => transition.mutate("start")}
            disabled={transition.isPending}
            className="rounded-full border px-3 py-1.5 text-xs font-medium hover:bg-slate-50 disabled:opacity-50"
          >
            {route.purpose === "consultation" ? "Start consultation" : "Start"}
          </button>
        )}
        {canWork && route.status === "in_progress" && (
          <button
            onClick={() => transition.mutate("complete")}
            disabled={transition.isPending}
            className="rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
          >
            Mark done
          </button>
        )}
        {canRoute && route.status !== "completed" && route.status !== "cancelled" && (
          <button
            onClick={() => confirm(`Call off ${route.patient_name}'s visit to ${route.department_name}?`) && cancelRoute.mutate()}
            className="text-xs text-red-600 hover:underline"
          >
            Cancel
          </button>
        )}
        <Link
          to={`/patients/${route.patient_id ?? ""}`}
          className="text-xs text-brand-600 hover:underline"
        >
          Open patient →
        </Link>
      </div>
    </div>
  );
}

function NewRouteForm() {
  const queryClient = useQueryClient();
  const [patient, setPatient] = useState(null);
  const [visitType, setVisitType] = useState("opd");
  const [reason, setReason] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [purpose, setPurpose] = useState("vitals");
  const [assignedTo, setAssignedTo] = useState("");
  const [priority, setPriority] = useState("routine");
  const [notes, setNotes] = useState("");

  const { data: departments } = useQuery({
    queryKey: ["departments"],
    queryFn: () => api.get("/departments/").then((r) => r.data.results ?? r.data),
  });

  // Vitals go to a nurse; everything else is usually a doctor. Naming the
  // person is optional — an unassigned route sits in the department's queue.
  const assigneeRole = purpose === "vitals" ? "nurse" : "doctor";
  const { data: assignees } = useQuery({
    queryKey: ["users", assigneeRole],
    queryFn: () => api.get("/users/", { params: { role: assigneeRole } }).then((r) => r.data.results ?? r.data),
  });

  const routePatient = useMutation({
    mutationFn: async () => {
      const { data: visit } = await api.post("/visits/", {
        patient: patient.id,
        visit_type: visitType,
        reason,
      });
      return api.post("/patient-routes/", {
        visit: visit.id,
        department: Number(departmentId),
        purpose,
        assigned_to: assignedTo ? Number(assignedTo) : null,
        priority,
        notes,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
      setPatient(null);
      setReason("");
      setNotes("");
      setDepartmentId("");
      setPurpose("vitals");
      setAssignedTo("");
      setPriority("routine");
    },
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (patient && departmentId) routePatient.mutate();
      }}
      className="bg-white border rounded-xl p-5 space-y-4"
    >
      <h2 className="font-medium text-slate-800">Route a patient</h2>

      <div className="grid md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">Patient *</label>
          <PatientPicker value={patient} onChange={setPatient} />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Visit type</label>
          <select value={visitType} onChange={(e) => setVisitType(e.target.value)} className="w-full border rounded-md px-3 py-2">
            <option value="opd">Outpatient</option>
            <option value="ipd">Inpatient</option>
            <option value="emergency">Emergency</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Reason</label>
          <input value={reason} onChange={(e) => setReason(e.target.value)} className="w-full border rounded-md px-3 py-2" placeholder="Reason for visit" />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Route to department *</label>
          <select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)} required className="w-full border rounded-md px-3 py-2">
            <option value="">Select department</option>
            {(departments ?? []).map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Send for</label>
          <select
            value={purpose}
            onChange={(e) => { setPurpose(e.target.value); setAssignedTo(""); }}
            className="w-full border rounded-md px-3 py-2"
          >
            <option value="vitals">Vitals (nursing)</option>
            <option value="consultation">Consultation</option>
            <option value="procedure">Procedure</option>
            <option value="investigation">Investigation</option>
            <option value="other">Other</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">
            Assign to {assigneeRole === "nurse" ? "nurse" : "doctor"}
          </label>
          <select value={assignedTo} onChange={(e) => setAssignedTo(e.target.value)} className="w-full border rounded-md px-3 py-2">
            <option value="">Anyone in the department</option>
            {(assignees ?? []).map((u) => (
              <option key={u.id} value={u.id}>{[u.first_name, u.last_name].filter(Boolean).join(" ") || u.username}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Priority</label>
          <select value={priority} onChange={(e) => setPriority(e.target.value)} className="w-full border rounded-md px-3 py-2">
            <option value="routine">Routine</option>
            <option value="urgent">Urgent</option>
            <option value="emergency">Emergency</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Notes</label>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} className="w-full border rounded-md px-3 py-2" placeholder="Optional notes" />
        </div>
      </div>

      {routePatient.isError && (
        <p className="text-sm text-red-600">Could not route this patient. Check the fields above.</p>
      )}

      <button
        type="submit"
        disabled={!patient || !departmentId || routePatient.isPending}
        className="bg-brand-600 text-white px-5 py-2.5 rounded-md hover:bg-brand-700 disabled:opacity-50"
      >
        {routePatient.isPending ? "Routing…" : "Route patient"}
      </button>
    </form>
  );
}
