import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { patientNumber } from "../components/patientIdentity.js";
import { Icon } from "../components/icons.jsx";
import { readError } from "../api/errors";
import { useAuth } from "../auth/AuthContext.jsx";
import { CLINICAL_ROLES } from "../auth/roles.js";
import { useToast } from "../components/Toaster.jsx";
import {
  Alert, Badge, Button, Card, CardBody, CardFooter, CardHeader, Field, Input,
  MetaStat, Page, PageHeader, TextLink, Textarea, waitedFor,
} from "../components/ui.jsx";
import AcceptedBadge from "../components/AcceptedBadge.jsx";
import { acceptanceOf } from "../components/routeAcceptance.js";
import LabResultEntry from "../components/LabResultEntry.jsx";
import { PaymentBadge, PaymentLines, PaymentNotice } from "../components/PaymentStatus.jsx";
import { PrintButton } from "../components/printing.jsx";

// One working page, three units. Laboratory, Ultrasound and the Eye clinic
// do the same job in the same order — a doctor refers, somebody claims the
// work, does it, writes what they found, and closes it — so they share a
// station rather than each getting a near-identical page of its own.
//
// The queue is scoped server-side (workflow.views.work_routes_for): a route
// reaches the role its purpose calls for, whether or not anybody remembered
// to add staff to a Department.

export const STATIONS = {
  laboratory: {
    purpose: "laboratory",
    title: "Laboratory",
    icon: "🧫",
    iconName: "flask",
    blurb: "Requests the doctors have sent to the lab. Claim one, run it, and write what it showed.",
    resultLabel: "Findings",
    resultPlaceholder: "e.g. Hb 11.2 g/dL, WBC 6.1, no parasites seen",
    titlePlaceholder: "e.g. Full blood count",
    roles: ["laboratory"],
    // The laboratory works off the configured test catalogue — pick the
    // tests, fill in the parameters you ran. The other two units write a
    // report in prose, so they have no `structured` mode.
    structured: true,
  },
  ultrasound: {
    purpose: "ultrasound",
    title: "Ultrasound / Imaging",
    icon: "🩻",
    iconName: "scan",
    blurb: "Scans the doctors have requested. Claim one, scan, and write the report.",
    resultLabel: "Report",
    resultPlaceholder: "e.g. Normal liver echotexture. No gallstones seen.",
    titlePlaceholder: "e.g. Abdominal ultrasound",
    roles: ["radiology"],
  },
  eye: {
    purpose: "eye",
    title: "Eye Clinic",
    icon: "👁",
    iconName: "eye",
    blurb: "Patients referred to the eye clinic. Claim one, see them, and write your findings.",
    resultLabel: "Findings",
    resultPlaceholder: "e.g. VA 6/6 both eyes. IOP 14/15 mmHg.",
    titlePlaceholder: "e.g. Refraction and IOP",
    roles: ["optometrist", "ophthalmologist"],
    // A clinic calls this "in consultation", a bench "in progress". The state
    // is the same route status either way — only the word on the tab changes.
    workingLabel: "In consultation",
    // The eye doctor is a clinician as well as a member of this unit: once a
    // referral is theirs, the station opens the patient's chart and a new
    // consultation note (with its eye examination) — the Doctor Desk's own
    // pages, not a second copy of them. The optometrist keeps the findings form.
    chartActions: true,
  },
};

// What the referral actually asked for, and whether it has been paid for.
//
// The rows are the route's own `services` (workflow.RouteService) — the
// configured examinations a clinician ordered, each carrying the charge it
// raised. The unit reads this and is never gated by it: a patient already on
// the couch is scanned and the cash desk chases the balance, which is the
// rule the laboratory already works to (rule 24).
//
// The badge, the word and the tone are `components/billingStatus.js`, shared
// with the bench's own worklist and the doctor's chart — three screens, one
// vocabulary, so nobody has to work out whether "Part paid" here and
// "PARTIALLY PAID" there are the same thing.
export function RequestedServices({ services, className = "" }) {
  return <PaymentLines services={services} className={className} />;
}

