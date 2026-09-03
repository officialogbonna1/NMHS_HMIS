import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext.jsx";

// Wrap any route element: <RequireAuth roles={['doctor','admin']}><Page/></RequireAuth>
// Omit `roles` to just require login, any role.
export default function RequireAuth({ children, roles }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <div className="p-6 text-slate-500">Loading…</div>;
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;
  if (roles && !roles.includes(user.role) && !["admin", "hospital_admin"].includes(user.role)) {
    return <div className="p-6 text-red-500">You don't have access to this page.</div>;
  }
  return children;
}
