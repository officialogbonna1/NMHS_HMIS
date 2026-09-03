import { createContext, useCallback, useContext, useRef, useState } from "react";

const ToastContext = createContext(null);

const TONE_STYLES = {
  success: { bar: "bg-emerald-500", icon: "text-emerald-600 bg-emerald-50" },
  error: { bar: "bg-red-500", icon: "text-red-600 bg-red-50" },
  info: { bar: "bg-brand-500", icon: "text-brand-600 bg-brand-50" },
};

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback(({ title, message, tone = "success", duration = 4500 }) => {
    const id = ++nextId.current;
    setToasts((current) => [...current, { id, title, message, tone }]);
    if (duration) setTimeout(() => dismiss(id), duration);
    return id;
  }, [dismiss]);

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2 pointer-events-none">
        {toasts.map((t) => (
          <ToastCard key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastCard({ toast, onDismiss }) {
  const tone = TONE_STYLES[toast.tone] ?? TONE_STYLES.success;
  return (
    <div className="pointer-events-auto w-[min(92vw,380px)] overflow-hidden rounded-xl bg-white shadow-lg ring-1 ring-black/5 animate-toast-in">
      <div className="flex items-start gap-3 p-4">
        <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${tone.icon}`}>
          {toast.tone === "error" ? (
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4"><path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm-1-9a1 1 0 112 0v4a1 1 0 11-2 0V9zm1-4a1 1 0 100 2 1 1 0 000-2z" clipRule="evenodd" /></svg>
          ) : (
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4"><path fillRule="evenodd" d="M16.7 5.3a1 1 0 010 1.4l-7.4 7.4a1 1 0 01-1.4 0L3.3 9.5a1 1 0 111.4-1.4l3.6 3.6 6.7-6.7a1 1 0 011.4 0z" clipRule="evenodd" /></svg>
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-slate-900">{toast.title}</p>
          {toast.message && <p className="mt-0.5 text-sm text-slate-500">{toast.message}</p>}
        </div>
        <button onClick={onDismiss} className="shrink-0 rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
          <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4"><path d="M6.7 5.3a1 1 0 00-1.4 1.4L8.6 10l-3.3 3.3a1 1 0 101.4 1.4L10 11.4l3.3 3.3a1 1 0 001.4-1.4L11.4 10l3.3-3.3a1 1 0 00-1.4-1.4L10 8.6 6.7 5.3z" /></svg>
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
