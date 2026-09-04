import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import PatientPicker from "../components/PatientPicker.jsx";
import { readError } from "../api/errors";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Alert, Badge, Button, Card, CardBody, CardFooter, CardHeader, EmptyState,
  ErrorState, Field, Input, Page, PageHeader,
  MetaStat, Select, SkeletonRows, StatusDot, TabBar, Tab, FormGrid,
  waitedFor,
} from "../components/ui.jsx";
import { Icon } from "../components/icons.jsx";

const PURPOSE_LABEL = {
  vitals: "Vitals",
  consultation: "Consultation",
  procedure: "Procedure",
  investigation: "Investigation",
  other: "Other",
};

// Badge tones from the design system, not a fourth hand-rolled pill.
const PRIORITY_TONE = { emergency: "danger", urgent: "warning", routine: "neutral" };

const STATUS_TONE = { queued: "neutral", in_progress: "brand", completed: "success", cancelled: "danger" };
const STATUS_LABEL = { queued: "Waiting", in_progress: "In progress", completed: "Done", cancelled: "Cancelled" };

// Front desk creates a Visit + routes it to a department; the destination
// department's queue is scoped server-side (see workflow.views.work_routes_for).
export default function PatientQueue() {
  const { user } = useAuth();
  const canRoute = ["reception", "admin", "hospital_admin"].includes(user?.role);

  const { data: routes, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["patient-routes"],
    queryFn: () => api.get("/patient-routes/").then((r) => r.data.results ?? r.data),
  });

  // Anyone a route can be sent to gets the transitions that say the work is
  // under way or over. Nursing does the same from the vitals station, which
  // is also where the vitals rule is explained.
  const canWork = ["doctor", "laboratory", "radiology", "optometrist", "ophthalmologist"]
    .includes(user?.role);

  const [filter, setFilter] = useState("open");
  const all = routes ?? [];
  const counts = {
    waiting: all.filter((r) => r.status === "queued").length,
    working: all.filter((r) => r.status === "in_progress").length,
    urgent: all.filter((r) => r.priority === "emergency" || r.priority === "urgent").length,
  };
  // "Open" is the working view — a finished visit is history, not a queue.
  const shown = all.filter((r) =>
    filter === "open" ? ["queued", "in_progress"].includes(r.status)
    : filter === "waiting" ? r.status === "queued"
    : filter === "working" ? r.status === "in_progress"
    : true);

  return (
    <Page width="wide">
      <PageHeader
        icon="queue"
        title="My queue"
        subtitle="Patients routed to you, and the work each one is waiting on."
        meta={
          <>
            <MetaStat value={counts.waiting} label="waiting" />
            <MetaStat value={counts.working} label="in progress" tone="brand" />
            {counts.urgent > 0 && <MetaStat value={counts.urgent} label="urgent" tone="danger" />}
          </>
        }
        actions={
          <Button variant="soft" onClick={() => refetch()} disabled={isFetching}>
            <Icon name="clock" className={`h-4 w-4 shrink-0 ${isFetching ? "animate-spin" : ""}`} aria-hidden="true" />
            {isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {canRoute && <NewRouteForm />}

      {/* A queue is read by state, so the states are the tabs. */}
      <TabBar label="Queue filter" className={canRoute ? "mt-6" : ""}>
        <Tab active={filter === "open"} onClick={() => setFilter("open")}>Open ({counts.waiting + counts.working})</Tab>
        <Tab active={filter === "waiting"} onClick={() => setFilter("waiting")}>Waiting ({counts.waiting})</Tab>
        <Tab active={filter === "working"} onClick={() => setFilter("working")}>In progress ({counts.working})</Tab>
        <Tab active={filter === "all"} onClick={() => setFilter("all")}>All ({all.length})</Tab>
      </TabBar>

      {isLoading && <SkeletonRows rows={4} />}
      {isError && <ErrorState title="Could not load your queue." onRetry={refetch} />}

      {!isLoading && !isError && shown.length === 0 && (
        <Card>
          <EmptyState
            icon="queue"
            title={filter === "all" ? "Nothing in your queue yet" : "No patients waiting"}
            description={
              canRoute
                ? "Route a patient above and they will appear here for the department you send them to."
                : "Patients routed to you appear here, and the bell tells you the moment one arrives."
            }
          />
        </Card>
      )}

      <div className="grid gap-2.5">
        {shown.map((route) => (
          <RouteRow key={route.id} route={route} canRoute={canRoute} canWork={canWork} />
        ))}
      </div>
    </Page>
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

  const urgent = route.priority === "emergency" || route.priority === "urgent";

  return (
    // Wraps rather than overflowing: `shrink-0` buttons beside a child with
    // the default `min-width:auto` pushed this row 124px past a 375px screen.
    // The left rule is the priority — an emergency should be findable by
    // running a thumb down the edge of the list, not by reading every badge.
    <div
      className={`flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition
        hover:border-slate-300 sm:flex-row sm:items-center sm:justify-between sm:gap-4
        ${urgent ? "border-l-[3px] border-l-red-400" : ""}`}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <span className="truncate font-semibold text-slate-900">
            {route.patient_name ?? `Visit #${route.visit}`}
          </span>
          <StatusDot tone={STATUS_TONE[route.status] ?? "neutral"}>
            {STATUS_LABEL[route.status] ?? route.status.replace("_", " ")}
          </StatusDot>
          <Badge tone="brand">{PURPOSE_LABEL[route.purpose] ?? route.purpose}</Badge>
          {route.priority !== "routine" && (
            <Badge tone={PRIORITY_TONE[route.priority] ?? "neutral"}>
              <span className="capitalize">{route.priority}</span>
            </Badge>
          )}
        </div>

        {/* The identifying line: who they are on paper, where they were sent,
            and how long they have been standing there. */}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-600">
          {route.patient_file_number && (
            <>
              <span className="tabular-nums">{route.patient_file_number}</span>
              <span aria-hidden="true" className="text-slate-400">·</span>
            </>
          )}
          <span className="truncate">{route.department_name ?? "Department"}</span>
          <span aria-hidden="true" className="text-slate-400">·</span>
          <span className="truncate">{route.assigned_to_name ?? "Unassigned"}</span>
          {route.created_at && (
            <>
              <span aria-hidden="true" className="text-slate-400">·</span>
              <span className={urgent ? "font-medium text-red-700" : ""}>
                waiting {waitedFor(route.created_at)}
              </span>
            </>
          )}
        </div>

        {route.notes && (
          <p className="mt-2 border-l-2 border-slate-200 pl-2.5 text-sm text-slate-700">{route.notes}</p>
        )}
        {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {canWork && route.status === "queued" && (
          <Button size="sm" onClick={() => transition.mutate("start")} disabled={transition.isPending}>
            {route.purpose === "consultation" ? "Start consultation" : "Start"}
          </Button>
        )}
        {canWork && route.status === "in_progress" && (
          <Button
            variant="successOutline" size="sm"
            onClick={() => transition.mutate("complete")}
            disabled={transition.isPending}
          >
            Mark done
          </Button>
        )}
        {canRoute && route.status !== "completed" && route.status !== "cancelled" && (
          <Button
            variant="linkDanger" size="sm"
            onClick={() => confirm(`Call off ${route.patient_name}'s visit to ${route.department_name}?`) && cancelRoute.mutate()}
          >
            Cancel
          </Button>
        )}
        <Button variant="link" size="sm" to={`/patients/${route.patient_id ?? ""}`}>
          Open patient
          <Icon name="chevronRight" className="h-4 w-4" aria-hidden="true" />
        </Button>
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
    <Card as="form"
      onSubmit={(e) => {
        e.preventDefault();
        if (patient && departmentId) routePatient.mutate();
      }}
    >
      <CardHeader
        title="Route a patient"
        description="Raise a visit and send it to a department. Naming the person is optional — an unassigned route sits in that department's shared queue."
      />
      <CardBody>
        <FormGrid columns={2}>
          <Field label="Patient" required className="sm:col-span-2 lg:col-span-1">
            <PatientPicker value={patient} onChange={setPatient} />
          </Field>

          <Field label="Visit type">
            <Select value={visitType} onChange={(e) => setVisitType(e.target.value)}>
              <option value="opd">Outpatient</option>
              <option value="ipd">Inpatient</option>
              <option value="emergency">Emergency</option>
            </Select>
          </Field>

          <Field label="Reason">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for visit" />
          </Field>

          <Field label="Route to department" required>
            <Select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)} required>
              <option value="">Select department</option>
              {(departments ?? []).map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </Select>
          </Field>

          <Field label="Send for">
            <Select
              value={purpose}
              onChange={(e) => { setPurpose(e.target.value); setAssignedTo(""); }}
            >
              <option value="vitals">Vitals (nursing)</option>
              <option value="consultation">Consultation</option>
              <option value="procedure">Procedure</option>
              <option value="investigation">Investigation</option>
              <option value="other">Other</option>
            </Select>
          </Field>

          <Field label={`Assign to ${assigneeRole === "nurse" ? "nurse" : "doctor"}`}>
            <Select value={assignedTo} onChange={(e) => setAssignedTo(e.target.value)}>
              <option value="">Anyone in the department</option>
              {(assignees ?? []).map((u) => (
                <option key={u.id} value={u.id}>{[u.first_name, u.last_name].filter(Boolean).join(" ") || u.username}</option>
              ))}
            </Select>
          </Field>

          <Field label="Priority">
            <Select value={priority} onChange={(e) => setPriority(e.target.value)}>
              <option value="routine">Routine</option>
              <option value="urgent">Urgent</option>
              <option value="emergency">Emergency</option>
            </Select>
          </Field>

          <Field label="Notes">
            <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Optional notes" />
          </Field>
        </FormGrid>

        {routePatient.isError && (
          <Alert tone="danger" className="mt-4">
            Could not route this patient. Check the fields above.
          </Alert>
        )}
      </CardBody>

      <CardFooter>
        <Button type="submit" disabled={!patient || !departmentId || routePatient.isPending}>
          {routePatient.isPending ? "Routing…" : "Route patient"}
        </Button>
      </CardFooter>
    </Card>
  );
}
