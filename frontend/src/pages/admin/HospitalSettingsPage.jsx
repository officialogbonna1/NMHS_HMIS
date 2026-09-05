import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../../api/client";
import { readError } from "../../api/errors";
import { useToast } from "../../components/Toaster.jsx";
import {
  Alert, Badge, Button, Field, Input, Page, PageHeader, Section,
} from "../../components/ui.jsx";

// The hospital's own details and the numbers the application treats as
// tunable — one Django row (`core.HospitalSettings`), edited here or in
// Django admin.
//
// Everything on this form changes real behaviour. The identity block is the
// letterhead on every printed document and the name in the application
// header; each threshold drives an alert that somebody acts on. A setting
// that changed nothing would be worse than no setting at all: it gets edited,
// nothing happens, and the screen stops being believed.

export default function HospitalSettingsPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [form, setForm] = useState(null);
  const [error, setError] = useState(null);

  const { data, isLoading } = useQuery({
    queryKey: ["hospital-settings"],
    queryFn: () => api.get("/hospital-settings/current/").then((r) => r.data),
  });

  useEffect(() => { if (data && !form) setForm(data); }, [data, form]);

  const save = useMutation({
    mutationFn: () => api.patch("/hospital-settings/current/", form),
    onSuccess: (response) => {
      setForm(response.data);
      queryClient.invalidateQueries({ queryKey: ["hospital-settings"] });
      showToast({ title: "Settings saved", message: "Documents and alerts follow immediately." });
    },
    onError: (err) => setError(readError(err, "Could not save these settings.")),
  });

  const set = (name, value) => setForm((f) => ({ ...f, [name]: value }));

  if (isLoading || !form) return <Page><p className="text-slate-600">Loading…</p></Page>;

  return (
    <Page width="default">
      <PageHeader
        icon="building"
        title="Hospital settings"
        subtitle="The hospital's own details, and the thresholds the dashboards alert on."
      />

      <form
        onSubmit={(e) => { e.preventDefault(); setError(null); save.mutate(); }}
        className="space-y-6"
      >
        <Section
          title="Identity"
          description="Printed on the patient card, invoices, receipts and laboratory reports, and shown in the application header."
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Short name" hint="What the header and a document footer use.">
              <Input value={form.name ?? ""} onChange={(e) => set("name", e.target.value)} />
            </Field>
            <Field label="Full name" hint="The letterhead on every printed document.">
              <Input value={form.full_name ?? ""} onChange={(e) => set("full_name", e.target.value)} />
            </Field>
            <Field label="Address">
              <Input value={form.address ?? ""} onChange={(e) => set("address", e.target.value)} />
            </Field>
            <Field label="Phone">
              <Input value={form.phone ?? ""} onChange={(e) => set("phone", e.target.value)} />
            </Field>
            <Field label="Email">
              <Input type="email" value={form.email ?? ""}
                     onChange={(e) => set("email", e.target.value)} />
            </Field>
          </div>
        </Section>

        <Section
          title="Alert thresholds"
          description="Each of these drives an alert somebody acts on. Changing one changes what the dashboards say the next time they load."
        >
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Expiry warning (days)"
                   hint="Stock this close to expiry is flagged, on the dashboards and in the nightly check.">
              <Input type="number" min="1" value={form.expiry_warning_days ?? 30}
                     onChange={(e) => set("expiry_warning_days", Number(e.target.value))} />
            </Field>
            <Field label="Vitals wait (minutes)"
                   hint="How long a patient may wait before the nursing dashboard says so.">
              <Input type="number" min="1" value={form.vitals_wait_alert_minutes ?? 30}
                     onChange={(e) => set("vitals_wait_alert_minutes", Number(e.target.value))} />
            </Field>
            <Field label="Unpaid charge alert (hours)"
                   hint="A charge this old and still unpaid is flagged to the cash desk.">
              <Input type="number" min="1" value={form.unpaid_charge_alert_hours ?? 2}
                     onChange={(e) => set("unpaid_charge_alert_hours", Number(e.target.value))} />
            </Field>
          </div>
        </Section>

        {error && <p className="text-sm text-red-700">{error}</p>}

        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save settings"}
        </Button>
      </form>
    </Page>
  );
}

// --------------------------------------------------------------------------

/**
 * Which categories of notification the hospital sends.
 *
 * Read by `core.services.notify()` on every notification raised, so switching
 * one off here actually stops them rather than only claiming to. Clinical is
 * shown but locked: a result reaching the clinician who ordered it is not a
 * preference, and the server ignores a setting that says otherwise.
 */
export function NotificationSettingsPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const { data: settings, isLoading } = useQuery({
    queryKey: ["notification-settings"],
    queryFn: () => api.get("/notification-settings/").then((r) => r.data.results ?? r.data),
  });

  const toggle = useMutation({
    mutationFn: (setting) => api.patch(`/notification-settings/${setting.id}/`,
                                       { is_enabled: !setting.is_enabled }),
    onSuccess: (_r, setting) => {
      queryClient.invalidateQueries({ queryKey: ["notification-settings"] });
      showToast({
        title: setting.is_enabled ? "Switched off" : "Switched on",
        message: `${setting.category_label}`,
      });
    },
    onError: (error) => showToast({
      title: "Could not change this",
      message: readError(error, "Please try again."),
      tone: "error",
    }),
  });

  return (
    <Page width="default">
      <PageHeader
        icon="bell"
        title="Notification settings"
        subtitle="Which kinds of notification the hospital sends. Switching one off stops those notifications being raised at all."
      />

      <Alert tone="warning" className="mb-4">
        Switching a category off means nobody is told. Somebody still has to find the
        work — a referral nobody is notified about sits in the queue until it is noticed.
      </Alert>

      {isLoading && <p className="text-slate-600">Loading…</p>}

      <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
        {(settings ?? []).map((setting) => (
          <div key={setting.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-4">
            <div className="min-w-0">
              <p className="font-medium text-slate-900">{setting.category_label}</p>
              {setting.description && (
                <p className="mt-0.5 text-sm text-slate-600">{setting.description}</p>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-3">
              {setting.is_enabled
                ? <Badge tone="success">On</Badge>
                : <Badge tone="neutral">Off</Badge>}
              {setting.can_disable ? (
                <Button
                  variant={setting.is_enabled ? "secondary" : "primary"}
                  size="sm"
                  onClick={() => toggle.mutate(setting)}
                  disabled={toggle.isPending}
                >
                  {setting.is_enabled ? "Switch off" : "Switch on"}
                </Button>
              ) : (
                <span className="text-sm text-slate-600">Always on</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </Page>
  );
}
