import { useSearchParams } from "react-router-dom";
import { Page, PageHeader, MetaStat, TabBar, Tab } from "../components/ui.jsx";
import {
  MovementLog, PhysicalCount, ReceiveStock, StockOnHand, TransferStock, useLocations,
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
  ["receive", "Receive stock"],
  ["transfer", "Transfer"],
  ["count", "Physical count"],
  ["movements", "Movement log"],
];

export default function InventoryDashboard() {
  // The tab lives in the URL so Administration's Inventory cards can open the
  // one they name — "Physical counts" has to land on the count sheet, not on
  // a page the person then has to find it in.
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab = TABS.some(([key]) => key === requested) ? requested : "stock";
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
        {TABS.map(([key, label]) => (
          <Tab key={key} active={tab === key} onClick={() => setTab(key)}>{label}</Tab>
        ))}
      </TabBar>

      {tab === "stock" && <StockOnHand locations={locations} />}
      {tab === "receive" && <ReceiveStock locations={locations} store={store} />}
      {tab === "transfer" && <TransferStock locations={locations} store={store} counter={counter} />}
      {tab === "count" && <PhysicalCount locations={locations} store={store} />}
      {tab === "movements" && <MovementLog locations={locations} />}
    </Page>
  );
}
