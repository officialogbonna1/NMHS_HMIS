import { useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { HospitalProvider, useHospital } from "./PrintSheet.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
// The nav and the route guards read from the same groups, so a link can
// never appear for a role that RequireAuth will then turn away.
import {
  ADMIN_ROLES, BED_BOARD_ROLES, BILLING_ROLES, CANCEL_ROLES, CLINICAL_ROLES, FINANCE_REPORT_ROLES,
  POS_HISTORY_ROLES, POS_ROLES, REFUND_ROLES, PATIENT_LOOKUP_ROLES, QUEUE_ROLES,
  hasRole,
} from "../auth/roles.js";
import { Icon } from "./icons.jsx";

const VITALS_ROLES = ["nurse"];
const PHARMACY_ROLES = ["pharmacist"];
const APPOINTMENT_ROLES = ["reception", "doctor"];

// [path, label, icon, roles, group] — omit roles to show for every
// authenticated user. **The roles are unchanged.** `group` is presentation
// only, and a group with nothing visible in it does not render at all, so
// grouping cannot reveal a link a role could not already reach.
const NAV_ITEMS = [
  ["/", "Dashboard", "home", undefined, "Workspace"],
  ["/patients", "Patients", "users", PATIENT_LOOKUP_ROLES, "Workspace"],
  ["/queue", "My Queue", "queue", QUEUE_ROLES, "Workspace"],
  ["/notifications", "Notifications", "bell", undefined, "Workspace"],

  // The label only. The route, the icon, the roles and the page behind it are
  // the nursing vitals station exactly as they were — `VitalsStation.jsx` at
  // `/vitals`, recording `clinical.Vitals`. "Triage" is what the ward calls
  // the job; nothing underneath it is renamed.
  ["/vitals", "Triage", "activity", VITALS_ROLES, "Clinical"],
  ["/send-to-doctor", "Send to Doctor", "handoff", VITALS_ROLES, "Clinical"],
  ["/refer", "Refer Patient", "share", CLINICAL_ROLES, "Clinical"],
  ["/appointments", "Appointments", "calendar", APPOINTMENT_ROLES, "Clinical"],

  // Each referral unit gets a station of its own, the way nursing has
  // /vitals. Admin sees all three.
  ["/laboratory", "Laboratory", "flask", ["laboratory"], "Departments"],
  ["/lab-catalogue", "Lab Catalogue", "list", ["laboratory"], "Departments"],
  ["/ultrasound", "Ultrasound", "scan", ["radiology"], "Departments"],
  ["/eye", "Eye Clinic", "eye", ["optometrist", "ophthalmologist"], "Departments"],
  ["/admissions", "Admissions", "bed", BED_BOARD_ROLES, "Departments"],
  // Both administrators, mirroring `IsAdmin` on the endpoints behind them —
  // the Super Admin and the ordinary `hospital_admin`, because a discharge is
  // a record written once rather than an irreversible act. Discharging from
  // the bed board is unchanged and still lives on /admissions for the ward.
  //
  // Each has an icon of its own, and neither is the bed. `bed` is Admissions,
  // and two rows wearing one icon are two destinations the eye cannot tell
  // apart; `clipboard` was named here but did not exist in the set at all, so
  // `Icon` returned null and that row sat in the sidebar with a blank where
  // its icon should be. `discharge` shares the bed's frame with an arrow
  // leaving it, so the pair reads as the two ends of one stay.
  ["/discharge", "Discharge Patient", "discharge", ADMIN_ROLES, "Departments"],
  ["/discharged", "Discharged Patients", "clipboard", ADMIN_ROLES, "Departments"],

  ["/billing", "Billing", "cash", BILLING_ROLES, "Finance"],
  // The report the cash desk and accounts reconcile from. Narrower than
  // BILLING_ROLES on purpose — see FINANCE_REPORT_ROLES.
  ["/finance", "Financial Report", "price", FINANCE_REPORT_ROLES, "Finance"],
  ["/outstanding", "Outstanding", "clock", BILLING_ROLES, "Finance"],
  ["/waivers", "Waived & Written Off", "tag", BILLING_ROLES, "Finance"],
  // Two desks, deliberately apart, because they are two different decisions.
  // **Refunds** is money going back to a patient — all or part of a payment,
  // the bill staying active. **Service Cancellations** ends the patient's
  // responsibility for a service they never received, and returns everything
  // paid for it when anything was. Both narrower than BILLING_ROLES: reception
  // bills at a window and never reverses what it billed.
  ["/refunds", "Refunds", "refund", REFUND_ROLES, "Finance"],
  ["/service-cancellations", "Service Cancellations", "receiptX", CANCEL_ROLES, "Finance"],
  ["/transactions", "Transaction History", "receipt", BILLING_ROLES, "Finance"],
  ["/billing-items", "Billing Catalog", "price", ["cashier", "accountant"], "Finance"],

  // **Pharmacy is pharmacy work.** Prescriptions, dispensing, the money the
  // counter takes, and the stock on its own shelf — all inside /pharmacy.
  // Configuring the catalogue and running the Main Store are Administration's;
  // a pharmacist has no Administration or Inventory item at all.
  // Most of these open a tab of /pharmacy (`?tab=`), so each carries the
  // /pharmacy guard's roles — a link never outruns the route it opens.
  ["/pharmacy?tab=overview", "Pharmacy Dashboard", "home", PHARMACY_ROLES, "Pharmacy"],
  ["/pharmacy", "Prescriptions", "pill", PHARMACY_ROLES, "Pharmacy"],
  ["/pharmacy?tab=dispensed", "Dispensing", "check", PHARMACY_ROLES, "Pharmacy"],
  // The walk-in till sits beside dispensing, never inside it: same catalogue,
  // same shelf, same FEFO, a different workflow.
  ["/pharmacy/pos", "Pharmacy POS", "cash", POS_ROLES, "Pharmacy"],
  ["/pharmacy/sales", "Sales", "receipt", POS_HISTORY_ROLES, "Pharmacy"],
  ["/pharmacy/sales?tab=returns", "Returns & Refunds", "refund", POS_HISTORY_ROLES, "Pharmacy"],
  ["/pharmacy?tab=products", "Products", "tag", PHARMACY_ROLES, "Pharmacy"],
  ["/pharmacy?tab=stock", "Stock", "box", PHARMACY_ROLES, "Pharmacy"],
  ["/pharmacy?tab=transfer", "Stock Operations", "handoff", PHARMACY_ROLES, "Pharmacy"],
  ["/pharmacy?tab=count", "Stock Count", "list", PHARMACY_ROLES, "Pharmacy"],
  ["/pharmacy?tab=import", "Inventory Import/Export", "archive", PHARMACY_ROLES, "Pharmacy"],

  // **Administration configures inventory.** /inventory is the hospital-wide
  // stock desk — receipts into the store, transfers between locations, counts
  // anywhere, the whole movement ledger — so it lives here with the rest of
  // the setup, for admin and the inventory manager who keeps the store.
  ["/admin", "Administration", "shield", ADMIN_ROLES, "Administration"],
  ["/inventory", "Inventory", "box", ["inventory_manager"], "Administration"],
  ["/inventory?tab=import", "Stock Import / Export", "archive", ["inventory_manager"], "Administration"],
  ["/departments", "Departments", "building", ADMIN_ROLES, "Administration"],
  ["/users", "Users", "shield", ADMIN_ROLES, "Administration"],
];

const GROUP_ORDER = ["Workspace", "Clinical", "Departments", "Finance", "Pharmacy", "Administration"];

export default function AppShell() {
  // The hospital's own details come from `core.HospitalSettings`, so a
  // rename is a form, not a deployment. Loaded once here and shared with
  // every page and printed document below.
  return (
    <HospitalProvider>
      <Shell />
    </HospitalProvider>
  );
}

function Shell() {
  const { user, logout } = useAuth();
  const HOSPITAL = useHospital();
  const location = useLocation();
  // Two states, because a drawer that animates open and vanishes shut is worse
  // than one that does neither. `menuOpen` is "in the DOM"; `menuShown` is
  // "slid into view". Closing lowers the second, and the first follows once
  // the panel has finished travelling.
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuShown, setMenuShown] = useState(false);
  const closeTimer = useRef(null);

  const openMenu = useCallback(() => {
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
    setMenuOpen(true);
    // Two frames: the panel has to be painted at -100% before it is told to
    // travel to 0, or the browser sees one state and animates nothing.
    requestAnimationFrame(() => requestAnimationFrame(() => setMenuShown(true)));
  }, []);

  const closeMenu = useCallback(() => {
    setMenuShown(false);
    if (closeTimer.current) clearTimeout(closeTimer.current);
    // A timer rather than `transitionend`, which never fires when the panel is
    // already at rest or when reduced motion has zeroed the duration — either
    // would strand the drawer mounted and swallowing taps.
    closeTimer.current = setTimeout(() => { setMenuOpen(false); closeTimer.current = null; }, 280);
  }, []);

  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);

  const name = user?.first_name || user?.username || "Staff";
  const items = NAV_ITEMS.filter(([, , , roles]) => hasRole(user, roles));
  const groups = GROUP_ORDER
    .map((group) => ({ group, rows: items.filter((item) => item[4] === group) }))
    .filter((section) => section.rows.length > 0);

  // One number on a timer, so a patient sent through or a fresh set of
  // vitals is visible from whatever page the doctor is already on — a
  // notification nobody knows about is not a notification.
  const { data: unread } = useQuery({
    queryKey: ["unread-count"],
    queryFn: () => api.get("/notifications/unread-count/").then((r) => r.data.unread),
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
    enabled: Boolean(user),
  });

  // Following a link on a phone should leave you on the page, not behind the
  // drawer you opened to get there.
  useEffect(() => { closeMenu(); }, [location.pathname, location.search, closeMenu]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (e) => { if (e.key === "Escape") closeMenu(); };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [menuOpen, closeMenu]);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <a href="#main"
         className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-white focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-brand-700 focus:shadow-lg">
        Skip to content
      </a>

      <header className="sticky top-0 z-30 border-b border-brand-900/40 bg-brand-950 pt-safe text-white">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-1.5 px-2 px-safe sm:h-16 sm:gap-3 sm:px-5">
          {/* Below `lg` this is the only way to any other page — the sidebar
              used to be `hidden md:block` with nothing in its place, which
              left a phone with no navigation at all. */}
          <button
            type="button"
            onClick={openMenu}
            aria-label="Open menu"
            aria-expanded={menuOpen}
            aria-controls="main-nav"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-brand-100 transition hover:bg-brand-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 lg:hidden"
          >
            <Icon name="menu" className="h-5 w-5" />
          </button>

          {/* The wordmark carries the brand on its own now. The tile that used
              to sit beside it held a "+" — a generic medical cross that named
              no hospital and, at 36px next to a four-letter acronym, took as
              much of the bar as the name did. The gradient it was filled with
              moved onto the letters, which is the thing worth looking at. */}
          <Link
            to="/"
            className="ml-0.5 flex min-w-0 items-center rounded-lg px-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
            title={HOSPITAL.fullName}
          >
            <span className="wordmark truncate text-xl font-extrabold tracking-[0.14em] sm:text-2xl">
              {HOSPITAL.name}
            </span>
          </Link>

          <div className="ml-auto flex shrink-0 items-center gap-1.5 sm:gap-2">
            <NavLink
              to="/notifications"
              aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
              className={({ isActive }) => `relative grid h-10 w-10 place-items-center rounded-lg transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 ${
                isActive ? "bg-brand-700 text-white" : "text-brand-100 hover:bg-brand-800"}`}
            >
              <Icon name="bell" className="h-5 w-5" />
              {unread > 0 && (
                <span className="absolute right-0.5 top-0.5 grid min-w-[18px] place-items-center rounded-full bg-red-500 px-1 text-[11px] font-bold leading-[18px] text-white ring-2 ring-brand-950">
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
            </NavLink>

            <div className="hidden min-w-0 text-right sm:block">
              <p className="truncate text-sm font-medium leading-tight">{name}</p>
              <p className="truncate text-xs capitalize leading-tight text-brand-200/80">
                {user?.role?.replaceAll("_", " ")}
              </p>
            </div>
            <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-800 text-sm font-semibold text-brand-200">
              {name.slice(0, 1).toUpperCase()}
            </span>
            <button
              onClick={logout}
              className="grid h-10 w-10 place-items-center rounded-lg text-brand-100 transition hover:bg-brand-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 sm:h-auto sm:w-auto sm:border sm:border-brand-700 sm:px-3 sm:py-2"
            >
              <Icon name="logout" className="h-5 w-5 sm:hidden" />
              <span className="sr-only sm:not-sr-only sm:text-sm sm:font-medium">Sign out</span>
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-[1600px]">
        {/* Desktop / large-tablet rail. */}
        <aside className="nav-scroll sticky top-16 hidden h-[calc(100dvh-4rem)] w-60 shrink-0 border-r border-brand-100 bg-brand-50/70 lg:block xl:w-64">
          <NavList groups={groups} unread={unread} />
        </aside>

        {/* Phone / small-tablet drawer. */}
        {menuOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <div
              className={`absolute inset-0 bg-slate-900/50 transition-opacity duration-200 ease-out motion-reduce:transition-none ${
                menuShown ? "opacity-100" : "opacity-0"}`}
              onClick={closeMenu}
              aria-hidden="true"
            />
            <div
              id="main-nav" role="dialog" aria-modal="true" aria-label="Main navigation"
              className={`absolute inset-y-0 left-0 flex w-[min(82vw,17rem)] flex-col bg-brand-50 pt-safe shadow-2xl
                will-change-transform transition-transform duration-[280ms] ease-[cubic-bezier(0.32,0.72,0,1)]
                motion-reduce:transition-none ${menuShown ? "translate-x-0" : "-translate-x-full"}`}
            >
              <div className="flex h-14 shrink-0 items-center justify-between border-b border-brand-100 px-4">
                <span className="font-semibold text-slate-900">Menu</span>
                <button
                  type="button" onClick={closeMenu} aria-label="Close menu" autoFocus
                  className="grid h-10 w-10 place-items-center rounded-lg text-slate-600 transition hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                >
                  <Icon name="close" className="h-5 w-5" />
                </button>
              </div>
              <div className="nav-scroll min-h-0 flex-1">
                <NavList groups={groups} unread={unread} />
              </div>
              <div className="shrink-0 border-t border-brand-100 px-4 py-3 pb-safe">
                <p className="truncate text-sm font-medium text-slate-900">{name}</p>
                <p className="truncate text-xs capitalize text-slate-600">{user?.role?.replaceAll("_", " ")}</p>
              </div>
            </div>
          </div>
        )}

        <main id="main" className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/**
 * Is this navigation item the page being shown? Several items open tabs of one
 * page (`/pharmacy?tab=stock`), which NavLink cannot tell apart — it compares
 * paths only, so every one of them would light up at once. A tabbed item is
 * active when its path and its query both match; a plain item beside tabbed
 * siblings owns its page only while none of their tabs is showing.
 */
export function navItemActive(to, location, paths) {
  const [path, query] = to.split("?");
  if (path === "/") return location.pathname === "/";
  const current = new URLSearchParams(location.search);
  const matches = (candidate) => [...new URLSearchParams(candidate.split("?")[1] ?? "")]
    .every(([key, value]) => current.get(key) === value);
  if (query) return location.pathname === path && matches(to);
  const tabbed = paths.filter((other) => other.startsWith(`${path}?`));
  if (tabbed.length) return location.pathname === path && !tabbed.some(matches);
  return location.pathname === path || location.pathname.startsWith(`${path}/`);
}

function NavList({ groups, unread }) {
  const location = useLocation();
  const paths = groups.flatMap(({ rows }) => rows.map(([to]) => to));
  return (
    <nav className="p-3">
      {groups.map(({ group, rows }) => (
        <div key={group} className="mb-4 last:mb-2">
          <p className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
            {group}
          </p>
          {rows.map(([to, label, icon]) => {
            const isActive = navItemActive(to, location, paths);
            return (
              <Link
                key={to} to={to} aria-current={isActive ? "page" : undefined}
                className={`mb-0.5 flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition
                  focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 ${
                  isActive
                    ? "bg-white text-brand-800 shadow-[inset_2px_0_0_0] shadow-brand-600"
                    : "text-slate-700 hover:bg-white/70 hover:text-slate-900"}`}
              >
                <Icon name={icon} aria-hidden="true"
                      className={`h-[18px] w-[18px] shrink-0 ${isActive ? "text-brand-600" : "text-slate-500"}`} />
                <span className="min-w-0 flex-1 truncate">{label}</span>
                {to === "/notifications" && unread > 0 && (
                  <span className="shrink-0 rounded-full bg-red-500 px-1.5 py-0.5 text-xs font-bold text-white">
                    {unread > 99 ? "99+" : unread}
                  </span>
                )}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
