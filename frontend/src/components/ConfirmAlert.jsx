import { createContext, useCallback, useContext, useRef, useState } from "react";
import { Button, Modal } from "./ui.jsx";

/**
 * The confirmation alert — "are you sure?", asked properly.
 *
 * Six places in the application asked this with `window.confirm()`: cancelling
 * a visit, removing a health-record entry, deleting a configuration row,
 * writing off expired stock, and — the one this was built for — adding a
 * laboratory panel, where the question has to list the tests and what each of
 * them will cost the patient. A browser confirm renders that as a wall of
 * monospaced plain text with a bullet list made of `\n`, cannot show a figure
 * as a figure, cannot be styled, and blocks the whole thread while it is open.
 *
 * This asks the same question, takes the same decision, and runs the same code
 * on "yes" — it only changes how the question looks. It is built on the
 * existing `Modal` (focus trap, Escape to close, body-scroll lock, bottom
 * sheet on a phone), so there is no second dialog implementation here.
 *
 * `useConfirm()` returns an `ask()` that resolves to true or false, which is
 * what lets a call site read almost exactly as the `confirm()` it replaced:
 *
 *     if (await ask({ title: "Remove this?", tone: "danger" })) remove();
 */

const ConfirmContext = createContext(null);

const TONES = {
  danger: {
    accent: "bg-red-50 text-red-700", confirmVariant: "danger",
    icon: <path fillRule="evenodd" d="M8.3 2.8a2 2 0 013.4 0l6.3 10.9A2 2 0 0116.3 17H3.7a2 2 0 01-1.7-3.3L8.3 2.8zM10 7a1 1 0 00-1 1v3a1 1 0 102 0V8a1 1 0 00-1-1zm0 7.5a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />,
    label: "Warning",
  },
  info: {
    accent: "bg-brand-50 text-brand-700", confirmVariant: "primary",
    icon: <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-11a1 1 0 11-2 0 1 1 0 012 0zm-2 3a1 1 0 112 0v4a1 1 0 11-2 0v-4z" clipRule="evenodd" />,
    label: "Information",
  },
};

export function ConfirmProvider({ children }) {
  const [request, setRequest] = useState(null);
  const resolver = useRef(null);

  /**
   * Ask, and resolve with what they chose.
   *
   * `{ title, message, details, total, confirmLabel, cancelLabel, tone }`.
   * `details` is `[{ label, value }]` — a line per thing being agreed to, with
   * its figure beside it, which is the part a `confirm()` string could never
   * render. Nothing here is ever hard-coded by this module: every label and
   * value comes from the caller's own data.
   */
  const ask = useCallback((options) => new Promise((resolve) => {
    resolver.current = resolve;
    setRequest({ tone: "danger", confirmLabel: "Confirm", cancelLabel: "Cancel", ...options });
  }), []);

  const settle = useCallback((answer) => {
    // Resolving before clearing, so a caller awaiting this is not left hanging
    // if the render that follows throws.
    resolver.current?.(answer);
    resolver.current = null;
    setRequest(null);
  }, []);

  return (
    <ConfirmContext.Provider value={{ ask }}>
      {children}
      {request && (
        <Modal
          open
          // Escape, the close button and a click outside all mean "no", which
          // is what dismissing a browser confirm does.
          onClose={() => settle(false)}
          title={request.title}
          description={request.description}
          size="sm"
          footer={
            <>
              <Button variant="secondary" onClick={() => settle(false)}>
                {request.cancelLabel}
              </Button>
              <Button
                variant={(TONES[request.tone] ?? TONES.danger).confirmVariant}
                onClick={() => settle(true)}
                autoFocus
              >
                {request.confirmLabel}
              </Button>
            </>
          }
        >
          <ConfirmBody request={request} />
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}

function ConfirmBody({ request }) {
  const tone = TONES[request.tone] ?? TONES.danger;
  return (
    <div className="flex items-start gap-3">
      <span className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full ${tone.accent}`}>
        <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5" aria-hidden="true">
          {tone.icon}
        </svg>
        <span className="sr-only">{tone.label}</span>
      </span>

      <div className="min-w-0 flex-1">
        {request.message && (
          <p className="break-words text-sm text-slate-700">{request.message}</p>
        )}

        {/* What is being agreed to, line by line. A long test name wraps; the
            figure beside it stays on one line and lines up with the others. */}
        {request.details?.length > 0 && (
          <dl className="mt-3 max-h-56 divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-200">
            {request.details.map((row, index) => (
              <div
                key={`${row.label}-${index}`}
                className="flex items-baseline justify-between gap-3 px-3 py-2"
              >
                <dt className="min-w-0 break-words text-sm text-slate-800">{row.label}</dt>
                {row.value != null && (
                  <dd className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">
                    {row.value}
                  </dd>
                )}
              </div>
            ))}
          </dl>
        )}

        {request.total && (
          <p className="mt-3 flex items-baseline justify-between gap-3 rounded-lg bg-amber-50 px-3 py-2">
            <span className="text-sm text-amber-900">{request.total.label}</span>
            <span className="shrink-0 text-base font-bold tabular-nums text-amber-900">
              {request.total.value}
            </span>
          </p>
        )}
      </div>
    </div>
  );
}

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used inside ConfirmProvider");
  return ctx;
}
