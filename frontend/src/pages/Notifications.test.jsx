import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Notifications from "./Notifications.jsx";
import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";

// Notifications are archived, never deleted: read / unread, archive one,
// archive a selection, archive everything — and an Archived tab to read them
// back and restore one.
const DOCTOR = { id: 5, username: "dera", role: "doctor" };

const note = (over = {}) => ({
  id: 1, recipient: 5, recipient_name: "dera", recipient_role: "doctor",
  title: "Laboratory result: FBC", message: "", category: "clinical",
  is_read: false, is_archived: false, archived_at: null, action_url: "",
  created_at: "2026-09-01T10:00:00Z",
  ...over,
});

let inbox;
let archive;

const lastParams = () => {
  const calls = api.get.mock.calls.filter(([url]) => url === "/notifications/");
  return calls[calls.length - 1]?.[1]?.params;
};

const rowOf = (title) => within(screen.getByText(title).closest("li"));

beforeEach(() => {
  inbox = [note(), note({ id: 2, title: "Referral accepted", category: "routing", is_read: true })];
  archive = [note({ id: 3, title: "Vitals recorded", is_archived: true,
                    archived_at: "2026-09-02T09:00:00Z" })];
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url === "/notifications/") {
      return Promise.resolve({ data: config?.params?.archived ? archive : inbox });
    }
    return Promise.resolve({ data: {} });
  });
  vi.spyOn(api, "post").mockResolvedValue({ data: { archived: 2 } });
  vi.spyOn(api, "patch").mockResolvedValue({ data: {} });
});
afterEach(() => vi.restoreAllMocks());

describe("the inbox", () => {
  it("asks for the inbox only and offers no delete", async () => {
    renderWithApp(<Notifications />, { user: DOCTOR });
    expect(await screen.findByText("Laboratory result: FBC")).toBeInTheDocument();
    expect(lastParams()).not.toHaveProperty("archived");
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
  });

  it("marks one read and another unread", async () => {
    renderWithApp(<Notifications />, { user: DOCTOR });
    await screen.findByText("Laboratory result: FBC");

    await userEvent.click(rowOf("Laboratory result: FBC").getByRole("button", { name: "Mark read" }));
    expect(api.patch).toHaveBeenCalledWith("/notifications/1/", { is_read: true });

    await userEvent.click(rowOf("Referral accepted").getByRole("button", { name: "Mark unread" }));
    expect(api.patch).toHaveBeenCalledWith("/notifications/2/", { is_read: false });
  });

  it("archives one", async () => {
    renderWithApp(<Notifications />, { user: DOCTOR });
    await screen.findByText("Laboratory result: FBC");
    await userEvent.click(rowOf("Laboratory result: FBC").getByRole("button", { name: "Archive" }));
    expect(api.post).toHaveBeenCalledWith("/notifications/1/archive/");
  });

  it("archives the selected ones together", async () => {
    renderWithApp(<Notifications />, { user: DOCTOR });
    await screen.findByText("Laboratory result: FBC");
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Laboratory result: FBC" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Referral accepted" }));
    await userEvent.click(screen.getByRole("button", { name: "Archive selected (2)" }));
    expect(api.post).toHaveBeenCalledWith("/notifications/archive_selected/", { ids: [1, 2] });
  });

  it("archives everything only after confirming", async () => {
    renderWithApp(<Notifications />, { user: DOCTOR });
    await screen.findByText("Laboratory result: FBC");
    await userEvent.click(screen.getByRole("button", { name: "Archive all" }));
    expect(api.post).not.toHaveBeenCalled();

    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/Nothing is deleted/)).toBeInTheDocument();
    await userEvent.click(dialog.getByRole("button", { name: "Archive all" }));
    expect(api.post).toHaveBeenCalledWith("/notifications/archive_all/");
  });
});

describe("the Archived tab", () => {
  it("reads the archive back and restores one, with no selection and no delete", async () => {
    renderWithApp(<Notifications />, { user: DOCTOR });
    await screen.findByText("Laboratory result: FBC");
    await userEvent.click(screen.getByRole("button", { name: "Archived" }));

    expect(await screen.findByText("Vitals recorded")).toBeInTheDocument();
    expect(lastParams()).toMatchObject({ archived: true });
    expect(screen.queryByText("Laboratory result: FBC")).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /select/i })).toBeNull();
    expect(screen.queryByRole("button", { name: "Archive all" })).toBeNull();
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();

    await userEvent.click(rowOf("Vitals recorded").getByRole("button", { name: "Restore to inbox" }));
    expect(api.post).toHaveBeenCalledWith("/notifications/3/unarchive/");
  });
});
