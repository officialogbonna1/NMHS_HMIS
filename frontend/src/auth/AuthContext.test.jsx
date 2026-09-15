import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { AuthProvider, useAuth } from "./AuthContext.jsx";

// The query cache belongs to the browser tab, not to the person. Signing out
// and in again must not show the next account the last one's notifications.

function Probe() {
  const { login, logout } = useAuth();
  return (
    <>
      <button type="button" onClick={() => login("eye_a", "secret")}>Sign in</button>
      <button type="button" onClick={() => logout()}>Sign out</button>
    </>
  );
}

function renderWithCache() {
  const client = new QueryClient();
  client.setQueryData(["unread-count"], 6);
  client.setQueryData(["notifications", "mine", "inbox", "all", false, ""],
                      [{ id: 1, title: "REFUND: Nwosu, Amaka" }]);
  render(<QueryClientProvider client={client}><AuthProvider><Probe /></AuthProvider></QueryClientProvider>);
  return client;
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("switching accounts on one browser tab", () => {
  it("drops the previous account's notifications and badge on sign-out", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post").mockResolvedValue({ data: {} });
    const client = renderWithCache();

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(client.getQueryData(["unread-count"])).toBeUndefined());
    expect(client.getQueryData(["notifications", "mine", "inbox", "all", false, ""])).toBeUndefined();
  });

  it("starts the next account with nothing cached from the last", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post").mockResolvedValue({ data: { token: "t", user: { id: 2, role: "ophthalmologist" } } });
    const client = renderWithCache();

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(client.getQueryData(["unread-count"])).toBeUndefined());
    expect(localStorage.getItem("authToken")).toBe("t");
  });
});
