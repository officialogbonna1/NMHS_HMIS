import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";
import { Icon } from "../components/icons.jsx";
import { readError } from "../api/errors";
import { useAuth } from "../auth/AuthContext.jsx";
import { useToast } from "../components/Toaster.jsx";
import { Button, Page, PageHeader, MetaStat, TabBar, Tab, Badge, waitedFor } from "../components/ui.jsx";
import { PrintButton } from "../components/printing.jsx";
import VitalsEntryForm from "../components/VitalsEntryForm.jsx";
import NursingNoteForm from "../components/NursingNoteForm.jsx";

// The nurse's workspace. Reception routes a patient here (purpose
// "Vitals"); the nurse opens them, records the reading and an observation,
// and marks the route done. Everything saved here lands on the doctor's
// chart for that patient.
const PRIORITY_TONE = {
  emergency: "border-red-300 bg-red-50 text-red-700",
  urgent: "border-amber-300 bg-amber-50 text-amber-800",
  routine: "border-slate-200 bg-slate-50 text-slate-600",
};

const PRIORITY_ORDER = { emergency: 0, urgent: 1, routine: 2 };

const PURPOSE_LABEL = {
  vitals: "Vitals",
  consultation: "Consultation",
  procedure: "Procedure",
  investigation: "Investigation",
  other: "Other",
};

export default function VitalsStation() {
  const { user } = useAuth();
  const [openRoute, setOpenRoute] = useState(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  // The tab lives in the URL so the dashboard's "recorded today" cards can
  // link straight to the day list — the queue they used to open has emptied
  // of the very patients those cards are counting.
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "today" ? "today" : "queue";
  const setTab = (next) => setParams(next === "today" ? { tab: "today" } : {}, { replace: true });

  const { data: routes, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["patient-routes"],
    queryFn: () => api.get("/patient-routes/").then((r) => r.data.results ?? r.data),
    refetchInterval: 60000,
  });

  // Counts are derived from the queue already on screen, so the summary line
  // and the rows below it can never disagree. The day's totals live on the
  // nurse's dashboard, which counts them in the database.
  const stats = useMemo(() => {
    const all = routes ?? [];
    return {
      waiting: all.filter((r) => r.status === "queued").length,
      inProgress: all.filter((r) => r.status === "in_progress").length,
      urgent: all.filter((r) => r.priority !== "routine").length,
    };
  }, [routes]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (routes ?? [])
      .filter((r) => (filter === "all" ? true : r.status === filter))
      .filter((r) => !term || `${r.patient_name} ${r.patient_file_number ?? ""}`.toLowerCase().includes(term))
      .sort((a, b) => {
        const byPriority = (PRIORITY_ORDER[a.priority] ?? 9) - (PRIORITY_ORDER[b.priority] ?? 9);
        return byPriority !== 0 ? byPriority : new Date(a.created_at) - new Date(b.created_at);
      });
  }, [routes, search, filter]);

  if (openRoute) {
    return <PatientStation route={openRoute} onBack={() => setOpenRoute(null)} />;
  }

  return (
    <Page width="wide">
      <PageHeader
        icon="activity"
        title="Vitals"
        subtitle={`Record and monitor patient vital signs. Patients the front desk has sent to you, ${user?.first_name || user?.username}.`}
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

      <TabBar label="Vitals sections">
        {[["queue", "My queue"], ["today", "Recorded today"]].map(([value, label]) => (
          <Tab key={value} active={tab === value} onClick={() => setTab(value)}>{label}</Tab>
        ))}
      </TabBar>

      {tab === "today" && <RecordedToday />}

      {tab === "queue" && (
      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
          <h2 className="font-semibold">My queue</h2>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search this queue…"
              className="rounded-lg border px-3 py-1.5 text-sm"
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

        {isLoading && <p className="p-5 text-sm text-slate-500">Loading your queue…</p>}
        {isError && (
          <div className="p-5">
            <p className="text-sm text-red-700">Could not load your queue.</p>
            <button onClick={refetch} className="mt-3 rounded bg-slate-900 px-3 py-2 text-sm text-white">Try again</button>
          </div>
        )}

        {!isLoading && !isError && visible.length === 0 && (
          <div className="p-10 text-center text-slate-500">
            <p className="text-3xl">🩺</p>
            <p className="mt-2 font-medium">
              {routes?.length ? "Nothing matches that filter" : "Nobody is waiting"}
            </p>
            <p className="mt-1 text-sm">
              {routes?.length
                ? "Clear the search or switch back to All."
                : "When reception sends a patient for vitals, they appear here."}
            </p>
          </div>
        )}

        <div className="divide-y divide-slate-100">
          {visible.map((route) => (
            <QueueRow
              key={route.id}
              route={route}
              // `accept` hands back the claimed route; a plain row click hands
              // back nothing. Anything else — a click event, say — is not a
              // route, and opening the station on it leaves every field blank.
              onOpen={(updated) => setOpenRoute(updated?.id ? updated : route)}
            />
          ))}
        </div>
      </section>
      )}
    </Page>
  );
}

