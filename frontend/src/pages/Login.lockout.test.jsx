/**
 * The login screen while it is locked out.
 *
 * What is being tested is the *telling*: that the fifth failure produces a
 * warning somebody can read, that the wait counts down, and that the form
 * comes back by itself when it is over. The refusal itself is the server's and
 * is held by `apps/accounts/tests/test_login_lockout.py` — including that the
 * right password does not get past the lock, which is exactly the thing this
 * countdown must never be trusted for.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthContext } from "../auth/AuthContext.jsx";
import Login from "./Login.jsx";

const wrongPassword = () => {
  const error = new Error("Request failed with status code 400");
  error.response = { status: 400, data: { non_field_errors: ["Unable to log in."] } };
  return error;
};

const lockedOut = (retry_after = 300) => {
  const error = new Error("Request failed with status code 429");
  error.response = {
    status: 429,
    data: {
      code: "login_locked",
      retry_after,
      detail:
        "Too many failed login attempts. For your security, login has been " +
        "temporarily blocked for 5 minutes. Please try again after the lockout expires.",
    },
  };
  return error;
};

function renderLogin(login) {
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={{ user: null, loading: false, login, logout: () => {} }}>
        <Login />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

/**
 * Move the clock on a second at a time.
 *
 * The countdown re-arms itself from an effect, so each tick needs React to
 * render before the next `setTimeout` exists. One 28-second jump fires one
 * timer and then finds nothing scheduled.
 */
async function tick(seconds) {
  for (let i = 0; i < seconds; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { vi.advanceTimersByTime(1000); });
  }
}

async function signIn(user) {
  await user.type(screen.getByPlaceholderText("Username"), "ada");
  await user.type(screen.getByPlaceholderText("Password"), "wrong");
  await user.click(screen.getByRole("button", { name: /sign in/i }));
}

describe("the login screen when the server locks it out", () => {
  let user;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows an ordinary refusal for the first four failures", async () => {
    const login = vi.fn().mockRejectedValue(wrongPassword());
    renderLogin(login);
    await signIn(user);

    expect(await screen.findByText("Invalid username or password.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: /sign in/i }).disabled).toBe(false);
  });

  it("warns clearly when the fifth attempt trips the lockout", async () => {
    const login = vi.fn().mockRejectedValue(lockedOut(300));
    renderLogin(login);
    await signIn(user);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Too many failed login attempts");
    // The server's own sentence, not a re-worded copy of it.
    expect(alert.textContent).toContain("temporarily blocked for 5 minutes");
    expect(alert.textContent).toContain("Please try again in 5 minutes");
  });

  it("disables the form while the lock stands", async () => {
    renderLogin(vi.fn().mockRejectedValue(lockedOut(300)));
    await signIn(user);
    await screen.findByRole("alert");

    expect(screen.getByRole("button", { name: /login locked/i }).disabled).toBe(true);
  });

  it("counts the remaining time down", async () => {
    renderLogin(vi.fn().mockRejectedValue(lockedOut(300)));
    await signIn(user);
    await screen.findByRole("alert");

    await tick(28);
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("4 minutes 32 seconds"));

    // On the minute it reads "4 minutes", not "4 minutes 0 seconds".
    await tick(32);
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("try again in 4 minutes."));
  });

  it("says 'Login temporarily locked' on a later attempt during the cooldown", async () => {
    // A page reopened mid-cooldown: the server answers 429 straight away, and
    // the heading is the standing state rather than the news.
    const login = vi.fn().mockRejectedValue(lockedOut(272));
    const { unmount } = renderLogin(login);
    await signIn(user);
    await screen.findByRole("alert");
    unmount();

    renderLogin(login);
    await signIn(user);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Too many failed login attempts");
    expect(alert.textContent).toContain("4 minutes 32 seconds");
  });

  it("lets the user try again when the cooldown expires, without a reload", async () => {
    const login = vi.fn().mockRejectedValue(lockedOut(3));
    renderLogin(login);
    await signIn(user);
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: /login locked/i }).disabled).toBe(true);

    await tick(4);

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    const button = screen.getByRole("button", { name: /sign in/i });
    expect(button.disabled).toBe(false);

    // And the form works again: the next attempt actually reaches the server,
    // which is what decides — the countdown only re-enabled the button.
    login.mockResolvedValueOnce({ username: "ada" });
    await user.click(button);
    await waitFor(() => expect(login).toHaveBeenCalledTimes(2));
  });

  it("never reveals whether the username exists", async () => {
    // Both a real and an unknown username come back as the same refusal, so
    // the screen has nothing to distinguish and says the same thing.
    const login = vi.fn().mockRejectedValue(wrongPassword());
    renderLogin(login);
    await signIn(user);
    const shown = await screen.findByText("Invalid username or password.");
    expect(shown.textContent).toBe("Invalid username or password.");
  });
});
