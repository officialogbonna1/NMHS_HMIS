import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { Icon } from "../components/icons.jsx";
import { readError } from "../api/errors";
import { useAuth } from "../auth/AuthContext.jsx";
import { useToast } from "../components/Toaster.jsx";
import { Button, TextLink, Page, PageHeader, MetaStat, Badge, waitedFor } from "../components/ui.jsx";
import LabResultEntry from "../components/LabResultEntry.jsx";
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
  },
};

const PRIORITY_TONE = {
  emergency: "border-red-300 bg-red-50 text-red-700",
  urgent: "border-amber-300 bg-amber-50 text-amber-800",
  routine: "border-slate-200 bg-slate-50 text-slate-600",
};
const PRIORITY_ORDER = { emergency: 0, urgent: 1, routine: 2 };

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

  // The queue holds everything this user can work; this page is one unit of
  // it, so a doctor covering two roles does not see the lab's list here.
  const mine = useMemo(
    () => (routes ?? []).filter((r) => r.purpose === config.purpose),
    [routes, config.purpose],
  );

  const stats = useMemo(() => ({
    waiting: mine.filter((r) => r.status === "queued").length,
    inProgress: mine.filter((r) => r.status === "in_progress").length,
    urgent: mine.filter((r) => r.priority !== "routine").length,
  }), [mine]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return mine
      .filter((r) => (filter === "all" ? true : r.status === filter))
      .filter((r) => !term || `${r.patient_name} ${r.patient_file_number ?? ""}`.toLowerCase().includes(term))
      .sort((a, b) => {
        const byPriority = (PRIORITY_ORDER[a.priority] ?? 9) - (PRIORITY_ORDER[b.priority] ?? 9);
        return byPriority !== 0 ? byPriority : new Date(a.created_at) - new Date(b.created_at);
      });
  }, [mine, filter, search]);

  if (openRoute) {
    const live = mine.find((r) => r.id === openRoute.id) ?? openRoute;
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
              placeholder="Search this queue…"
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            />
            <div className="flex overflow-hidden rounded-lg border text-sm">
              {[["all", "All"], ["queued", "Waiting"], ["in_progress", "In progress"]].map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setFilter(value)}
                  className={`px-3 py-1.5 ${filter === value ? "bg-brand-600 text-white" : "hover:bg-slate-50"}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {isLoading && <p className="p-5 text-sm text-slate-700">Loading the queue…</p>}
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
              {mine.length ? "Nothing matches that filter" : "Nothing waiting"}
            </p>
            <p className="mt-1 text-sm">
              {mine.length
                ? "Clear the search or switch back to All."
                : "When a doctor refers a patient here, they appear in this list."}
            </p>
          </div>
        )}

        <div className="divide-y divide-slate-100">
          {visible.map((route) => (
            <QueueRow
              key={route.id}
              route={route}
              user={user}
              onOpen={(updated) => setOpenRoute(updated?.id ? updated : route)}
            />
          ))}
        </div>
      </section>
    </Page>
  );
}

function QueueRow({ route, user, onOpen }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const isMine = route.assigned_to === user?.id;

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
      <button onClick={() => onOpen()} className="min-w-0 flex-1 text-left">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-slate-800">{route.patient_name}</span>
          <span className="text-sm text-slate-600">{route.patient_file_number}</span>
          {isMine
            ? <Pill tone="border-brand-300 bg-brand-50 text-brand-700">Yours</Pill>
            : route.assigned_to
              ? <Pill>{route.assigned_to_name}</Pill>
              : <Pill tone="border-amber-300 bg-amber-50 text-amber-800">Unclaimed</Pill>}
          {route.result && <Pill tone="border-emerald-300 bg-emerald-50 text-emerald-700">Result written</Pill>}
        </div>
        <p className="mt-1 text-sm text-slate-700">
          Requested by {route.routed_by_name ?? "a doctor"} · {waitedFor(route.created_at)}
        </p>
        {route.notes && <p className="mt-1 truncate text-sm text-slate-700">{route.notes}</p>}
      </button>
      <div className="flex shrink-0 items-center gap-3">
        <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${PRIORITY_TONE[route.priority]}`}>
          {route.priority}
        </span>
        {isMine ? (
          <button onClick={() => onOpen()} className="rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-white">
            Open →
          </button>
        ) : (
          <button
            onClick={() => accept.mutate()}
            disabled={accept.isPending}
            className="rounded-md bg-brand-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {accept.isPending ? "Accepting…" : "Accept"}
          </button>
        )}
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
  // Structured entry is the lab's normal way of working; the prose form
  // stays one click away for a result that came in on paper from outside.
  const [mode, setMode] = useState(config.structured ? "structured" : "freeform");

  // Multipart: the result is usually a scanned printout or a photo of one
  // as well as typed values, and JSON cannot carry a File.
  function payload() {
    const form = new FormData();
    form.append("result", result);
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
  const canFinish = result.trim().length > 0 || Boolean(file) || Boolean(route.result_file_url);

  return (
    <Page width="narrow" className="space-y-6">
      <PageHeader
        className="mb-0"
        breadcrumb={
          <div className="mb-1">
            <Button variant="link" size="xs" onClick={onBack}>
              <Icon name="back" className="h-4 w-4" aria-hidden="true" />
              Back to the queue
            </Button>
          </div>
        }
        title={route.patient_name}
        subtitle={`Requested by ${route.routed_by_name ?? "a doctor"}`}
        meta={
          <>
            <Badge tone={route.status === "in_progress" ? "brand" : "neutral"}>
              {route.status === "in_progress" ? "In progress" : "Waiting"}
            </Badge>
            <MetaStat value={route.patient_file_number} label="file number" />
            <MetaStat value={route.purpose_label ?? config.title} label="requested" />
            <MetaStat value={waitedFor(route.created_at)} label="waiting" />
          </>
        }
        actions={
          <>
            {/* This unit's own paperwork, not the patient's registration
                card: the request form the doctor raised while the work is
                open, and the report once a finding has been written. The
                second only appears when there is one — a blank report is not
                a document. */}
            <PrintButton
              role={user?.role}
              variant="secondary"
              documents={route.result
                ? ["referral_report", "referral_request"]
                : ["referral_request"]}
              context={{ routeId: route.id }}
            />
            {route.status === "queued" && (
              <Button variant="secondary" onClick={() => transition.mutate("start")} disabled={transition.isPending}>
                Start
              </Button>
            )}
            <Button
              variant="successOutline"
              onClick={() => transition.mutate("complete")}
              disabled={transition.isPending || !canFinish}
              title={canFinish ? undefined : "Write the result first"}
            >
              {transition.isPending ? "Working…" : "Save & mark done"}
            </Button>
          </>
        }
      />

      {route.notes && (
        <section className="rounded-xl border border-brand-200 bg-brand-50/60 p-4">
          <h2 className="text-sm font-semibold text-slate-800">What the doctor asked for</h2>
          <p className="mt-1 whitespace-pre-wrap text-slate-800">{route.notes}</p>
        </section>
      )}

      {config.structured && (
        <div className="flex overflow-hidden rounded-lg border border-slate-300 text-sm">
          {[["structured", "Result form"], ["freeform", "Free text / upload"]].map(([value, label]) => (
            <button
              key={value}
              onClick={() => setMode(value)}
              className={`flex-1 px-4 py-2 font-medium transition ${
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
      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-medium text-slate-800">Conduct the test</h2>
        <p className="mb-4 text-sm text-slate-700">
          This goes back to the doctor who asked and onto the patient's Tests &amp; Diagnostics
          record, where it stays after this visit closes. They are notified the moment you save it.
        </p>
        <label className="mb-1 block text-sm font-medium text-slate-700">
          What the test was called
        </label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={config.titlePlaceholder}
          className="mb-4 w-full rounded-md border border-slate-300 px-3 py-2"
        />

        <label className="mb-1 block text-sm font-medium text-slate-700">
          {config.resultLabel}
        </label>
        <textarea
          value={result}
          onChange={(e) => setResult(e.target.value)}
          rows={7}
          placeholder={config.resultPlaceholder}
          className="w-full rounded-md border border-slate-300 px-3 py-2"
        />

        <label className="mb-1 mt-4 block text-sm font-medium text-slate-700">
          Upload the report
        </label>
        <input
          type="file"
          accept="image/*,application/pdf,.doc,.docx,.txt,.csv,.rtf,.odt,.xls,.xlsx"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm file:mr-3 file:rounded-full file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
        />
        <p className="mt-1 text-sm text-slate-600">
          A scan or a photo of the printout, or a PDF. Optional if you have typed the values above.
        </p>
        {file && <p className="mt-1 text-sm text-slate-700">Ready to upload: {file.name}</p>}
        {!file && route.result_file_url && (
          <p className="mt-1 text-sm text-slate-700">
            On file:{" "}
            <TextLink href={route.result_file_url} target="_blank" rel="noreferrer">
              {route.result_file_name}
            </TextLink>
            {" — choose a file to replace it."}
          </p>
        )}

        {route.result_at && (
          <p className="mt-2 text-sm text-slate-600">
            Last saved {new Date(route.result_at).toLocaleString()}
            {route.result_by_name && ` by ${route.result_by_name}`}
          </p>
        )}
        {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            onClick={() => save.mutate()}
            disabled={!canFinish || save.isPending}
            className="rounded-md bg-brand-600 px-5 py-2.5 text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : "Save result"}
          </button>
          <span className="text-sm text-slate-700">
            Saving keeps the patient on your list; <strong>Save &amp; mark done</strong> closes it.
          </span>
        </div>
      </section>
      )}
    </Page>
  );
}

function Pill({ children, tone = "border-slate-200 bg-slate-50 text-slate-700" }) {
  return <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>{children}</span>;
}

