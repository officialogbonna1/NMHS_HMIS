/**
 * What the login page knows about a lockout.
 *
 * A pure module with no React in it, the pattern `refundPolicy.js` set: the
 * decisions are testable without rendering anything, and the page below is
 * left with markup.
 *
 * **None of this enforces anything.** The server refuses a locked client
 * before it so much as reads the password (`accounts/lockout.py`), and it is
 * the only thing that decides. Everything here exists so somebody staring at a
 * form that has stopped working is told why and for how long, instead of being
 * left to guess. A countdown that reached zero early would simply produce
 * another 429.
 */

/** Seconds left, read from the server's own 429 rather than timed locally. */
export function lockoutFrom(error) {
  const data = error?.response?.data;
  if (error?.response?.status !== 429 || data?.code !== "login_locked") return null;
  const seconds = Number(data.retry_after);
  return {
    seconds: Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : 0,
    detail: data.detail || "",
  };
}

/**
 * "4 minutes 32 seconds" — spoken the way somebody waiting would say it.
 *
 * Seconds alone ("272 seconds") makes a person do arithmetic to find out
 * whether to wait or walk away; minutes alone rounds a nearly-over lock up to
 * a whole minute and reads as longer than it is.
 */
export function formatRemaining(seconds) {
  const left = Math.max(0, Math.ceil(seconds || 0));
  const minutes = Math.floor(left / 60);
  const rest = left % 60;
  const parts = [];
  if (minutes) parts.push(`${minutes} minute${minutes === 1 ? "" : "s"}`);
  if (rest || !minutes) parts.push(`${rest} second${rest === 1 ? "" : "s"}`);
  return parts.join(" ");
}

/** The sentence under the heading while the lock stands. */
export function lockoutMessage(seconds) {
  return `Too many failed login attempts. Please try again in ${formatRemaining(seconds)}.`;
}
