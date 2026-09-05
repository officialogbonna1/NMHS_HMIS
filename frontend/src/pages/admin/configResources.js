// What the hospital can configure, described once.
//
// Every entry here is a **Django model**, reached over the same API Django
// admin's ModelAdmin sits on top of. Adding Paracetamol from Administration →
// Products writes the `inventory.Item` row that Django admin then lists;
// editing it there is the row this page reloads. There is deliberately no
// second copy of any of this anywhere.
//
// One config-driven page serves all of them (`ConfigResource.jsx`), the way
// `tileConfig.js` serves the nine health-record tiles: adding a configurable
// entity should be an entry here, not another near-identical page.
//
// Field types: text · textarea · number · money · select · reference · toggle
// A `reference` field names another endpoint; the page fetches it for the
// dropdown, so relationships are managed rather than typed.

export const CONFIG_RESOURCES = {
  products: {
    title: "Products",
    group: "Inventory",
    icon: "box",
    endpoint: "items",
    blurb: "The drug and consumable catalogue. Quantities are not set here — "
      + "stock arrives by receipt and moves by transfer.",
    adminOnly: true,
    searchPlaceholder: "Search products…",
    columns: [
      { key: "name", label: "Product", strong: true },
      { key: "category_name", label: "Category" },
      { key: "unit_label", label: "Unit" },
      { key: "total_quantity", label: "On hand", align: "right" },
      { key: "reorder_threshold", label: "Reorder at", align: "right" },
    ],
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "category", label: "Category", type: "reference", endpoint: "item-categories",
        hint: "Manage the list under Categories." },
      { name: "unit", label: "Unit of measure", type: "reference", endpoint: "units" },
      { name: "reorder_threshold", label: "Reorder threshold", type: "number", default: 10,
        hint: "The hospital-wide total below which this product is flagged as low." },
      { name: "is_active", label: "Active", type: "toggle", default: true },
    ],
    // What makes a row undeletable, so the page can say so before the API does.
    usedWhen: (row) => row.batch_count > 0 || row.total_quantity > 0,
    usedNote: "This product has stock or history behind it — deactivate it instead.",
  },

  "item-categories": {
    title: "Product categories",
    group: "Inventory",
    icon: "tag",
    endpoint: "item-categories",
    blurb: "How the catalogue is grouped. A category is retired, never deleted, "
      + "once products are filed under it.",
    adminOnly: true,
    columns: [
      { key: "name", label: "Category", strong: true },
      { key: "description", label: "Description" },
      { key: "item_count", label: "Products", align: "right" },
      { key: "display_order", label: "Order", align: "right" },
    ],
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "description", label: "Description", type: "text" },
      { name: "display_order", label: "Display order", type: "number", default: 100 },
      { name: "is_active", label: "Active", type: "toggle", default: true },
    ],
    usedWhen: (row) => row.item_count > 0,
    usedNote: "Products are filed under this category.",
  },

  units: {
    title: "Units of measure",
    group: "Inventory",
    icon: "list",
    endpoint: "units",
    blurb: "Tablets, bottles, vials. The abbreviation is what a prescription "
      + "and a dispensing label print.",
    adminOnly: true,
    columns: [
      { key: "name", label: "Unit", strong: true },
      { key: "abbreviation", label: "Prints as" },
      { key: "item_count", label: "Products", align: "right" },
      { key: "display_order", label: "Order", align: "right" },
    ],
    fields: [
      { name: "name", label: "Name", type: "text", required: true, placeholder: "Tablet" },
      { name: "abbreviation", label: "Abbreviation", type: "text", placeholder: "tab",
        hint: "What labels print. Falls back to the name when empty." },
      { name: "description", label: "Description", type: "text" },
      { name: "display_order", label: "Display order", type: "number", default: 100 },
      { name: "is_active", label: "Active", type: "toggle", default: true },
    ],
    usedWhen: (row) => row.item_count > 0,
    usedNote: "Products are measured in this unit.",
  },

  "stock-locations": {
    title: "Stock locations",
    group: "Inventory",
    icon: "building",
    endpoint: "stock-locations",
    blurb: "Where stock stands. Main Store receives deliveries; the Pharmacy is "
      + "the only place patients are dispensed from.",
    // The two flags decide where every delivery lands and every prescription
    // draws from, so this one is admin-only on the server as well.
    adminOnly: true,
    columns: [
      { key: "name", label: "Location", strong: true },
      { key: "code", label: "Code" },
      { key: "kind", label: "Kind" },
      { key: "total_units", label: "Units held", align: "right" },
    ],
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "code", label: "Code", type: "text", required: true, slugFrom: "name" },
      { name: "kind", label: "Kind", type: "select", default: "store",
        options: [["store", "Store"], ["dispensary", "Dispensary"]] },
      { name: "description", label: "Description", type: "text" },
      { name: "display_order", label: "Display order", type: "number", default: 100 },
      { name: "is_active", label: "Active", type: "toggle", default: true },
    ],
    // Read-only on the serializer too: moving the dispensing point changes
    // where every prescription draws from, so it is not a form field.
    notes: "Which location receives deliveries and which one dispenses is set in "
      + "Django admin — moving either flag changes where every delivery and every "
      + "prescription goes.",
    usedWhen: (row) => row.total_units > 0,
    usedNote: "This location is holding stock.",
  },

  services: {
    title: "Services",
    group: "Hospital",
    icon: "price",
    endpoint: "services",
    blurb: "What each department offers, and what it costs.",
    adminOnly: true,
    columns: [
      { key: "name", label: "Service", strong: true },
      { key: "department_name", label: "Department" },
      { key: "code", label: "Code" },
      { key: "price", label: "Price", align: "right", money: true },
    ],
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "code", label: "Code", type: "text", required: true, slugFrom: "name" },
      { name: "department", label: "Department", type: "reference", endpoint: "departments",
        required: true },
      { name: "price", label: "Price", type: "money", default: "0" },
      { name: "is_active", label: "Active", type: "toggle", default: true },
    ],
  },

  wards: {
    title: "Wards",
    group: "Hospital",
    icon: "bed",
    endpoint: "wards",
    blurb: "The wards patients are admitted to. Beds hang off them.",
    adminOnly: true,
    columns: [
      { key: "name", label: "Ward", strong: true },
      { key: "bed_count", label: "Beds", align: "right" },
      { key: "occupied_count", label: "Occupied", align: "right" },
    ],
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "department", label: "Department", type: "reference", endpoint: "departments" },
      { name: "is_active", label: "Active", type: "toggle", default: true },
    ],
    usedWhen: (row) => row.bed_count > 0,
    usedNote: "This ward has beds. Remove them first, or deactivate the ward.",
  },

  beds: {
    title: "Beds",
    group: "Hospital",
    icon: "bed",
    endpoint: "beds",
    blurb: "Every bed on every ward. A bed somebody has occupied is part of the "
      + "stay record and is deactivated rather than deleted.",
    adminOnly: true,
    columns: [
      { key: "number", label: "Bed", strong: true },
      { key: "ward_name", label: "Ward" },
      { key: "occupant", label: "Occupied by" },
    ],
    fields: [
      { name: "ward", label: "Ward", type: "reference", endpoint: "wards", required: true },
      { name: "number", label: "Bed number", type: "text", required: true },
      { name: "is_active", label: "Active", type: "toggle", default: true },
    ],
    usedWhen: (row) => Boolean(row.occupant),
    usedNote: "Somebody is in this bed.",
  },
};

