import { useEffect, useRef, useState } from "react";
import { HOSPITAL } from "../components/PrintSheet.jsx";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";
import { lockoutFrom, lockoutMessage } from "./loginLockout.js";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  // Seconds left on a server-issued lockout, or null. Seeded from the 429 and
  // then run down here purely so the page can say something useful while it
  // waits — the server is what refuses, and it is asked again on every submit.
  const [lockedFor, setLockedFor] = useState(null);
  const justLocked = useRef(false);

  useEffect(() => {
    if (lockedFor === null) return undefined;
    if (lockedFor <= 0) {
      // The wait is over: clear the notice so the form is usable again
      // without reloading the application. If the server disagrees — its
      // clock is the one that counts — the next attempt simply locks again.
      setLockedFor(null);
      justLocked.current = false;
      setError(null);
      return undefined;
    }
    const timer = setTimeout(() => setLockedFor((left) => left - 1), 1000);
    return () => clearTimeout(timer);
  }, [lockedFor]);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(location.state?.from?.pathname ?? "/", { replace: true });
    } catch (err) {
      const lockout = lockoutFrom(err);
      if (lockout) {
        // The attempt that trips the lock is announced in the server's own
        // words; every attempt after it counts down instead.
        justLocked.current = lockedFor === null;
        setLockedFor(lockout.seconds);
        setError(justLocked.current ? lockout.detail : null);
      } else {
        setError("Invalid username or password.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const locked = lockedFor !== null && lockedFor > 0;

  return (
    <div className="min-h-screen bg-brand-950 flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-2 text-center">
          <span className="wordmark text-4xl font-extrabold tracking-[0.16em]">
            {HOSPITAL.name}
          </span>
          <p className="text-sm text-brand-200">{HOSPITAL.fullName}</p>
        </div>
        <div className="bg-white rounded-2xl p-7 shadow-xl">
          <h1 className="text-lg font-semibold text-slate-900 mb-1">Sign in</h1>
          <p className="mb-5 text-sm text-slate-600">Hospital Management Information System</p>
          <form onSubmit={handleSubmit} className="space-y-3">
            <input
              className="w-full border rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent"
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
            <input
              type="password"
              className="w-full border rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {locked && (
              <div
                role="alert"
                aria-live="polite"
                className="rounded-md border border-amber-300 bg-amber-50 p-3"
              >
                <p className="text-sm font-semibold text-amber-900">
                  {justLocked.current
                    ? "Too many failed login attempts"
                    : "Login temporarily locked"}
                </p>
                <p className="mt-1 text-sm text-amber-800">
                  {justLocked.current ? error : lockoutMessage(lockedFor)}
                </p>
                {justLocked.current && (
                  <p className="mt-1 text-sm text-amber-800">
                    {lockoutMessage(lockedFor)}
                  </p>
                )}
              </div>
            )}
            {!locked && error && <p className="text-red-500 text-sm">{error}</p>}
            <button
              type="submit"
              disabled={submitting || locked}
              className="w-full bg-brand-600 text-white rounded-full py-2 hover:bg-brand-700 transition disabled:opacity-50"
            >
              {locked ? "Login locked" : submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
