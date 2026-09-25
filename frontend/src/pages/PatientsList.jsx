import { useEffect, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import api from "../api/client";
import { useAuth } from "../auth/AuthContext.jsx";
import { EmptyState, Page, PageHeader, SearchInput, Skeleton, Button, MetaStat } from "../components/ui.jsx";
import { Icon } from "../components/icons.jsx";
// The listing itself — rows, letter sections and the A–Z rail — lives in one
// place, because the maternity desk shows the same list of patients and two
// spellings of it would drift.
import { LetterRail, PatientLinkRow, PatientSections } from "../components/patientListing.jsx";

const CAN_REGISTER_ROLES = ["reception", "admin", "hospital_admin"];

// Mirrors the reference manual's "Your Patients" screen: alphabetical
// listing, live search by name, letter jump.
export default function PatientsList() {
  const { user } = useAuth();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");

  // The search box used to be the query key directly, so every keystroke of
  // "Okonkwo" fired eight requests at `/patients/` and the list flickered
  // through eight results. A quarter second of settling makes it one, and
  // `keepPreviousData` holds the last list on screen while it lands.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["patients", query],
    queryFn: () => api.get("/patients/", { params: { search: query } }).then((r) => r.data.results ?? r.data),
    placeholderData: keepPreviousData,
  });

  const patients = data ?? [];

  return (
    // No `pr-14` any more: that gutter was reserving room for the letter rail
    // at every width, including the phones where the rail is not rendered.
    <Page width="narrow" className="relative sm:pr-12">
      <PageHeader
        icon="users"
        title="Patients"
        subtitle="View, register and manage patient records."
        meta={data && <MetaStat value={patients.length} label={`patient${patients.length === 1 ? "" : "s"}`} />}
        actions={CAN_REGISTER_ROLES.includes(user?.role) && (
          <Button to="/patients/new">
            <Icon name="plus" className="h-4 w-4" aria-hidden="true" />
            Register patient
          </Button>
        )}
        toolbar={
          <div className="min-w-0 flex-1">
            <SearchInput
              value={search}
              onChange={setSearch}
              label="Search patients"
              placeholder="Name or file number (e.g. NMHS-000001)"
            />
            {/* Only while a *new* search is in flight — the list below stays put. */}
            {isFetching && !isLoading && (
              <p className="mt-1.5 text-sm text-slate-600" role="status">Searching…</p>
            )}
          </div>
        }
      />

      {isLoading && (
        <div className="space-y-2" role="status" aria-label="Loading patients">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
              <Skeleton className="h-11 w-11 shrink-0 rounded-full" />
              <div className="min-w-0 flex-1 space-y-2">
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="h-3 w-1/4" />
              </div>
            </div>
          ))}
        </div>
      )}

      {!isLoading && patients.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white">
          <EmptyState
            icon="users"
            title={search ? "No patients match that search" : "No patients registered yet"}
            description={search ? "Try part of a surname, or the file number." : undefined}
          />
        </div>
      )}

      <PatientSections
        patients={patients}
        renderRow={(p) => <PatientLinkRow key={p.id} patient={p} />}
      />

      <LetterRail patients={patients} />
    </Page>
  );
}