// What this nurse has already done today, and where each patient went next.
// The queue empties as they work, so this is the only place they can look
// back at a reading — or answer "which doctor has that patient now?".
function RecordedToday() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["vitals-recorded-today"],
    queryFn: () => api.get("/vitals/recorded-today/").then((r) => r.data),
  });

  if (isLoading) return <p className="text-sm text-slate-500">Loading today’s readings…</p>;
  if (isError) {
    return (
      <div className="rounded-2xl border bg-white p-5">
        <p className="text-sm text-red-700">Could not load today’s readings.</p>
        <button onClick={refetch} className="mt-3 rounded bg-slate-900 px-3 py-2 text-sm text-white">Try again</button>
      </div>
    );
  }
  if (!data?.length) {
    return (
      <div className="rounded-2xl border bg-white p-10 text-center text-slate-500">
        <p className="text-3xl">🗒️</p>
        <p className="mt-2 font-medium">Nothing recorded yet today</p>
        <p className="mt-1 text-sm">Readings you take appear here, with the doctor each patient went on to.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600">
        <strong>{data.length}</strong> reading{data.length === 1 ? "" : "s"} today ·{" "}
        <strong>{data.filter((r) => r.sent_to).length}</strong> sent on to a doctor ·{" "}
        <strong>{data.filter((r) => !r.sent_to && !r.with_doctor).length}</strong> with nobody yet
      </p>
      {data.map((row) => (
        <article key={row.id} className="rounded-xl border bg-white p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <Link to={`/patients/${row.patient_id}`} className="font-medium text-slate-800 hover:text-brand-600">
                {row.patient_name}
              </Link>
              <p className="mt-0.5 text-xs text-slate-500">
                {row.patient_file_number} · recorded {new Date(row.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </p>
            </div>
            {row.sent_to ? (
              <span className="rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-800">
                Sent to Dr. {row.sent_to.doctor} · {row.sent_to.status_label}
              </span>
            ) : row.with_doctor ? (
              // A re-check on a patient a doctor already has. It was never
              // "unsent" — the doctor could see it the moment it was saved.
              <span className="rounded-full border border-blue-300 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-800">
                Already with Dr. {row.with_doctor.doctor} · {row.with_doctor.status_label}
              </span>
            ) : (
              <span className="rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800">
                Not sent to a doctor yet
              </span>
            )}
          </div>

          {row.readings.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm text-slate-700">
              {row.readings.map((r) => (
                <span key={r.label}>
                  <span className="text-slate-600">{r.label}</span> {r.value}
                  <span className="text-slate-600"> {r.unit}</span>
                </span>
              ))}
            </div>
          )}

          {row.note && (
            <div className="mt-3 border-t pt-3 text-sm">
              {row.note.complaint && <p className="font-medium text-slate-700">{row.note.complaint}</p>}
              <p className="whitespace-pre-wrap text-slate-600">{row.note.observation}</p>
            </div>
          )}
        </article>
      ))}
    </div>
  );
}

function QueueRow({ route, onOpen }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const mine = route.assigned_to === user?.id;

  const accept = useMutation({
    mutationFn: () => api.post(`/patient-routes/${route.id}/accept/`),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["vitals-recorded-today"] });
      showToast({ title: "Patient accepted", message: `${route.patient_name} is yours — take their vitals.` });
      onOpen(response.data);
    },
    onError: (error) => {
      queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
      showToast({
        title: "Could not accept",
        message: readError(error, "Please try again."),
        tone: "error",
      });
    },
  });

  return (
    <div className="flex items-start justify-between gap-4 px-5 py-4 transition hover:bg-slate-50">
      <button onClick={() => onOpen()} className="min-w-0 flex-1 text-left">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-slate-800">{route.patient_name}</span>
          <span className="text-xs text-slate-600">{route.patient_file_number}</span>
          <Pill>{PURPOSE_LABEL[route.purpose] ?? route.purpose}</Pill>
          {mine
            ? <Pill tone="border-brand-300 bg-brand-50 text-brand-700">Yours</Pill>
            : route.assigned_to
              ? <Pill tone="border-slate-200 bg-slate-50 text-slate-500">{route.assigned_to_name}</Pill>
              : <Pill tone="border-amber-300 bg-amber-50 text-amber-800">Unclaimed</Pill>}
        </div>
        <p className="mt-1 text-sm text-slate-500">
          {route.department_name} · waiting {waitedFor(route.created_at)}
        </p>
        {route.notes && <p className="mt-1 truncate text-sm text-slate-600">{route.notes}</p>}
      </button>
      <div className="flex shrink-0 items-center gap-3">
        <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${PRIORITY_TONE[route.priority]}`}>
          {route.priority}
        </span>
        {mine ? (
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

function Pill({ children, tone = "border-slate-200 bg-slate-50 text-slate-600" }) {
  return <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>{children}</span>;
}


function PatientStation({ route, onBack }) {
  const patientId = route.patient_id;
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { showToast } = useToast();
  const [savedVitalsId, setSavedVitalsId] = useState(null);

  const { data: vitals } = useQuery({
    queryKey: ["vitals", patientId],
    queryFn: () => api.get("/vitals/", { params: { patient: patientId } }).then((r) => r.data.results ?? r.data),
  });

  const { data: notes } = useQuery({
    queryKey: ["nursing-notes", patientId],
    queryFn: () => api.get("/nursing-notes/", { params: { patient: patientId } }).then((r) => r.data.results ?? r.data),
  });

  // Matches the server's rule: only a reading taken since reception raised
  // this route counts. A returning patient's old vitals must not stand in
  // for the ones this attendance is waiting on.
  const takenForThisRoute = (vitals ?? []).some((v) => new Date(v.created_at) >= new Date(route.created_at));

  const refreshQueue = () => queryClient.invalidateQueries({ queryKey: ["patient-routes"] });

  const transition = useMutation({
    mutationFn: (action) => api.post(`/patient-routes/${route.id}/${action}/`),
    onSuccess: (_response, action) => {
      refreshQueue();
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["vitals-recorded-today"] });
      if (action === "complete") {
        showToast({ title: "Patient done", message: `${route.patient_name} is out of your queue.` });
        onBack();
      } else {
        showToast({ title: "Marked in progress" });
      }
    },
    onError: (error) => showToast({
      title: "Could not update the queue",
      message: readError(error, "Please try again."),
      tone: "error",
    }),
  });

  return (
    <Page className="space-y-6">
      <PageHeader
        className="mb-0"
        breadcrumb={
          <div className="mb-1">
            <Button variant="link" size="xs" onClick={onBack}>
              <Icon name="back" className="h-4 w-4" aria-hidden="true" />
              Back to my queue
            </Button>
          </div>
        }
        title={route.patient_name}
        subtitle={route.notes || undefined}
        meta={
          <>
            <Badge tone={route.status === "in_progress" ? "brand" : "neutral"}>
              {route.status === "in_progress" ? "In progress" : "Waiting"}
            </Badge>
            <MetaStat value={route.patient_file_number} label="file number" />
            <MetaStat value={waitedFor(route.created_at)} label="waiting" />
          </>
        }
        actions={
          <>
            {/* The nursing document: the readings and the notes written
                beside them, for the folder or the ward round. Vitals lock on
                save, so what prints is what was recorded. */}
            <PrintButton
              role={user?.role}
              variant="secondary"
              documents={["vitals_record"]}
              context={{ patientId }}
            />
            {route.status === "queued" && (
              <Button variant="secondary" onClick={() => transition.mutate("start")} disabled={transition.isPending}>
                Start
              </Button>
            )}
            <Button
              variant="successOutline"
              onClick={() => transition.mutate("complete")}
              disabled={transition.isPending || !takenForThisRoute}
              title={takenForThisRoute ? undefined : "Record this patient's vitals first"}
            >
              {transition.isPending ? "Working…" : "Mark done"}
            </Button>
          </>
        }
      />

      {!takenForThisRoute && (
        <p className="text-sm text-amber-700">
          Nothing recorded for this patient yet — take their vitals below before marking them done.
          If they left without being seen, use <strong>Cancel</strong> from reception instead.
        </p>
      )}

      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-medium text-slate-800">Take vitals</h2>
        <p className="mb-4 text-sm text-slate-600">Vitals lock the moment they are saved — check the figures before you save.</p>
        <VitalsEntryForm
          patientId={patientId}
          onSaved={(saved) => {
            queryClient.invalidateQueries({ queryKey: ["vitals", patientId] });
            queryClient.invalidateQueries({ queryKey: ["dashboard"] });
            queryClient.invalidateQueries({ queryKey: ["vitals-recorded-today"] });
            setSavedVitalsId(saved.id);
            showToast({ title: "Vitals saved", message: "The doctor can see them on the patient's chart." });
          }}
        />
      </section>

      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-medium text-slate-800">Nursing note</h2>
        <p className="mb-4 text-sm text-slate-600">
          What you observed. The doctor reads this alongside the vitals; it locks on save, so a follow-up is a new note.
        </p>
        <NursingNoteForm
          patientId={patientId}
          vitalsId={savedVitalsId}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ["nursing-notes", patientId] });
            queryClient.invalidateQueries({ queryKey: ["dashboard"] });
            queryClient.invalidateQueries({ queryKey: ["vitals-recorded-today"] });
            showToast({ title: "Note saved" });
          }}
        />
      </section>

      <SendToDoctor
        route={route}
        hasVitals={takenForThisRoute}
        onSent={(doctorName) => {
          queryClient.invalidateQueries({ queryKey: ["patient-routes"] });
          queryClient.invalidateQueries({ queryKey: ["dashboard"] });
          queryClient.invalidateQueries({ queryKey: ["vitals-recorded-today"] });
          showToast({ title: "Sent to the doctor", message: `${route.patient_name} is now with Dr. ${doctorName}.` });
          onBack();
        }}
      />

      <section>
        <h2 className="mb-3 font-medium text-slate-800">Recent readings</h2>
        {!vitals?.length && <p className="text-sm text-slate-500">Nothing recorded yet.</p>}
        <div className="grid gap-2">
          {(vitals ?? []).slice(0, 5).map((v) => (
            <div key={v.id} className="flex flex-wrap gap-x-6 gap-y-1 rounded-lg border bg-white p-3 text-sm">
              <span className="font-medium">{new Date(v.visit_time).toLocaleString()}</span>
              {v.temperature_c && <span>Temp {v.temperature_c}°C</span>}
              {v.heart_rate && <span>HR {v.heart_rate}</span>}
              {v.bp_systolic && v.bp_diastolic && <span>BP {v.bp_systolic}/{v.bp_diastolic}</span>}
              {v.sao2 && <span>SaO₂ {v.sao2}%</span>}
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-3 font-medium text-slate-800">Previous notes</h2>
        {!notes?.length && <p className="text-sm text-slate-500">No nursing notes yet.</p>}
        <div className="grid gap-2">
          {(notes ?? []).slice(0, 5).map((n) => (
            <div key={n.id} className="rounded-lg border bg-white p-3 text-sm">
              <p className="text-sm text-slate-600">{new Date(n.created_at).toLocaleString()} · {n.nurse_name}</p>
              {n.complaint && <p className="mt-1 font-medium">{n.complaint}</p>}
              <p className="mt-1 whitespace-pre-wrap text-slate-800">{n.observation}</p>
            </div>
          ))}
        </div>
      </section>
    </Page>
  );
}

// The last step of the nurse's turn: hand the patient on. Locked until a
// reading exists, because that is the whole reason the patient came here —
// the server enforces the same rule.
function SendToDoctor({ route, hasVitals, onSent }) {
  const { showToast } = useToast();
  const [doctorId, setDoctorId] = useState("");
  const [notes, setNotes] = useState("");

  const { data: doctors } = useQuery({
    queryKey: ["users", "doctor"],
    queryFn: () => api.get("/users/", { params: { role: "doctor" } }).then((r) => r.data.results ?? r.data),
  });

  const doctorName = (d) => [d.first_name, d.last_name].filter(Boolean).join(" ") || d.username;

  const send = useMutation({
    mutationFn: () => api.post(`/patient-routes/${route.id}/forward/`, { doctor: Number(doctorId), notes }),
    onSuccess: () => {
      const doctor = (doctors ?? []).find((d) => String(d.id) === doctorId);
      onSent(doctor ? doctorName(doctor) : "");
    },
    onError: (error) => showToast({
      title: "Could not send this patient",
      message: readError(error, "Please try again."),
      tone: "error",
    }),
  });

  return (
    <section className="rounded-xl border border-brand-200 bg-brand-50/50 p-5">
      <h2 className="font-medium text-slate-800">Send to a doctor</h2>
      <p className="mb-4 text-sm text-slate-600">
        {hasVitals
          ? "The doctor is notified, gets the chart, and this patient leaves your queue."
          : "Record the patient's vitals first — that is what the doctor is waiting on."}
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="mb-1 block text-sm font-medium">Doctor *</label>
          <select
            value={doctorId}
            onChange={(e) => setDoctorId(e.target.value)}
            disabled={!hasVitals}
            className="rounded-md border px-3 py-2 disabled:bg-slate-100"
          >
            <option value="">Select doctor</option>
            {(doctors ?? []).map((d) => <option key={d.id} value={d.id}>Dr. {doctorName(d)}</option>)}
          </select>
        </div>
        <div className="min-w-[14rem] flex-1">
          <label className="mb-1 block text-sm font-medium">Handover note</label>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={!hasVitals}
            placeholder="Anything the doctor should know first"
            className="w-full rounded-md border px-3 py-2 disabled:bg-slate-100"
          />
        </div>
        <button
          onClick={() => send.mutate()}
          disabled={!hasVitals || !doctorId || send.isPending}
          className="rounded-md bg-brand-600 px-5 py-2.5 text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {send.isPending ? "Sending…" : "Send to doctor"}
        </button>
      </div>
    </section>
  );
}
