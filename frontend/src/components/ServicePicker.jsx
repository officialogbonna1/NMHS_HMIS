import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import api from "../api/client";
import { SearchInput } from "./ui.jsx";
import { grouped, matching, priceOf, serviceQuery, totalOf } from "./billableServices";

// Choosing configured services, in the two shapes the hospital needs:
// `ServicePicker` for a desk billing a selection, `ServiceChooser` for a
// clinician ordering examinations. Both read the same `/billable-services/` list
// (`billableServices.js`), so what a doctor can order and what Reception can
// bill are the same rows at the same prices — which is the whole point.
//
// Both are built for a catalogue that is long: a search box, a list that
// scrolls itself rather than the page, no arbitrary cut-off, and controls at
// a size a thumb can hit.

const currency = (n) =>
  new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 })
    .format(n ?? 0);

function useServices(category) {
  // The term is held here and filtered in the browser as well as sent to the
  // server: 60-odd rows filter instantly, and the round trip keeps the list
  // right when the catalogue is bigger than one page of typing.
  const [term, setTerm] = useState("");
  const { data, isLoading } = useQuery(serviceQuery(api, { category, search: term }));
  const services = useMemo(() => matching(data ?? [], term), [data, term]);
  return { term, setTerm, services, all: data ?? [], isLoading };
}

function Price({ service }) {
  return priceOf(service) > 0
    ? <span className="shrink-0 font-semibold text-slate-800">{currency(priceOf(service))}</span>
    : <span className="shrink-0 text-sm text-amber-700">No price set</span>;
}

/**
 * The services a desk is billing, picked from one category — Reception's
 * counter and the chart's billing tab.
 *
 * **Multi-select, because a patient arrives with a list.** A doctor's
 * referral is five tests, so billing them was five trips through a
 * single-select control; each tick is a service, each untick takes it back
 * off, and the running total is the sum of what is ticked. What is submitted
 * is the set of catalogue **keys** — the prices on this screen are for the
 * person reading it, and `/charges/bill-services/` prices the bill again from
 * the catalogue before it raises anything.
 */
export function ServicePicker({ category, selected = [], onToggle, onClear, emptyHint }) {
  const { term, setTerm, services, all, isLoading } = useServices(category);
  const picked = (service) => selected.some((s) => s.key === service.key);

  return (
    <div className="min-w-0">
      <SearchInput
        value={term}
        onChange={setTerm}
        label="Search services"
        placeholder="Search this list — type a few letters of the service…"
      />

      <p className="mt-2 text-sm text-slate-600">
        {isLoading
          ? "Loading the catalogue…"
          : `${services.length} of ${all.length} service${all.length === 1 ? "" : "s"}`}
      </p>

      {/* Scrolls itself, so a long catalogue never pushes the total and the
          settle buttons off the bottom of a phone. */}
      <div className="mt-2 max-h-80 min-w-0 overflow-y-auto rounded-lg border border-slate-200">
        {services.map((service) => {
          const on = picked(service);
          return (
            <label
              key={service.key}
              className={`flex w-full min-w-0 cursor-pointer items-center gap-3 border-b border-slate-100 px-3 py-3 text-sm last:border-b-0 ${
                on ? "bg-brand-50 text-brand-900" : "text-slate-800 hover:bg-slate-50"
              }`}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => onToggle(service)}
                className="h-5 w-5 shrink-0 rounded border-slate-400 text-brand-600 focus:ring-brand-500"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{service.name}</span>
                {service.detail && (
                  <span className="block truncate text-xs text-slate-500">{service.detail}</span>
                )}
              </span>
              <Price service={service} />
            </label>
          );
        })}
        {!isLoading && services.length === 0 && (
          <p className="px-3 py-4 text-sm text-amber-700">
            {term
              ? `Nothing here matches “${term}”.`
              : (emptyHint ?? "Nothing priced under this yet — add it under Billing Catalog.")}
          </p>
        )}
      </div>

      <SelectedServices selected={selected} onToggle={onToggle} onClear={onClear} />
    </div>
  );
}

