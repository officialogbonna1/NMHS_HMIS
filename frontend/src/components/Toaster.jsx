import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

/**
 * The application's alerts — the message that answers an action.
 *
 * One mechanism, used everywhere: `useToast().showToast({…})`. This is the
 * hospital's alert presentation and the only one; a page that needs to tell
 * somebody something calls this rather than reaching for `window.alert`, which
 * cannot be styled, cannot be read by a screen reader politely, blocks the
 * thread, and looks like a browser rather than a hospital system.
 *
 * **Not the notification system.** `core.services.notify`, the bell, the
 * notification centre and the emails are a separate thing entirely: those are
 * a record of something that happened, addressed to whoever can act on it,
 * kept until it is read. An alert here is the immediate answer to the thing
 * the person in front of the screen just did, and it is gone in five seconds.
 *
 * Five tones, each with its own icon and colour — and each also carrying its
 * own **shape** of icon, because colour alone is not a signal somebody with a
 * colour vision deficiency can read.
 */

const ToastContext = createContext(null);

//: Tone → the treatment, the icon, and how assistively it is announced.
//:
//: `role="alert"` interrupts a screen reader and is right for a failure or a
//: warning; `role="status"` waits for a pause and is right for a success or a
//: note, which is the difference between "this did not work" and "saved".
const TONES = {
  success: {
    bar: "bg-emerald-500", icon: "text-emerald-700 bg-emerald-50", ring: "ring-emerald-600/10",
    role: "status", live: "polite", label: "Success",
  },
  error: {
    bar: "bg-red-500", icon: "text-red-700 bg-red-50", ring: "ring-red-600/10",
    role: "alert", live: "assertive", label: "Error",
  },
  warning: {
    bar: "bg-amber-500", icon: "text-amber-800 bg-amber-50", ring: "ring-amber-600/10",
    role: "alert", live: "assertive", label: "Warning",
  },
  info: {
    bar: "bg-brand-500", icon: "text-brand-700 bg-brand-50", ring: "ring-brand-600/10",
    role: "status", live: "polite", label: "Information",
  },
};

function ToneIcon({ tone }) {
  const paths = {
    // A tick, a cross, a triangle and an "i" — four different silhouettes, so
    // the tone is legible without reading the colour.
    success: <path fillRule="evenodd" d="M16.7 5.3a1 1 0 010 1.4l-7.4 7.4a1 1 0 01-1.4 0L3.3 9.5a1 1 0 111.4-1.4l3.6 3.6 6.7-6.7a1 1 0 011.4 0z" clipRule="evenodd" />,
    error: <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM7.7 6.3a1 1 0 00-1.4 1.4L8.6 10l-2.3 2.3a1 1 0 101.4 1.4L10 11.4l2.3 2.3a1 1 0 001.4-1.4L11.4 10l2.3-2.3a1 1 0 00-1.4-1.4L10 8.6 7.7 6.3z" clipRule="evenodd" />,
    warning: <path fillRule="evenodd" d="M8.3 2.8a2 2 0 013.4 0l6.3 10.9A2 2 0 0116.3 17H3.7a2 2 0 01-1.7-3.3L8.3 2.8zM10 7a1 1 0 00-1 1v3a1 1 0 102 0V8a1 1 0 00-1-1zm0 7.5a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />,
    info: <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-11a1 1 0 11-2 0 1 1 0 012 0zm-2 3a1 1 0 112 0v4a1 1 0 11-2 0v-4z" clipRule="evenodd" />,
  };
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4" aria-hidden="true">
      {paths[tone] ?? paths.success}
    </svg>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(0);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  /**
   * Raise an alert.
   *
   * `{ title, message, tone, duration, details }` — `details` is an optional
   * list of `{ label, value }` rows for an alert that has to carry a figure as
   * well as a sentence: a test and what it costs, a drug and how many. Every
   * existing caller passes title/message/tone and is unaffected.
   */
  const showToast = useCallback((options) => {
    const { title, message, tone = "success", duration = 4500, details } = options ?? {};
    const id = ++nextId.current;
    setToasts((current) => [...current, { id, title, message, tone, details }]);
    if (duration) {
      timers.current.set(id, setTimeout(() => dismiss(id), duration));
    }
    return id;
  }, [dismiss]);

  // Nothing is left ticking against an unmounted provider.
  useEffect(() => () => {
    timers.current.forEach((timer) => clearTimeout(timer));
    timers.current.clear();
  }, []);

  return (
    <ToastContext.Provider value={{ showToast, dismiss }}>
      {children}
      {/* Above the modal layer (z-50) so an alert raised by a dialog is not
          hidden behind it. `pointer-events-none` on the stack and `auto` on
          each card keeps the page clickable around them. */}
      <div
        className="pointer-events-none fixed inset-x-0 top-4 z-[100] flex flex-col items-center gap-2 px-3 sm:px-4"
        aria-live="polite"
        aria-relevant="additions"
      >
        {toasts.map((toast) => (
          <ToastCard key={toast.id} toast={toast} onDismiss={() => dismiss(toast.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastCard({ toast, onDismiss }) {
  const tone = TONES[toast.tone] ?? TONES.success;
  return (
    <div
      role={tone.role}
      aria-live={tone.live}
      className={`animate-toast-in pointer-events-auto w-full max-w-[420px] overflow-hidden rounded-xl bg-white shadow-lg ring-1 ${tone.ring}`}
    >
      <div className="flex items-start gap-3 p-4">
        <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${tone.icon}`}>
          <ToneIcon tone={toast.tone} />
          {/* The tone in words, for a reader that cannot see the icon. */}
          <span className="sr-only">{tone.label}</span>
        </span>

        <div className="min-w-0 flex-1">
          {/* `break-words` so a long test or product name wraps inside the
              card instead of widening it off the side of a phone. */}
          <p className="break-words text-sm font-semibold text-slate-900">{toast.title}</p>
          {toast.message && (
            <p className="mt-0.5 break-words text-sm text-slate-600">{toast.message}</p>
          )}

          {/* The figures, where an alert has to carry them — a laboratory test
              and its price, say. A definition list rather than a sentence, so
              the value is findable at a glance and reads correctly aloud. */}
          {toast.details?.length > 0 && (
            <dl className="mt-2 divide-y divide-slate-100 rounded-lg bg-slate-50 px-3 py-1">
              {toast.details.map((row, index) => (
                <div key={`${row.label}-${index}`} className="flex items-baseline justify-between gap-3 py-1.5">
                  <dt className="min-w-0 break-words text-sm text-slate-700">{row.label}</dt>
                  <dd className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>

        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss alert"
          className="-m-1 shrink-0 rounded-md p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4" aria-hidden="true">
            <path d="M6.7 5.3a1 1 0 00-1.4 1.4L8.6 10l-3.3 3.3a1 1 0 101.4 1.4L10 11.4l3.3 3.3a1 1 0 001.4-1.4L11.4 10l3.3-3.3a1 1 0 00-1.4-1.4L10 8.6 6.7 5.3z" />
          </svg>
        </button>
      </div>
      <div className={`h-1 ${tone.bar}`} />
    </div>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}
