import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";

// Anything that doesn't match a route lands here rather than rendering
// nothing. A blank page gives no clue what went wrong; this at least names
// the address and offers a way back.
export default function NotFound() {
  const location = useLocation();
  const { user } = useAuth();

  return (
    <div className="mx-auto max-w-xl p-8">
      <div className="rounded-2xl border bg-white p-8 text-center">
        <p className="text-4xl">🧭</p>
        <h1 className="mt-3 text-xl font-semibold">That page doesn't exist</h1>
        <p className="mt-2 text-sm text-slate-500">
          Nothing is served at <code className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">{location.pathname}</code>.
          {user ? " It may be an old link — the page it pointed to has moved or been renamed." : ""}
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <Link to="/" className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
            Go to my dashboard
          </Link>
          <Link to="/patients" className="rounded-lg border px-4 py-2 text-sm font-medium hover:bg-slate-50">
            Patients
          </Link>
        </div>
      </div>
    </div>
  );
}
