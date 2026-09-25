import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "../api/client";
import { renderWithApp } from "../test/harness.jsx";
import {
  AmendRecordForm, AmendedBadge, AmendmentHistory,
} from "./MaternityAmendment.jsx";

// Correcting a maternity record, on screen.
//
// The record reads as a record until Amend is pressed; a correction needs a
// reason off the server's own list; and what the record used to say stays
// visible rather than being replaced by what it says now.
//
// None of this is the protection. The last block says so with a test: the
// server refuses, and the form surfaces the refusal instead of pretending.

const MIDWIFE = { id: 5, role: "maternity_nurse" };

const PREGNANCY = { id: 7, lmp: "2026-07-09", edd: "2027-04-15", is_amended: true };

const AMENDMENTS = [
  { id: 2, created_at: "2026-09-24T10:15:00Z", amended_by_name: "Grace Nwosu",
    reason: "wrong_value", reason_label: "Wrong value recorded",
    detail: "Dating scan corrected it.",
    previous: { lmp: "2026-07-02" },
    changes: [{ field: "lmp", label: "LMP", from: "2026-07-02", to: "2026-07-09" }] },
  { id: 1, created_at: "2026-09-20T08:00:00Z", amended_by_name: "Ada Okafor",
    reason: "typo", reason_label: "Typing or data-entry error", detail: "",
    previous: { gravida: 1 },
    changes: [{ field: "gravida", label: "Gravida", from: 1, to: 2 }] },
];

const REASONS = [
  { value: "typo", label: "Typing or data-entry error" },
  { value: "wrong_value", label: "Wrong value recorded" },
];

const FIELDS = [
  { name: "lmp", label: "LMP", type: "date" },
  { name: "edd", label: "EDD", type: "date" },
];

function mockApi({ amendments = AMENDMENTS } = {}) {
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url.endsWith("/amendments/")) return Promise.resolve({ data: amendments });
    if (url.endsWith("/amendment-reasons/")) return Promise.resolve({ data: REASONS });
    return Promise.resolve({ data: [] });
  });
}

afterEach(() => vi.restoreAllMocks());

describe("the amended badge", () => {
  it("marks a record that was corrected", () => {
    renderWithApp(<AmendedBadge record={PREGNANCY} />, { user: MIDWIFE });
    expect(screen.getByText("Amended")).toBeInTheDocument();
  });

  it("says nothing about one that was not", () => {
    renderWithApp(<AmendedBadge record={{ is_amended: false }} />, { user: MIDWIFE });
    expect(screen.queryByText("Amended")).not.toBeInTheDocument();
  });
});

describe("the amendment history", () => {
  const render = (props = {}) =>
    renderWithApp(<AmendmentHistory endpoint="pregnancies" recordId={7} {...props} />,
                  { user: MIDWIFE });

  it("shows what the record used to say, not only what it says now", async () => {
    mockApi();
    render();

    const history = await screen.findByRole("region", { name: "Amendment history" });
    expect(within(history).getByText("2026-07-02")).toBeInTheDocument();
    expect(within(history).getByText("2026-07-09")).toBeInTheDocument();
  });

  it("keeps the original struck through rather than removing it", async () => {
    mockApi();
    render();

    const original = await screen.findByText("2026-07-02");
    expect(original.className).toMatch(/line-through/);
  });

  it("names who corrected it, when, and why", async () => {
    mockApi();
    render();

    const history = await screen.findByRole("region", { name: "Amendment history" });
    expect(within(history).getByText("Wrong value recorded")).toBeInTheDocument();
    expect(within(history).getByText("Dating scan corrected it.")).toBeInTheDocument();
    expect(within(history).getByText(/Grace Nwosu/)).toBeInTheDocument();
  });

  it("shows every correction, newest first", async () => {
    mockApi();
    render();

    const history = await screen.findByRole("region", { name: "Amendment history" });
    // The outer list only — each entry holds its own list of changes.
    const entries = within(history).getAllByRole("list")[0].children;
    expect(entries).toHaveLength(2);
    expect(entries[0]).toHaveTextContent("Wrong value recorded");
  });

  it("says plainly when a record has never been corrected", async () => {
    mockApi({ amendments: [] });
    render();

    expect(await screen.findByText("This record has not been corrected."))
      .toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Amendment history" }))
      .not.toBeInTheDocument();
  });
});

