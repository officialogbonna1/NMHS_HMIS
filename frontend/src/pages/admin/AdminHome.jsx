import { Link } from "react-router-dom";
import { Page, PageHeader, Alert } from "../../components/ui.jsx";
import { Icon } from "../../components/icons.jsx";
import { useAuth } from "../../auth/AuthContext.jsx";
import { hasRole } from "../../auth/roles.js";
import { ADMIN_SECTIONS } from "./configResources.js";

// The one place to look for "where do I set this up?".
//
// It gathers every configuration screen — the new config-driven ones and the
// dedicated pages that already existed (Departments, Users, the laboratory
// catalogue, the billing catalogue) — rather than rebuilding any of them.
// Each opens a screen that edits Django models directly, so anything set here
// is what Django admin shows, and anything a technical administrator changes
// there is what these screens load.

export default function AdminHome() {
  const { user } = useAuth();

  const sections = ADMIN_SECTIONS
    .map((section) => ({
      ...section,
      // A card that only leads to a 403 is worse than no card. The API is
      // what actually refuses; this keeps the page honest.
      entries: section.entries.filter((entry) =>
        entry.adminOnly ? hasRole(user, []) : hasRole(user, entry.roles)),
    }))
    .filter((section) => section.entries.length > 0);

  return (
    <Page width="wide">
      <PageHeader
        icon="shield"
        title="Administration"
        subtitle="Set the hospital up: departments and staff, the product catalogue, wards and beds, prices and settings."
      />

      <Alert tone="info" className="mb-6" title="One database, two ways in">
        These screens and Django admin edit the same records. Add a product here and
        it is in Django admin immediately; change it there and it is here on the next
        load. Configuration that history points at is deactivated rather than deleted,
        so old documents keep reading correctly.
      </Alert>

      <div className="space-y-8">
        {sections.map((section) => (
          <section key={section.group}>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-600">
              {section.group}
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {section.entries.map((entry) => (
                <Link
                  key={entry.to}
                  to={entry.to}
                  className="group flex min-w-0 gap-3 rounded-xl border border-slate-200 bg-white p-4 transition hover:border-brand-300 hover:shadow-sm"
                >
                  <span aria-hidden="true"
                        className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-100">
                    <Icon name={entry.icon} className="h-5 w-5" />
                  </span>
                  <span className="min-w-0">
                    <span className="block font-medium text-slate-900 group-hover:text-brand-800">
                      {entry.title}
                    </span>
                    <span className="mt-0.5 block text-sm text-slate-600">{entry.blurb}</span>
                  </span>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </Page>
  );
}