// The Administration hub, grouped. Entries without an `endpoint` are the
// dedicated pages that already exist — they are listed here so the section is
// the one place to look, not so they get rebuilt.
export const ADMIN_SECTIONS = [
  {
    group: "Hospital",
    entries: [
      { to: "/admin/settings", title: "Hospital settings", icon: "building",
        blurb: "Name and address printed on every document, and the thresholds the "
          + "dashboards alert on.", adminOnly: true },
      { to: "/departments", title: "Departments", icon: "building",
        blurb: "Units, the staff attached to them and the services each offers.",
        adminOnly: true },
      { to: "/admin/services", title: "Services", icon: "price",
        blurb: "What each department offers, and its price.", adminOnly: true },
      { to: "/users", title: "Users & roles", icon: "shield",
        blurb: "Staff accounts, roles and access.", adminOnly: true },
      { to: "/admin/wards", title: "Wards", icon: "bed",
        blurb: "Wards patients are admitted to.", adminOnly: true },
      { to: "/admin/beds", title: "Beds", icon: "bed",
        blurb: "Beds on each ward.", adminOnly: true },
    ],
  },
  {
    group: "Inventory",
    // Configuration first, then the operations it makes possible, then the
    // ledger they leave behind — the order stock actually moves in:
    // Supplier → Main Store → Pharmacy → patient.
    entries: [
      { to: "/admin/products", title: "Products", icon: "box",
        blurb: "The drug and consumable catalogue every prescription draws from.",
        adminOnly: true },
      { to: "/admin/item-categories", title: "Product categories", icon: "tag",
        blurb: "How the catalogue is grouped.", adminOnly: true },
      { to: "/admin/units", title: "Units of measure", icon: "list",
        blurb: "What products are counted and labelled in.", adminOnly: true },
      { to: "/admin/stock-locations", title: "Stock locations", icon: "building",
        blurb: "Main Store, Pharmacy, and anywhere else stock stands.",
        adminOnly: true },
      { to: "/inventory", title: "Stock operations", icon: "box",
        blurb: "Receive deliveries into the store and transfer stock to the pharmacy.",
        roles: ["inventory_manager"] },
      { to: "/inventory?tab=count", title: "Physical counts", icon: "list",
        blurb: "Count a location's shelves and post the difference.",
        roles: ["inventory_manager"] },
      { to: "/inventory?tab=stock", title: "Adjustments & write-offs", icon: "alert",
        blurb: "Correct a shelf, or take expired stock off it.",
        roles: ["inventory_manager"] },
      { to: "/inventory?tab=movements", title: "Movement log", icon: "receipt",
        blurb: "Every unit that moved, where it moved and who moved it.",
        roles: ["inventory_manager"] },
    ],
  },
  {
    group: "Clinical & finance",
    entries: [
      { to: "/lab-catalogue", title: "Laboratory catalogue", icon: "flask",
        blurb: "Tests, parameters, reference ranges and prices.",
        roles: ["laboratory"] },
      { to: "/billing-items", title: "Billing catalogue", icon: "price",
        blurb: "The price list every counter quotes from.",
        roles: ["cashier", "accountant"] },
      { to: "/admin/notifications", title: "Notification settings", icon: "bell",
        blurb: "Which categories of notification the hospital sends.", adminOnly: true },
    ],
  },
];