/**
 * What is going on the bill, kept in front of the person billing it.
 *
 * A ticked row can be miles up a scrolled list, so each one is repeated here
 * as a chip that removes itself — the same "tick, untick, see the total move"
 * the cart at the pharmacy till gives a cashier.
 */
export function SelectedServices({ selected = [], onToggle, onClear }) {
  if (!selected.length) {
    return (
      <p className="mt-3 text-sm text-slate-600">
        Nothing selected yet — tick every service this patient is being billed for.
      </p>
    );
  }
  return (
    <div className="mt-3 rounded-lg border border-brand-200 bg-brand-50/60 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-slate-800">
          {selected.length} selected · Total {currency(totalOf(selected))}
        </p>
        {onClear && (
          <button
            type="button"
            onClick={onClear}
            className="rounded-md px-2 py-1 text-xs font-semibold text-slate-700 underline hover:bg-white"
          >
            Clear all
          </button>
        )}
      </div>
      <ul className="mt-2 flex flex-wrap gap-2">
        {selected.map((service) => (
          <li key={service.key}>
            <button
              type="button"
              onClick={() => onToggle(service)}
              aria-label={`Remove ${service.name}`}
              className="rounded-full bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700"
            >
              {service.name} · {currency(priceOf(service))} ✕
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Several services, chosen by a clinician ordering them — the imaging
 * examinations on a radiology referral, the same way the laboratory's own
 * chooser puts tests on a lab order. Each one raises a charge at the price
 * shown, which is the catalogue's.
 */
export function ServiceChooser({ category, chosen, onToggle, title, blurb }) {
  const { term, setTerm, services, isLoading } = useServices(category);
  const groups = useMemo(() => grouped(services), [services]);
  const picked = (service) => chosen.some((c) => c.key === service.key);

  return (
    <div className="mt-4 min-w-0 rounded-xl border border-slate-200 bg-slate-50/60 p-4">
      <p className="text-sm font-medium text-slate-800">{title}</p>
      <p className="mt-0.5 text-sm text-slate-600">{blurb}</p>

      {chosen.length > 0 && (
        <div className="mt-3">
          <div className="flex flex-wrap gap-2">
            {chosen.map((service) => (
              <button
                key={service.key}
                type="button"
                onClick={() => onToggle(service)}
                className="rounded-full bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white"
              >
                {service.name} ✕
              </button>
            ))}
          </div>
          <p className="mt-2 text-sm font-medium text-slate-800">
            {chosen.length} examination{chosen.length === 1 ? "" : "s"} ·{" "}
            {currency(totalOf(chosen))} will be added to the patient's bill.
          </p>
        </div>
      )}

      <div className="mt-3">
        <SearchInput
          value={term}
          onChange={setTerm}
          label="Search examinations"
          placeholder="Search the catalogue — abdominal, obstetric, Doppler…"
        />
      </div>

      <div className="mt-3 max-h-56 min-w-0 overflow-y-auto pr-1">
        {groups.map(([heading, rows]) => (
          <div key={heading} className="mb-3">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              {heading}
            </p>
            <div className="flex flex-wrap gap-2">
              {rows.map((service) => (
                <button
                  key={service.key}
                  type="button"
                  aria-pressed={picked(service)}
                  onClick={() => onToggle(service)}
                  className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                    picked(service)
                      ? "border-brand-500 bg-brand-50 text-brand-800"
                      : "border-slate-300 bg-white text-slate-800 hover:border-brand-400"
                  }`}
                >
                  {picked(service) ? "✓ " : ""}{service.name}
                  {priceOf(service) > 0 && (
                    <span className={picked(service) ? "text-brand-700" : "text-slate-600"}>
                      {" "}· {currency(priceOf(service))}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>
        ))}
        {!isLoading && services.length === 0 && (
          <p className="text-sm text-amber-700">
            Nothing configured under this yet — an administrator adds it under Billing Catalog.
          </p>
        )}
      </div>
    </div>
  );
}
