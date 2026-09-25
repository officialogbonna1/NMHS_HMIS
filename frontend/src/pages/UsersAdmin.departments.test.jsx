import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import UsersAdmin from "./UsersAdmin.jsx";

// **Posting somebody to a second department, from the Users page.**
//
// The relation is `Department.staff` — the one Django admin edits — so this is
// a second front door onto one model, not a second way of granting access. The
// API is what decides: `UserViewSet` is `IsAdmin()` on every write, and the
// field is absent from the payload an account reads about itself.

const ADMIN = { id: 1, role: "admin" };

const DEPARTMENTS = [
  { id: 1, code: "general-medicine", name: "General Medicine", is_active: true },
  { id: 2, code: "maternity", name: "Maternity", is_active: true },
  { id: 3, code: "laboratory", name: "Laboratory", is_active: true },
];

const DARA = {
  id: 7, username: "doctor_test", first_name: "Dara", last_name: "", email: "",
  role: "doctor", department: "", staff_number: "NMHS-S000007", is_active: true,
  must_change_password: false,
  authorized_departments: [1],
  authorized_department_names: ["General Medicine"],
};

function mockApi({ users = [DARA] } = {}) {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/departments/") return Promise.resolve({ data: DEPARTMENTS });
    if (url === "/users/") return Promise.resolve({ data: users });
    return Promise.resolve({ data: [] });
  });
}

const ticked = (name) => screen.getByRole("checkbox", { name });

afterEach(() => vi.restoreAllMocks());

describe("the authorized departments control", () => {
  it("offers every active department as a tick box", async () => {
    mockApi();
    renderWithApp(<UsersAdmin />, { user: ADMIN });

    // The fieldset renders before the departments land, so wait for a tick
    // box rather than for the box it sits in.
    await screen.findByRole("checkbox", { name: "General Medicine" });
    const box = screen.getByRole("group", { name: /Authorized departments/i });
    for (const name of ["General Medicine", "Maternity", "Laboratory"]) {
      expect(within(box).getByRole("checkbox", { name })).toBeInTheDocument();
    }
  });

  it("says that a posting is not a role", async () => {
    mockApi();
    renderWithApp(<UsersAdmin />, { user: ADMIN });

    const box = await screen.findByRole("group", { name: /Authorized departments/i });
    expect(within(box).getByText(/a doctor authorised for Maternity is a doctor in Maternity/i))
      .toBeInTheDocument();
  });

  it("distinguishes the primary department from the authorisation", async () => {
    mockApi();
    renderWithApp(<UsersAdmin />, { user: ADMIN });

    expect(await screen.findByText(/It is not an authorisation/i)).toBeInTheDocument();
  });

  it("shows where each person already works, on their row", async () => {
    mockApi();
    renderWithApp(<UsersAdmin />, { user: ADMIN });

    // Scoped to the row: the name is also a tick box in the form above.
    const row = (await screen.findByText("Dara")).closest("div");
    expect(within(row).getByText("General Medicine")).toBeInTheDocument();
  });
});

describe("posting Dr Dara to a second department", () => {
  async function editDara(user) {
    mockApi();
    renderWithApp(<UsersAdmin />, { user: ADMIN });
    await user.click(await screen.findByRole("button", { name: /^Edit/ }));
  }

  it("loads the departments she already has", async () => {
    const user = userEvent.setup();
    await editDara(user);

    await waitFor(() => expect(ticked("General Medicine")).toBeChecked());
    expect(ticked("Maternity")).not.toBeChecked();
  });

  it("sends both once Maternity is ticked", async () => {
    const user = userEvent.setup();
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ data: {} });
    await editDara(user);

    await waitFor(() => expect(ticked("General Medicine")).toBeChecked());
    await user.click(ticked("Maternity"));
    await user.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() => expect(patch).toHaveBeenCalled());
    const [url, body] = patch.mock.calls[0];
    expect(url).toBe("/users/7/");
    expect(body.authorized_departments.sort()).toEqual([1, 2]);
  });

  it("sends the remaining one when a department is unticked", async () => {
    const user = userEvent.setup();
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ data: {} });
    await editDara(user);

    await waitFor(() => expect(ticked("General Medicine")).toBeChecked());
    await user.click(ticked("Maternity"));
    await user.click(ticked("General Medicine"));
    await user.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls[0][1].authorized_departments).toEqual([2]);
  });

  it("never sends a password it was not given", async () => {
    const user = userEvent.setup();
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ data: {} });
    await editDara(user);

    await waitFor(() => expect(ticked("General Medicine")).toBeChecked());
    await user.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls[0][1]).not.toHaveProperty("password");
  });
});

describe("what the page does not decide", () => {
  it("is only a form — the server is what accepts or refuses the posting", async () => {
    const user = userEvent.setup();
    const patch = vi.spyOn(api, "patch")
      .mockRejectedValue({ response: { status: 403, data: { detail: "Forbidden" } } });
    mockApi();
    renderWithApp(<UsersAdmin />, { user: ADMIN });
    await user.click(await screen.findByRole("button", { name: /^Edit/ }));

    await waitFor(() => expect(ticked("General Medicine")).toBeChecked());
    await user.click(ticked("Maternity"));
    await user.click(screen.getByRole("button", { name: /Save changes/ }));

    // The refusal is surfaced; the form does not pretend it worked.
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(await screen.findByText(/Could not save this user/i)).toBeInTheDocument();
  });
});
