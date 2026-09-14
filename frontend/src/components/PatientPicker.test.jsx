import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PatientPicker from "./PatientPicker.jsx";
import api from "../api/client";
import { PATIENT, renderWithApp } from "../test/harness.jsx";

// The dropdown shape of the one patient picker: shut until clicked, then a
// search box over the list the role may browse.
const OTHER = {
  id: 4, uuid: "8b0e5f1e-2222-4000-8000-000000000000", first_name: "Tunde",
  last_name: "Bakare", patient_number: "NMHS-P000013", phone_number: "07059876543",
};

beforeEach(() => {
  vi.spyOn(api, "get").mockImplementation((url, config) => {
    if (url !== "/patients/") return Promise.resolve({ data: [] });
    return Promise.resolve({ data: config?.params?.search ? [OTHER] : [PATIENT, OTHER] });
  });
});
afterEach(() => vi.restoreAllMocks());

const patientRequests = () => api.get.mock.calls.filter(([url]) => url === "/patients/");

function renderDropdown(props = {}) {
  const onChange = vi.fn();
  renderWithApp(
    <PatientPicker variant="dropdown" value={null} onChange={onChange}
                   placeholder="Select a patient" {...props} />,
  );
  return { onChange, trigger: screen.getByRole("button", { name: /select a patient/i }) };
}

async function openList(trigger) {
  await userEvent.click(trigger);
  return within(screen.getByRole("listbox", { name: "Patients" }));
}

describe("the dropdown patient picker", () => {
  it("starts closed, even when focused, and fetches nothing until clicked", () => {
    const { trigger } = renderDropdown({ autoFocus: true });
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(patientRequests()).toHaveLength(0);
  });

  it("opens on click with a search box and the patients to choose from", async () => {
    const { trigger } = renderDropdown();
    const list = await openList(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("combobox", { name: /search patients/i })).toHaveFocus();
    expect(await list.findByRole("option", { name: /Obi, Ada/ })).toBeInTheDocument();
    expect(list.getByRole("option", { name: /Bakare, Tunde/ })).toBeInTheDocument();
  });

  it("draws its list outside the card it sits in, so the card cannot clip it", async () => {
    // `Card` is `overflow-hidden`: a panel positioned inside it was cut off
    // just under the button, and the loaded list could not be seen.
    const onChange = vi.fn();
    renderWithApp(
      <section data-testid="card" className="overflow-hidden">
        <PatientPicker variant="dropdown" value={null} onChange={onChange} placeholder="Select a patient" />
      </section>,
    );
    await userEvent.click(screen.getByRole("button", { name: /select a patient/i }));
    const listbox = screen.getByRole("listbox", { name: "Patients" });
    expect(screen.getByTestId("card")).not.toContainElement(listbox);
    expect(listbox.closest("[style]")).toHaveStyle({ position: "fixed" });

    // Clicking inside the detached panel still counts as inside the picker.
    await userEvent.click(screen.getByRole("combobox", { name: /search patients/i }));
    expect(screen.getByRole("listbox", { name: "Patients" })).toBeInTheDocument();
    await userEvent.click(await within(listbox).findByRole("option", { name: /Obi, Ada/ }));
    expect(onChange).toHaveBeenCalledWith(PATIENT);
  });

  it("chooses a patient from the list and closes", async () => {
    const { trigger, onChange } = renderDropdown();
    const list = await openList(trigger);
    await userEvent.click(await list.findByRole("option", { name: /Obi, Ada/ }));
    expect(onChange).toHaveBeenCalledWith(PATIENT);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("searches the list by what is typed", async () => {
    const { trigger } = renderDropdown();
    const list = await openList(trigger);
    await userEvent.type(screen.getByRole("combobox", { name: /search patients/i }), "0705");
    await waitFor(() => expect(patientRequests().at(-1)[1].params).toMatchObject({ search: "0705" }));
    await waitFor(() => expect(list.queryByRole("option", { name: /Obi, Ada/ })).not.toBeInTheDocument());
    expect(list.getByRole("option", { name: /Bakare, Tunde/ })).toBeInTheDocument();
  });

  it("can be worked from the keyboard", async () => {
    const { trigger, onChange } = renderDropdown();
    const list = await openList(trigger);
    await list.findByRole("option", { name: /Bakare, Tunde/ });
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenCalledWith(OTHER);
  });

  it("closes on Escape and hands focus back to the button", async () => {
    const { trigger } = renderDropdown();
    await openList(trigger);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes when clicked again or when clicking elsewhere, and reopens with an empty search", async () => {
    const { trigger } = renderDropdown();
    await openList(trigger);
    await userEvent.click(trigger);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await openList(trigger);
    await userEvent.type(screen.getByRole("combobox", { name: /search patients/i }), "Bak");
    await userEvent.click(document.body);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await openList(trigger);
    expect(screen.getByRole("combobox", { name: /search patients/i })).toHaveValue("");
  });
});

describe("the default combobox", () => {
  it("is unchanged: a text box that does not open on focus alone", () => {
    renderWithApp(<PatientPicker value={null} onChange={() => {}} autoFocus />);
    const box = screen.getByRole("combobox");
    expect(box).toHaveFocus();
    expect(box).toHaveAttribute("aria-expanded", "false");
    expect(patientRequests()).toHaveLength(0);
  });
});
