import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Page, PageHeader, MetaStat, TabBar, Tab } from "../components/ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { ADMIN_ROLES } from "../auth/roles.js";
import { ResourceForm } from "./admin/ConfigResource.jsx";
import { CONFIG_RESOURCES } from "./admin/configResources.js";
import {
  CountImportExport, MovementLog, PhysicalCount, ReceiveStock, StockOnHand, TransferStock, useLocations,
} from "../components/StockPanels.jsx";

// **Administration → Inventory.** Stock control across every location the
// hospital holds.
//
// This page belongs to Administration, not to Pharmacy. The distinction is
// the one the whole module turns on:
//
//   Administration configures and runs inventory — the catalogue, the
//   locations, receipts into the Main Store, transfers between locations,
//   counts anywhere, and the whole movement ledger.
//
//   Pharmacy operates *pharmacy* stock — what is standing on the dispensing
//   shelf, what it is owed from the store, and what it hands to patients.
//
// The panels themselves are shared (`components/StockPanels.jsx`): the
// Pharmacy counter renders the same components pinned to its own location, so
// the two workspaces can never drift into two different stock screens.
//
// The flow is unchanged: Supplier → Receipt → Main Store → Transfer →
// Pharmacy → Dispensing → Patient.

const TABS = [
  ["stock", "Stock on hand"],
  // Creating a catalogue item is configuration, and `/api/items/` writes are
  // `IsAdmin` on the server — so the tab is offered to the administrators
  // alone (`adminOnly`), never to the inventory manager or the pharmacy, who
  // would only be refused. The server is what actually refuses.
  ["add-item", "Add item", { adminOnly: true }],
  ["receive", "Receive stock"],
  ["transfer", "Transfer"],
  ["count", "Physical count"],
  ["import", "Import / export"],
  ["movements", "Movement log"],
];

export default function InventoryDashboard() {
  // The tab lives in the URL so Administration's Inventory cards can open the
  // one they name — "Physical counts" has to land on the count sheet, not on
  // a page the person then has to find it in.
  const [params, setParams] = useSearchParams();
  const { user } = useAuth();
  const isAdmin = ADMIN_ROLES.includes(user?.role ?? "");
  const tabs = TABS.filter(([, , options]) => !options?.adminOnly || isAdmin);
  const requested = params.get("tab");
  const tab = tabs.some(([key]) => key === requested) ? requested : "stock";
  const setTab = (key) => setParams(key === "stock" ? {} : { tab: key }, { replace: true });

  const { data: locations } = useLocations();
  const store = locations?.find((l) => l.is_default_receiving) ?? locations?.[0];
  const counter = locations?.find((l) => l.is_dispensing_point);

  return (
    <Page width="wide">
      <PageHeader
        icon="box"
        title="Inventory"
        subtitle="Stock is tracked per batch and per location. Deliveries arrive in the Main Store; the Pharmacy is stocked from it by transfer."
        meta={
          <>
            {(locations ?? []).map((location) => (
              <MetaStat
                key={location.id}
                value={location.total_units}
                label={`units · ${location.name}`}
                tone={location.is_dispensing_point ? "brand" : "neutral"}
              />
            ))}
          </>
        }
      />

      <TabBar label="Inventory sections">
        {tabs.map(([key, label]) => (
          <Tab key={key} active={tab === key} onClick={() => setTab(key)}>{label}</Tab>
        ))}
      </TabBar>

      {tab === "stock" && <StockOnHand locations={locations} />}
      {tab === "add-item" && <AddItem onReceive={() => setTab("receive")} />}
      {tab === "receive" && <ReceiveStock locations={locations} store={store} />}
      {tab === "transfer" && <TransferStock locations={locations} store={store} counter={counter} />}
      {tab === "count" && <PhysicalCount locations={locations} store={store} />}
      {tab === "import" && <CountImportExport locations={locations} />}
      {tab === "movements" && <MovementLog locations={locations} />}
    </Page>
  );
}

/**
 * Inventory → Add item: a new catalogue item, from the inventory desk.
 *
 * **Not a second form.** It renders Administration → Products' own form
 * (`ResourceForm` with `CONFIG_RESOURCES.products`) — the Django admin
 * fieldsets, the same `POST /api/items/`, the same serializer validation and
 * the same audit row — worded as a "medical item", because the catalogue holds
 * cotton wool and syringes as well as medicines.
 *
 * On success the form resets and the `["items"]` query the Receive stock
 * picker reads is refreshed, so the new item is selectable there at once.
 */
function AddItem({ onReceive }) {
  const queryClient = useQueryClient();
  const [created, setCreated] = useState(null);
  const [formKey, setFormKey] = useState(0);

  return (
    <div className="max-w-3xl min-w-0 space-y-4">
      {created && (
        <Alert tone="success" title={`${created.name} added`}>
          <div className="flex flex-wrap items-center gap-3">
            <span>It is in the catalogue and can be received now.</span>
            <Button size="sm" variant="secondary" onClick={onReceive}>Receive stock</Button>
          </div>
        </Alert>
      )}
      <ResourceForm
        key={formKey}
        config={CONFIG_RESOURCES.products}
        row={null}
        title="New medical item"
        submitLabel="Add item"
        successTitle="Medical item added"
        onDone={(item) => {
          queryClient.invalidateQueries({ queryKey: ["items"] });
          setCreated(item ?? null);
          setFormKey((k) => k + 1);      // a fresh, empty form for the next one
        }}
      />
    </div>
  );
}
