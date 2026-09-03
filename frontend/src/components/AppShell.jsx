import { Link, NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import api from "../api/client";
import { useAuth } from "../auth/AuthContext.jsx";

const ADMIN_ROLES = ["admin", "hospital_admin"];
// Everyone a patient route can be sent to — they work it from /queue.
const QUEUE_ROLES = ["reception", "doctor", "nurse", "laboratory", "radiology",
                     "optometrist", "ophthalmologist", ...ADMIN_ROLES];
const VITALS_ROLES = ["nurse", ...ADMIN_ROLES];
const PHARMACY_ROLES = ["pharmacist", ...ADMIN_ROLES];
const INVENTORY_ROLES = ["pharmacist", "inventory_manager", ...ADMIN_ROLES];
const BILLING_ROLES = ["cashier", "accountant", "reception", ...ADMIN_ROLES];
const APPOINTMENT_ROLES = ["reception", "doctor", ...ADMIN_ROLES];

// [path, label, icon, roles] — omit roles to show for every authenticated user.
const NAV_ITEMS = [
  ["/", "Dashboard", "⌂"],
  ["/patients", "Patients", "♙"],
  ["/queue", "My Queue", "➜", QUEUE_ROLES],
  ["/vitals", "Vitals", "🩺", VITALS_ROLES],
  ["/send-to-doctor", "Send to Doctor", "🫱", VITALS_ROLES],
  ["/refer", "Refer Patient", "📤", ["doctor", ...ADMIN_ROLES]],
  // Each referral unit gets a station of its own, the way nursing has
  // /vitals. Admin sees all three.
  ["/laboratory", "Laboratory", "🧫", ["laboratory", ...ADMIN_ROLES]],
  ["/ultrasound", "Ultrasound", "🩻", ["radiology", ...ADMIN_ROLES]],
  ["/eye", "Eye Clinic", "👁", ["optometrist", "ophthalmologist", ...ADMIN_ROLES]],
  ["/appointments", "Appointments", "📅", APPOINTMENT_ROLES],
  ["/billing", "Billing", "₦", BILLING_ROLES],
  ["/transactions", "Transaction History", "🧾", BILLING_ROLES],
  ["/pharmacy", "Pharmacy", "💊", PHARMACY_ROLES],
  ["/inventory", "Inventory", "▣", INVENTORY_ROLES],
  ["/notifications", "Notifications", "🔔"],
  ["/departments", "Departments", "⌘", ADMIN_ROLES],
  ["/billing-items", "Billing Catalog", "🪪", ["cashier", "accountant", ...ADMIN_ROLES]],
  ["/users", "Users", "☺", ADMIN_ROLES],
];

export default function AppShell() {
  const { user, logout } = useAuth();
  const name = user?.first_name || user?.username || "Staff";
  const items = NAV_ITEMS.filter(([, , , roles]) => !roles || roles.includes(user?.role));

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

  return <div className="min-h-screen bg-brand-50 text-slate-900"><header className="sticky top-0 z-20 h-[74px] border-b border-white/10 bg-brand-950 px-4 text-white shadow-lg md:px-7"><div className="mx-auto flex h-full max-w-[1600px] items-center justify-between"><Link to="/" className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-brand-300 to-brand-500 font-bold text-brand-950 shadow-lg shadow-brand-500/20">+</span><span className="text-lg font-semibold tracking-tight">NMHS</span></Link><div className="flex items-center gap-3"><NavLink to="/notifications" aria-label={unread ? `${unread} unread notifications` : "Notifications"} className={({isActive})=>`relative grid h-9 w-9 place-items-center rounded-full transition ${isActive ? "bg-brand-700 text-white" : "bg-brand-800/60 text-brand-100 hover:bg-brand-700"}`}><span className="text-base">🔔</span>{unread > 0 && <span className="absolute -right-1 -top-1 grid min-w-[20px] place-items-center rounded-full bg-red-500 px-1 text-[11px] font-bold leading-5 text-white ring-2 ring-brand-950">{unread > 99 ? "99+" : unread}</span>}</NavLink><div className="hidden text-right sm:block"><p className="text-sm font-medium">{name}</p><p className="text-xs text-brand-200/70">{user?.role?.replaceAll("_", " ")}</p></div><span className="grid h-9 w-9 place-items-center rounded-full bg-brand-800 text-sm font-semibold text-brand-200">{name.slice(0, 1).toUpperCase()}</span><button onClick={logout} className="rounded-lg border border-brand-700 px-3 py-2 text-xs font-medium text-brand-200 transition hover:border-brand-400 hover:text-brand-300">Sign out</button></div></div></header><div className="mx-auto flex max-w-[1600px]"><aside className="hidden min-h-[calc(100vh-74px)] w-64 border-r border-brand-100 bg-white p-4 md:block"><p className="px-3 pb-3 pt-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-brand-400">Workspace</p>{items.map(([to,label,icon])=><NavLink key={to} to={to} end={to==="/"} className={({isActive})=>`mb-1 flex items-center gap-3 rounded-xl px-3 py-3 text-sm font-medium transition ${isActive ? "bg-gradient-to-r from-brand-600 to-brand-500 text-white shadow-md shadow-brand-500/20" : "text-slate-600 hover:bg-brand-50 hover:text-brand-950"}`}><span className="text-base">{icon}</span><span className="flex-1">{label}</span>{to === "/notifications" && unread > 0 && <span className="rounded-full bg-red-500 px-2 py-0.5 text-xs font-bold text-white">{unread > 99 ? "99+" : unread}</span>}</NavLink>)}</aside><main className="min-w-0 flex-1"><Outlet /></main></div></div>;
}
