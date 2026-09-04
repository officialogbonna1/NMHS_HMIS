import { Link, Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext.jsx";
import { hasRole } from "./roles.js";

// Wrap any route element: <RequireAuth roles={['doctor','admin']}><Page/></RequireAuth>
// Omit `roles` to just require login, any role. Admin roles pass everything.
//
// This is a courtesy guard, not the control: it keeps somebody off a page that
// would only 403 at them, and keeps a nav item from lying. The API is what
// actually refuses — see apps/core/tests/test_api_permissions.py.
export default function RequireAuth({ children, roles }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <div className="p-6 text-slate-600">Loading…</div>;
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;
  if (!hasRole(user, roles)) return <NoAccess role={user.role} />;
  return children;
}

function NoAccess({ role }) {
  return (
    <div className="mx-auto max-w-lg p-8">
      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-6">
        <h1 className="text-lg font-semibold text-slate-900">This page isn’t part of your role</h1>
        <p className="mt-2 text-sm text-slate-700">
          You are signed in as <span className="font-medium">{role?.replaceAll("_", " ")}</span>,
          which doesn’t cover this page. If you need it, ask an administrator to
          change your role — nothing here is hidden by accident.
        </p>
        <Link to="/" className="mt-4 inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
          Back to dashboard
        </Link>
      </div>
    </div>
  );
}
