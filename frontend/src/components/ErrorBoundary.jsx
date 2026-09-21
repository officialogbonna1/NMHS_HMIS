import { Component } from "react";

/**
 * The floor under the React tree.
 *
 * A render-time exception in one page used to blank the whole application: the
 * tree unmounts, the screen goes white, and a nurse mid-shift has no way back
 * but to reload and hope. This catches that and leaves the person something
 * they can act on.
 *
 * It is deliberately **not** an error-handling strategy. An API failure is
 * handled where it happens, with a message from `api/errors.js`; this is for
 * the bug nobody predicted. So it says nothing about the exception — no
 * message, no component stack — because that text is for a developer and would
 * mean nothing here. The detail goes to the console, where it is diagnosable.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("Unhandled error in the HMIS interface", error, info);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="min-h-[60vh] flex items-center justify-center p-6">
        <div className="max-w-md rounded-xl border border-slate-200 bg-white p-6 text-center shadow-sm">
          <h1 className="text-lg font-semibold text-slate-900">This screen could not be displayed</h1>
          <p className="mt-2 text-sm text-slate-600">
            Something went wrong while drawing this page. Nothing you have saved
            has been affected — the hospital record is unchanged.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-5 rounded-full bg-brand-600 px-5 py-2 text-sm text-white hover:bg-brand-700"
          >
            Reload the page
          </button>
        </div>
      </div>
    );
  }
}