const PRIORITY_TONE = {
  emergency: "border-red-300 bg-red-50 text-red-700",
  urgent: "border-amber-300 bg-amber-50 text-amber-800",
  routine: "border-slate-200 bg-slate-50 text-slate-600",
};
const PRIORITY_ORDER = { emergency: 0, urgent: 1, routine: 2 };

// When the patient arrived at this unit's list, as a clock reading.
const arrivedAt = (when) => (when
  ? new Date(when).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  : "—");

export default function DepartmentStation({ station }) {
  const config = STATIONS[station];
  const { user } = useAuth();
  const [openRoute, setOpenRoute] = useState(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");

  const { data: routes, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["patient-routes"],
    queryFn: () => api.get("/patient-routes/", { params: { page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
    refetchInterval: 60000,
  });

  // Finished work, asked for only when somebody looks at it. The queue is
  // live work by design (`work_routes_for`), so the closed clinic is a
  // separate, explicit request rather than a wider default everything pays
  // for — and the server applies exactly the same ownership rule to it.
  const { data: closed, isFetching: loadingClosed } = useQuery({
    queryKey: ["patient-routes", "completed"],
    queryFn: () => api.get("/patient-routes/", { params: { status: "completed", page_size: 200 } })
      .then((r) => r.data.results ?? r.data),
    enabled: filter === "completed",
    staleTime: 30000,
  });

  // The queue holds everything this user can work; this page is one unit of
  // it, so a doctor covering two roles does not see the lab's list here.
  const mine = useMemo(
    () => (routes ?? []).filter((r) => r.purpose === config.purpose),
    [routes, config.purpose],
  );
  const done = useMemo(
    () => (closed ?? []).filter((r) => r.purpose === config.purpose),
    [closed, config.purpose],
  );

  const stats = useMemo(() => ({
    waiting: mine.filter((r) => r.status === "queued").length,
    inProgress: mine.filter((r) => r.status === "in_progress").length,
    urgent: mine.filter((r) => r.priority !== "routine").length,
    mine: mine.filter((r) => r.assigned_to === user?.id).length,
    unassigned: mine.filter((r) => !r.assigned_to).length,
  }), [mine, user?.id]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    // Each tab answers a different question: what is on this clinic's list,
    // what is mine, what nobody has claimed, and what is finished.
    const source = filter === "completed" ? done : mine;
    return source
      .filter((r) => (
        filter === "all" || filter === "completed" ? true
        : filter === "mine" ? r.assigned_to === user?.id
        : filter === "unassigned" ? !r.assigned_to
        : r.status === filter))
      .filter((r) => !term || `${r.patient_name} ${patientNumber(r)}`.toLowerCase().includes(term))
      .sort((a, b) => {
        const byPriority = (PRIORITY_ORDER[a.priority] ?? 9) - (PRIORITY_ORDER[b.priority] ?? 9);
        return byPriority !== 0 ? byPriority : new Date(a.created_at) - new Date(b.created_at);
      });
  }, [mine, done, filter, search, user?.id]);

  if (openRoute) {
    // A row opened off the Completed tab is not in the live queue, so both
    // lists are searched for the fresh copy before falling back to the row
    // that was clicked.
    const live = [...mine, ...done].find((r) => r.id === openRoute.id) ?? openRoute;
    return <Station config={config} route={live} onBack={() => setOpenRoute(null)} />;
  }

  return (
    <Page width="wide">
      <PageHeader
        icon={config.iconName}
        title={config.title}
        subtitle={config.blurb}
        meta={
          <>
            <MetaStat value={stats.waiting} label="waiting" />
            <MetaStat value={stats.inProgress} label="in progress" tone="brand" />
            {stats.urgent > 0 && <MetaStat value={stats.urgent} label="urgent" tone="danger" />}
          </>
        }
        actions={
          <Button variant="soft" onClick={refetch} loading={isFetching}>
            {isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
          <h2 className="font-semibold">Requests</h2>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search this queue"
              placeholder="Search this queue…"
              // 16px on a phone or iOS zooms the page on focus, and 44px tall
              // so it can be hit with a thumb — the convention every control
              // in `ui.jsx` already keeps.
              className="min-h-[44px] min-w-0 rounded-lg border border-slate-300 px-3 py-1.5
                text-base text-slate-900 sm:min-h-0 sm:text-sm"
            />
            {/* Scrolls itself on a phone rather than wrapping into three rows
                or widening the page — the same rule TabBar keeps. */}
            <div className="-mx-1 flex min-w-0 max-w-full gap-1 overflow-x-auto px-1">
              {[
                ["all", `All (${mine.length})`],
                ["mine", `Mine (${stats.mine})`],
                ["unassigned", `Unassigned (${stats.unassigned})`],
                ["queued", `Waiting (${stats.waiting})`],
                ["in_progress", `${config.workingLabel ?? "In progress"} (${stats.inProgress})`],
                ["completed", "Completed"],
              ].map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setFilter(value)}
                  aria-pressed={filter === value}
                  className={`min-h-[36px] shrink-0 whitespace-nowrap rounded-lg border px-3 py-1.5 text-sm font-medium transition ${
                    filter === value
                      ? "border-brand-600 bg-brand-600 text-white"
                      : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {(isLoading || (filter === "completed" && loadingClosed && !done.length)) && (
          <p className="p-5 text-sm text-slate-700">Loading the queue…</p>
        )}
        {isError && (
          <div className="p-5">
            <p className="text-sm text-red-700">Could not load the queue.</p>
            <button onClick={refetch} className="mt-3 rounded bg-slate-900 px-3 py-2 text-sm text-white">Try again</button>
          </div>
        )}

        {!isLoading && !isError && visible.length === 0 && (
          <div className="p-10 text-center text-slate-700">
            <p className="text-3xl">{config.icon}</p>
            <p className="mt-2 font-medium">
              {filter === "completed" ? "Nothing finished yet"
                : mine.length ? "Nothing matches that filter" : "Nothing waiting"}
            </p>
            <p className="mt-1 text-sm">
              {filter === "completed"
                ? "Work you have closed appears here."
                : mine.length
                  ? "Clear the search or switch back to All."
                  : "When a patient is sent here, they appear in this list."}
            </p>
          </div>
        )}

        <div className="divide-y divide-slate-100">
          {visible.map((route) => (
            <QueueRow
              key={route.id}
              route={route}
              user={user}
              chartActions={Boolean(config.chartActions)}
              onOpen={(updated) => setOpenRoute(updated?.id ? updated : route)}
            />
          ))}
        </div>
      </section>
    </Page>
  );
}

// The chart opens only for a clinician holding the patient — the server's rule
// (`patients.access`), mirrored so an unclaimed row never offers a dead link.
const opensChart = (route, user) =>
  CLINICAL_ROLES.includes(user?.role) && route.assigned_to === user?.id;

function QueueRow({ route, user, onOpen, chartActions }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const isMine = route.assigned_to === user?.id;
  // The server says so on the row itself (`claimed_by_other`, from
  // `workflow/access.py` — the same rule that gates start / record-result),
  // with the local comparison as the fallback for a payload that predates it.
  const claimedByOther = route.claimed_by_other ?? Boolean(route.assigned_to && !isMine);
  // Whether the work has been taken up, read off the route's own status —
  // which is what `accept` moves, so there is no second answer to keep in
  // step and a reload of this page says exactly what the last one did.
  const { accepted, canAccept } = acceptanceOf(route, user);

  const accept = useMutation({
    mutationFn: () => api.post(`/patient-routes/${route.id}/accept/`),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      showToast({ title: "Accepted", message: `${route.patient_name} is yours.` });
      onOpen(response.data);
    },
    onError: (error) => {
      queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
      showToast({ title: "Could not accept", message: readError(error, "Please try again."), tone: "error" });
    },
  });

  return (
    <div className="flex items-start justify-between gap-4 px-5 py-4 transition hover:bg-slate-50">
      <button
        onClick={() => onOpen()}
        disabled={claimedByOther}
        className="min-w-0 flex-1 text-left disabled:cursor-default"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-slate-800">{route.patient_name}</span>
          <span className="text-sm text-slate-600">{patientNumber(route)}</span>
          {isMine
            ? <Pill tone="border-brand-300 bg-brand-50 text-brand-700">Yours</Pill>
            : route.assigned_to
              // Somebody in the unit has taken it. The row stays on the board
              // so nobody wonders whether the request was lost, and it says
              // who has it instead of offering a button that would 403.
              ? <Pill tone="border-slate-300 bg-slate-100 text-slate-700">
                  Accepted · {route.assigned_to_name}
                </Pill>
              : <Pill tone="border-amber-300 bg-amber-50 text-amber-800">Unclaimed</Pill>}
          {route.result && <Pill tone="border-emerald-300 bg-emerald-50 text-emerald-700">Result written</Pill>}
          {/* Where the money stands on this request, on the row itself.
              The bench used to have to open the order to find out, which
              meant it mostly did not — and a patient was sent back to a
              counter that had already taken the money, or through a scan
              nobody had billed for. It is the referral's whole bill,
              whichever unit raised it (`billing.status.route_billing`). */}
          <PaymentBadge billing={route.billing} />
        </div>
        <p className="mt-1 text-sm text-slate-700">
          {/* The arrival time as a clock reading, not only "waiting 20m" —
              a clinic list is read against the appointment book.

              The department is deliberately not here: every row on this page
              is this unit's own, so printing it on each one said nothing, and
              "from Remi (Eye Clinic)" read as though the patient had come
              *from* the eye clinic rather than been sent to it. */}
          Arrived {arrivedAt(route.created_at)} · waiting {waitedFor(route.created_at)}
          {" · "}sent by {route.routed_by_name ?? "a colleague"}
        </p>
        {route.notes && <p className="mt-1 truncate text-sm text-slate-700">{route.notes}</p>}
        {/* What was ordered, so the unit can set up before calling the
            patient in — and what it costs, so the desk's question is
            answerable without opening the bill. */}
        <RequestedServices services={route.services} className="mt-2" />
      </button>
      <div className="flex shrink-0 items-center gap-3">
        <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${PRIORITY_TONE[route.priority]}`}>
          {route.priority}
        </span>
        {chartActions && opensChart(route, user) && (
          <Button variant="link" size="sm" to={`/patients/${route.patient_uuid}`}>Open chart</Button>
        )}
        {/* Accepting is a one-way step, so the control it was made with does
            not survive it: once the route carries a holder, the slot reads as
            a finished state instead. A greyed-out "Accept" would invite a
            second press and a 409. */}
        {accepted && <AcceptedBadge tone={isMine ? "success" : "neutral"} />}
        {isMine ? (
          <button onClick={() => onOpen()} className="rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-white">
            Open →
          </button>
        ) : claimedByOther ? (
          // No Start either: the server would refuse it (403), and a control
          // that only fails is worse than none. What the rest of the unit
          // needs from this row is who is doing it.
          <span className="text-sm text-slate-600">
            With {route.assigned_to_name}
          </span>
        ) : canAccept ? (
          <button
            onClick={() => accept.mutate()}
            disabled={accept.isPending}
            className="rounded-md bg-brand-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {accept.isPending ? "Accepting…" : "Accept"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function Station({ config, route, onBack }) {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { showToast } = useToast();
  const [result, setResult] = useState(route.result ?? "");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState(null);
  const [error, setError] = useState(null);
  // The sections this unit's report is written in, served from the server's
  // one definition (`workflow/report_fields.py`) so the form holds no copy of
  // the field list — the same arrangement the eye examination has. A purpose
  // with no sections answers null and keeps the single findings box.
  const { data: reportSchema } = useQuery({
    queryKey: ["route-report-fields", config.purpose],
    queryFn: () => api.get("/patient-routes/report-fields/", { params: { purpose: config.purpose } })
      // `?? null` because TanStack refuses an undefined result, and a unit
      // with no sections is a legitimate answer, not a missing one.
      .then((r) => r.data?.schema ?? null),
    staleTime: 300000,
  });
  const [report, setReport] = useState(route.result_data ?? {});
  const sectioned = Boolean(reportSchema?.sections?.length);
  const reportFilled = Object.values(report).some((value) => (value ?? "").trim());
  // Structured entry is the lab's normal way of working; the prose form
  // stays one click away for a result that came in on paper from outside.
  const [mode, setMode] = useState(config.structured ? "structured" : "freeform");

  // Multipart: the result is usually a scanned printout or a photo of one
  // as well as typed values, and JSON cannot carry a File.
  function payload() {
    const form = new FormData();
    // Multipart cannot nest, so a section travels as `report.findings`. The
    // server reads both shapes through one reader, and renders the sections
    // into `result` itself — the browser never composes the report text.
    if (sectioned) {
      for (const section of reportSchema.sections) {
        const value = (report[section.key] ?? "").trim();
        if (value) form.append(`report.${section.key}`, value);
      }
    } else {
      form.append("result", result);
    }
    if (title.trim()) form.append("title", title.trim());
    if (file) form.append("file", file);
    return form;
  }

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    queryClient.invalidateQueries({ queryKey: ["patient-overview", String(route.patient_id)] });
  };

  const save = useMutation({
    mutationFn: () => api.post(`/patient-routes/${route.id}/record-result/`, payload()),
    onSuccess: () => {
      refresh();
      setError(null);
      setFile(null);
      showToast({
        title: "Result saved",
        message: "It is on the patient's chart, and the doctor who asked has been told.",
      });
    },
    onError: (err) => setError(readError(err, "Could not save this result.")),
  });

  const transition = useMutation({
    mutationFn: (action) => api.post(`/patient-routes/${route.id}/${action}/`,
      action === "complete" ? payload() : {}),
    onSuccess: (_r, action) => {
      refresh();
      setError(null);
      if (action === "complete") {
        showToast({ title: "Done", message: `${route.patient_name} is off your list.` });
        onBack();
      } else {
        showToast({ title: "Marked in progress" });
      }
    },
    onError: (err) => setError(readError(err, "Could not update this request.")),
  });

  // A result is typed values, an uploaded report, or both — never neither.
  const canFinish = (sectioned ? reportFilled : result.trim().length > 0)
    || Boolean(file) || Boolean(route.result_file_url);
  // Closed work is read from here, never re-worked: completing it again is a
  // refusal the server would have to make, so the button is not offered.
  const open_ = route.status === "queued" || route.status === "in_progress";

  // The identity line: who is in front of you, on one line, under the name.
  // A blank is left out rather than shown as "—" — a row of dashes is noise.
  const identity = [patientNumber(route), route.patient_age, route.patient_sex, route.patient_phone]
    .filter(Boolean).join(" · ");

  // The eye doctor's real work is the consultation note with its structured
  // eye examination; this page's prose form is the short answer that goes
  // back to whoever referred. The optometrist has no note to write, so for
  // them the form *is* the work. One config, two emphases.
  const clinician = config.chartActions && opensChart(route, user);
  const notePath = `/patients/${route.patient_uuid}/notes?new=1`;

  return (
    <Page width="narrow" className="space-y-5">
      <PageHeader
        className="mb-0"
        icon={config.iconName}
        breadcrumb={
          <div className="mb-1">
            <Button variant="link" size="xs" onClick={onBack}>
              <Icon name="back" className="h-4 w-4" aria-hidden="true" />
              Back to the queue
            </Button>
          </div>
        }
        title={route.patient_name}
        subtitle={identity}
        meta={
          <>
            <Badge tone={route.status === "in_progress" ? "brand"
              : route.status === "completed" ? "success" : "neutral"}>
              {route.status === "in_progress" ? (config.workingLabel ?? "In progress")
                : route.status === "completed" ? "Completed" : "Waiting"}
            </Badge>
            {route.priority !== "routine" && (
              <Badge tone={route.priority === "emergency" ? "danger" : "warning"}>
                <span className="capitalize">{route.priority}</span>
              </Badge>
            )}
            <MetaStat value={arrivedAt(route.created_at)} label="arrived" />
            <MetaStat value={waitedFor(route.created_at)} label="waiting" />
          </>
        }
        actions={
          <>
            {/* **One** primary action in the masthead, and it is the clinical
                work. The onward actions live with the patient's context
                below: a row of five equal buttons up here is a page with no
                answer to "what now?", and it starved the title of its width. */}
            {clinician && <Button to={notePath}>Eye consultation</Button>}
            {route.status === "queued" && (
              <Button variant={clinician ? "secondary" : "primary"}
                      onClick={() => transition.mutate("start")} disabled={transition.isPending}>
                Start
              </Button>
            )}
            {/* This unit's own paperwork, not the patient's registration
                card: the request form while the work is open, and the report
                once a finding has been written. The second only appears when
                there is one — a blank report is not a document. */}
            <PrintButton
              role={user?.role}
              variant="secondary"
              documents={route.result
                ? ["referral_report", "referral_request"]
                : ["referral_request"]}
              context={{ routeId: route.id }}
            />
          </>
        }
      />

      {/* Where the patient came from and what was asked for — the half of the
          context the identity line above does not carry. `department` is the
          unit the work was raised *into*, so it is labelled as this clinic,
          never as "from": the referrer is who it came from. */}
      <Card>
        <CardBody>
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <Fact label="Referred by" value={route.routed_by_name} />
            <Fact label="Clinic" value={route.department_name} />
            <Fact label="Visit" value={route.visit_type} />
            <Fact label="Reason for visit" value={route.visit_reason} />
            <Fact label="Working on it" value={route.assigned_to_name ?? "Unclaimed"} />
            <Fact label="Requested" value={route.purpose_label ?? config.title} />
          </dl>

          {/* Where this request's bill stands, before the patient is called
              in. Loud only when money is actually owed — a station that
              shouts on every request is one where nobody reads the
              shouting — and never a gate: the sample is run and the scan is
              done, and the desk chases the balance (rules 24 and 51). */}
          <PaymentNotice billing={route.billing} className="mt-4" />

          {route.services?.length > 0 && (
            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-700">
                Examinations requested
              </p>
              <RequestedServices services={route.services} className="mt-2" />
              <p className="mt-2 text-xs text-slate-600">
                Billing is the cash desk's — this is what was ordered and where it stands.
              </p>
            </div>
          )}

          {route.notes && (
            <div className="mt-4 rounded-lg border-l-[3px] border-brand-400 bg-brand-50/60 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-700">
                What was asked for
              </p>
              <p className="mt-1 whitespace-pre-wrap text-slate-800">{route.notes}</p>
            </div>
          )}
        </CardBody>

        {/* Everywhere else this patient can be taken, grouped where the
            patient is — the chart, the prescribing page and the doctor's
            referral page, each the hospital's existing one. */}
        {clinician && (
          <CardFooter>
            <Button variant="secondary" size="sm" to={`/patients/${route.patient_uuid}`}>
              Open chart
            </Button>
            <Button variant="secondary" size="sm" to={`/patients/${route.patient_uuid}/prescribe`}>
              Prescribe
            </Button>
            <Button variant="secondary" size="sm" to="/refer">Refer / request</Button>
          </CardFooter>
        )}
      </Card>

      {clinician && (
        <Alert tone="info">
          The full eye examination — acuity, refraction, pressure, both segments — is part of the
          consultation note, where it locks with the rest of it.{" "}
          <TextLink to={notePath}>Open the eye consultation</TextLink> to record it. What you write
          below is the short answer that goes back to whoever referred this patient, and onto their
          Tests &amp; Diagnostics record.
        </Alert>
      )}

      {config.structured && (
        <div className="flex overflow-hidden rounded-lg border border-slate-300 text-sm">
          {[["structured", "Result form"], ["freeform", "Free text / upload"]].map(([value, label]) => (
            <button
              key={value}
              onClick={() => setMode(value)}
              className={`min-h-[44px] flex-1 px-4 py-2 font-medium transition sm:min-h-[38px] ${
                mode === value ? "bg-brand-600 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {mode === "structured" ? (
        <LabResultEntry
          routeId={route.id}
          patientId={route.patient_id}
          onDone={onBack}
        />
      ) : (
        <Card>
          <CardHeader
            title={clinician ? "Findings to send back" : "Conduct the test"}
            description={clinician
              ? "A short summary for the referrer. They are notified the moment you save it, and it stays on the patient's record after this visit closes."
              : "This goes back to the doctor who asked and onto the patient's Tests & Diagnostics record, where it stays after this visit closes. They are notified the moment you save it."}
          />
          <CardBody className="space-y-4">
            <Field label="What the test was called" hint="Optional — it names the record on the chart.">
              <Input value={title} onChange={(e) => setTitle(e.target.value)}
                     placeholder={config.titlePlaceholder} />
            </Field>

            {sectioned ? (
              // A scan report is a technique, what was seen, the figures and a
              // conclusion — four things a sonographer should not have to run
              // together in one box for the doctor to pick apart later.
              reportSchema.sections.map((section) => (
                <Field key={section.key} label={section.label} hint={section.hint}>
                  {section.kind === "short" ? (
                    <Input
                      value={report[section.key] ?? ""}
                      onChange={(e) => setReport((current) =>
                        ({ ...current, [section.key]: e.target.value }))}
                      maxLength={section.max_length}
                    />
                  ) : (
                    <Textarea
                      value={report[section.key] ?? ""}
                      onChange={(e) => setReport((current) =>
                        ({ ...current, [section.key]: e.target.value }))}
                      rows={section.key === "findings" ? 6 : 3}
                      maxLength={section.max_length}
                    />
                  )}
                </Field>
              ))
            ) : (
              <Field label={config.resultLabel}>
                <Textarea value={result} onChange={(e) => setResult(e.target.value)} rows={7}
                          placeholder={config.resultPlaceholder} />
              </Field>
            )}

            <Field
              label="Upload the report"
              hint="A scan or a photo of the printout, or a PDF. Optional if you have typed the findings above."
            >
              <input
                type="file"
                aria-label="Upload the report"
                accept="image/*,application/pdf,.doc,.docx,.txt,.csv,.rtf,.odt,.xls,.xlsx"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm text-slate-800
                  file:mr-3 file:rounded-full file:border-0 file:bg-brand-50 file:px-3 file:py-1.5
                  file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
              />
            </Field>

            {file && <p className="text-sm text-slate-700">Ready to upload: {file.name}</p>}
            {!file && route.result_file_url && (
              <p className="text-sm text-slate-700">
                On file:{" "}
                <TextLink href={route.result_file_url} target="_blank" rel="noreferrer">
                  {route.result_file_name}
                </TextLink>
                {" — choose a file to replace it."}
              </p>
            )}
            {route.result_at && (
              <p className="text-sm text-slate-600">
                Last saved {new Date(route.result_at).toLocaleString()}
                {route.result_by_name && ` by ${route.result_by_name}`}
              </p>
            )}
            {error && <Alert tone="danger">{error}</Alert>}
          </CardBody>

          {/* The two ways out sit together, at the foot of the thing they
              save. "Save & mark done" used to be in the masthead, four
              buttons away from the box whose contents it commits. */}
          <CardFooter>
            <Button variant="secondary" onClick={() => save.mutate()}
                    disabled={!canFinish || save.isPending} loading={save.isPending}
                    loadingText="Saving…">
              Save result
            </Button>
            {open_ && (
              <Button
                variant="successOutline"
                onClick={() => transition.mutate("complete")}
                disabled={transition.isPending || !canFinish}
                loading={transition.isPending}
                loadingText="Working…"
                title={canFinish ? undefined : "Write the findings first"}
              >
                Save &amp; mark done
              </Button>
            )}
            <span className="min-w-0 text-sm text-slate-600">
              {canFinish
                ? "Saving keeps the patient on your list; Save & mark done closes it."
                : "Type the findings, or attach the report, to save."}
            </span>
          </CardFooter>
        </Card>
      )}
    </Page>
  );
}

function Fact({ label, value }) {
  if (!value) return null;
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="truncate text-slate-800">{value}</dd>
    </div>
  );
}

function Pill({ children, tone = "border-slate-200 bg-slate-50 text-slate-700" }) {
  return <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>{children}</span>;
}