describe("correcting a record", () => {
  const render = (props = {}) =>
    renderWithApp(
      <AmendRecordForm endpoint="pregnancies" record={PREGNANCY} fields={FIELDS}
                       {...props} />,
      { user: MIDWIFE });

  it("offers the amendable fields and nothing else", async () => {
    mockApi();
    render();

    const form = await screen.findByRole("form", { name: "Amend record" });
    expect(within(form).getByLabelText("LMP")).toBeInTheDocument();
    expect(within(form).getByLabelText("EDD")).toBeInTheDocument();
    // The identity facts are not on the form at all.
    expect(within(form).queryByLabelText(/Patient/i)).not.toBeInTheDocument();
  });

  it("says that the identity facts cannot be changed", async () => {
    mockApi();
    render();

    expect(await screen.findByText(/cannot be changed/i)).toBeInTheDocument();
  });

  it("will not save without a reason", async () => {
    mockApi();
    render();

    const save = await screen.findByRole("button", { name: "Save correction" });
    expect(save).toBeDisabled();
  });

  it("offers the reasons the server gave it, never a hard-coded list", async () => {
    mockApi();
    render();

    const chooser = await screen.findByLabelText(/Why is this being corrected/i);
    await waitFor(() => expect(chooser.options.length).toBe(REASONS.length + 1));
    expect([...chooser.options].map((o) => o.textContent)).toEqual(
      ["Choose a reason…", "Typing or data-entry error", "Wrong value recorded"]);
  });

  it("sends the correction with its reason", async () => {
    const user = userEvent.setup();
    mockApi();
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ data: PREGNANCY });
    render();

    const chooser = await screen.findByLabelText(/Why is this being corrected/i);
    await waitFor(() => expect(chooser.options.length).toBeGreaterThan(1));
    await user.selectOptions(chooser, "wrong_value");
    await user.type(screen.getByLabelText(/Anything else/i), "Dating scan.");
    await user.click(screen.getByRole("button", { name: "Save correction" }));

    await waitFor(() => expect(patch).toHaveBeenCalled());
    const [url, body] = patch.mock.calls[0];
    expect(url).toBe("/pregnancies/7/");
    expect(body.amendment_reason).toBe("wrong_value");
    expect(body.amendment_detail).toBe("Dating scan.");
  });
});

describe("the form is not the protection", () => {
  it("surfaces the server's refusal of a protected field", async () => {
    const user = userEvent.setup();
    mockApi();
    vi.spyOn(api, "patch").mockRejectedValue({
      response: { status: 400, data: {
        detail: "These are part of what identifies this record and cannot be changed: Patient.",
        code: "immutable_field", fields: ["patient"],
      } },
    });
    renderWithApp(
      <AmendRecordForm endpoint="pregnancies" record={PREGNANCY} fields={FIELDS} />,
      { user: MIDWIFE });

    const chooser = await screen.findByLabelText(/Why is this being corrected/i);
    await waitFor(() => expect(chooser.options.length).toBeGreaterThan(1));
    await user.selectOptions(chooser, "typo");
    await user.click(screen.getByRole("button", { name: "Save correction" }));

    expect(await screen.findByText("That cannot be changed")).toBeInTheDocument();
    expect(screen.getByText(/cannot be changed: Patient/)).toBeInTheDocument();
  });

  it("surfaces a refusal to amend somebody else's record", async () => {
    const user = userEvent.setup();
    mockApi();
    vi.spyOn(api, "patch").mockRejectedValue({
      response: { status: 403, data: {
        detail: "You can only correct a record you entered. Ask an administrator." } },
    });
    renderWithApp(
      <AmendRecordForm endpoint="pregnancies" record={PREGNANCY} fields={FIELDS} />,
      { user: MIDWIFE });

    const chooser = await screen.findByLabelText(/Why is this being corrected/i);
    await waitFor(() => expect(chooser.options.length).toBeGreaterThan(1));
    await user.selectOptions(chooser, "typo");
    await user.click(screen.getByRole("button", { name: "Save correction" }));

    expect(await screen.findByText(/You can only correct a record you entered/))
      .toBeInTheDocument();
  });
});
