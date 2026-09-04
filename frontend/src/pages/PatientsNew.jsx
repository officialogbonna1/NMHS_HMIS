
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../api/client";
import { PatientCardSheet } from "../components/PrintDocuments.jsx";
import { COUNTRIES, RELATIONSHIPS, statesFor, statesKnownFor } from "../data/geography.js";
import {
  ageFromBirthdate, birthdateFromAge, bornWithinTheYear, formatAge,
  INFANT_UNITS, INFANT_MAX, MAX_YEARS,
} from "../data/age.js";


// Named, so "Register another patient" can clear the form back to it.
const emptyForm = {
  first_name: "",
  middle_name: "",
  last_name: "",
  sex: "",
  birthdate: "",
  age_value: "",
  age_unit: "years",
  email: "",
  phone_number: "",
  short_note: "",
  street_address: "",
  city: "",
  state: "",
  // Almost every patient is local, so this is filled in rather than asked
  // for — a field retyped for each patient gets left blank.
  country: "Nigeria",
  emergency_contact_name: "",
  emergency_contact_relationship: "",
  emergency_contact_phone: "",
  emergency_contact_alt_phone: "",
  emergency_contact_address: "",
};

export default function PatientsNew() {
  const navigate = useNavigate();
  const [registered, setRegistered] = useState(null);
  const [showCard, setShowCard] = useState(false);
  // True when the date of birth was worked back from a stated age, so the
  // form can say so rather than presenting a guess as a fact.
  const [ageIsEstimate, setAgeIsEstimate] = useState(false);
  // Most patients are counted in years, so that is all the form asks for.
  // Ticking this reveals the small units — the maternity case is one tick
  // away instead of cluttering every registration.
  const [isInfant, setIsInfant] = useState(false);
  // Warnings appear once they have tried to save, not while they are typing.
  const [touched, setTouched] = useState(false);

  const [form, setForm] = useState(emptyForm);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Derived from form, so declared after it.
  const TODAY = new Date().toISOString().slice(0, 10);

  // The chosen country's states. `null` once we know it publishes none —
  // the field becomes a text box then — and `undefined` while the world
  // table is still being fetched, which only happens once per session.
  const [statesForCountry, setStatesForCountry] = useState(() => statesKnownFor(form.country));
  useEffect(() => {
    const known = statesKnownFor(form.country);
    setStatesForCountry(known);
    // Nigeria, and anything already fetched, is answered without a wait.
    if (known !== undefined) return undefined;

    let current = true;
    statesFor(form.country).then((states) => { if (current) setStatesForCountry(states); });
    return () => { current = false; };
  }, [form.country]);

  function handleChange(e) {
    const { name, value } = e.target;

    // Only one of birthdate/age is ever the real fact, so entering either
    // fills the other rather than leaving the desk to work it out — and
    // changing country clears a state that belongs to the old one.
    if (name === "birthdate") {
      // The unit follows the date: a three-day-old is shown in days, not
      // rounded to "0 years". A date inside the last year ticks the infant
      // box on the desk's behalf, so the unit they see matches the date.
      const age = ageFromBirthdate(value);
      setForm((current) => ({ ...current, birthdate: value, age_value: age.value, age_unit: age.unit }));
      setIsInfant(bornWithinTheYear(value));
      setAgeIsEstimate(false);
      return;
    }
    if (name === "age_value" || name === "age_unit") {
      setForm((current) => {
        const next = { ...current, [name]: value };
        return { ...next, birthdate: birthdateFromAge(next.age_value, next.age_unit) };
      });
      setAgeIsEstimate(Boolean(name === "age_value" ? value : form.age_value));
      return;
    }
    if (name === "country") {
      setForm((current) => ({ ...current, country: value, state: "" }));
      return;
    }

    setForm((current) => ({
      ...current,
      [name]: value,
    }));
  }

  // Switching between "counted in years" and "counted in days/weeks/months".
  // A date of birth that already agrees with the new mode is kept and the
  // age re-read from it; one that contradicts it is cleared, because the
  // tick is the desk telling us the date was wrong.
  function toggleInfant(checked) {
    setIsInfant(checked);
    const keepsDob = form.birthdate && bornWithinTheYear(form.birthdate) === checked;

    if (keepsDob) {
      const age = ageFromBirthdate(form.birthdate);
      setForm((current) => ({ ...current, age_value: age.value, age_unit: age.unit }));
      setAgeIsEstimate(false);
      return;
    }
    setForm((current) => ({
      ...current,
      birthdate: "",
      age_value: "",
      age_unit: checked ? "months" : "years",
    }));
    setAgeIsEstimate(false);
  }

  async function handleSubmit(e) {
    e.preventDefault();

    setSaving(true);
    setError("");

    try {
      const payload = {
        ...form,

        // Empty optional numeric field should be null
        age_value: form.age_value === "" ? null : Number(form.age_value),

        // Empty optional date should be null
        birthdate: form.birthdate === "" ? null : form.birthdate,
      };

      const { data } = await api.post("/patients/", payload);

      // The card comes before the chart: the file number is what the
      // patient has to leave with, and printing it later means finding
      // them again.
      setRegistered(data);
    } catch (err) {
      console.error("Patient creation failed:", err);

      const responseData = err.response?.data;

      if (responseData) {
        if (typeof responseData === "object") {
          const messages = Object.entries(responseData)
            .map(([field, message]) => {
              const text = Array.isArray(message)
                ? message.join(", ")
                : String(message);

              return `${field}: ${text}`;
            })
            .join("\n");

          setError(messages);
        } else {
          setError(String(responseData));
        }
      } else {
        setError("Unable to create patient. Please try again.");
      }
    } finally {
      setSaving(false);
    }
  }


  if (registered) {
    return (
      <div className="mx-auto w-full max-w-2xl space-y-5 px-4 py-6 sm:px-6 sm:py-8">
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5 sm:p-6">
          <h1 className="text-lg font-semibold text-emerald-900 sm:text-xl">
            {registered.last_name}, {registered.first_name} is registered
          </h1>
          <p className="mt-2 text-sm text-emerald-800">
            File number <strong className="text-base tracking-wider">{registered.file_number}</strong> —
            print the card and give it to them.
          </p>
        </div>

        <div className="grid gap-3 sm:flex sm:flex-wrap">
          <button
            onClick={() => setShowCard(true)}
            className="rounded-lg bg-brand-600 px-6 py-3 font-medium text-white shadow-sm transition hover:bg-brand-700 sm:py-2.5"
          >
            🖨 Print patient card
          </button>
          <button
            onClick={() => navigate(`/patients/${registered.id}`)}
            className="rounded-lg border border-slate-300 px-4 py-3 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 sm:py-2.5"
          >
            Open their chart
          </button>
          <button
            onClick={() => {
              setRegistered(null);
              setShowCard(false);
              setForm(emptyForm);
              setIsInfant(false);
              setAgeIsEstimate(false);
              setTouched(false);
            }}
            className="rounded-lg border border-slate-300 px-4 py-3 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 sm:py-2.5"
          >
            Register another patient
          </button>
        </div>

        {showCard && <PatientCardSheet patient={registered} onClose={() => setShowCard(false)} />}
      </div>
    );
  }

  const missing = [
    !form.first_name.trim() && "First name",
    !form.last_name.trim() && "Last name",
    !form.sex && "Sex",
  ].filter(Boolean);

  const futureBirthdate = form.birthdate && form.birthdate > TODAY;
  const noAgeAtAll = !form.birthdate && !form.age_value;

  // An age past what its unit can hold means the desk reached for the wrong
  // unit — "70 weeks" is a year and a half, not an infant.
  const ageNumber = form.age_value === "" ? null : Number(form.age_value);
  const unitCeiling = isInfant ? INFANT_MAX[form.age_unit] : MAX_YEARS;
  const ageTooBig = ageNumber !== null && ageNumber > unitCeiling;
  const ageOverflowMessage = ageTooBig
    ? (isInfant
      ? `Over ${INFANT_MAX[form.age_unit]} ${form.age_unit} is a year or more — use a bigger unit, or untick "under 1 year".`
      : `${MAX_YEARS} years is the most this accepts.`)
    : null;

  const blocked = missing.length > 0 || futureBirthdate || ageTooBig;

  function submit(e) {
    e.preventDefault();
    setTouched(true);
    if (!blocked) handleSubmit(e);
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 lg:px-8 lg:py-10">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
            Add new patient
          </h1>
          <p className="mt-1 text-sm text-slate-700">
            Fields marked <span className="font-semibold text-red-600">*</span> are required. Everything
            else can be filled in later.
          </p>
        </div>
        {/* Hidden on phones — the sticky bar at the foot already has one. */}
        <button
          type="button"
          onClick={() => navigate("/patients")}
          className="hidden rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 sm:inline-flex"
        >
          Cancel
        </button>
      </header>

      {error && (
        <div className="mb-5 flex gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
          <span className="text-lg leading-none">⚠️</span>
          <div className="min-w-0">
            <p className="font-medium text-red-800">The patient was not saved</p>
            <p className="mt-1 whitespace-pre-line break-words text-sm text-red-700">{error}</p>
          </div>
        </div>
      )}

      <form onSubmit={submit} className="space-y-4 sm:space-y-5">
        <Section step="1" title="Patient name" icon="👤">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="First name" required invalid={touched && !form.first_name.trim()}>
              <input
                name="first_name" value={form.first_name} onChange={handleChange}
                autoComplete="given-name"
                className={input(touched && !form.first_name.trim())} placeholder="First name"
              />
            </Field>
            <Field label="Middle name">
              <input
                name="middle_name" value={form.middle_name} onChange={handleChange}
                autoComplete="additional-name"
                className={input()} placeholder="Middle name"
              />
            </Field>
            <Field
              label="Last name" required invalid={touched && !form.last_name.trim()}
              className="sm:col-span-2 lg:col-span-1"
            >
              <input
                name="last_name" value={form.last_name} onChange={handleChange}
                autoComplete="family-name"
                className={input(touched && !form.last_name.trim())} placeholder="Last name"
              />
            </Field>
          </div>
        </Section>

        <Section
          step="2"
          title="Demographics"
          icon="🗓"
          hint="A date of birth fills in the age, and an age fills in an estimated date."
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Sex" required invalid={touched && !form.sex}>
              <select
                name="sex" value={form.sex} onChange={handleChange}
                className={input(touched && !form.sex)}
              >
                <option value="">Select sex</option>
                <option value="M">Male</option>
                <option value="F">Female</option>
              </select>
            </Field>

            <Field
              label="Date of birth"
              warning={futureBirthdate ? "That date is in the future." : null}
              note={ageIsEstimate && !futureBirthdate ? "Estimated from the age given — replace it if they know the real date." : null}
            >
              <input
                type="date" name="birthdate" value={form.birthdate} onChange={handleChange} max={TODAY}
                className={input(futureBirthdate, ageIsEstimate)}
              />
            </Field>
          </div>

          <AgeFields
            form={form}
            isInfant={isInfant}
            onToggleInfant={toggleInfant}
            onChange={handleChange}
            overflow={ageOverflowMessage}
          />

          {touched && noAgeAtAll && (
            <Warning>
              No date of birth or age. Dosing and a lot of clinical judgement depend on it — add one
              if the patient knows it.
            </Warning>
          )}
        </Section>

        <Section step="3" title="Contact" icon="📞">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Phone number">
              <input
                type="tel" inputMode="tel" name="phone_number" value={form.phone_number}
                onChange={handleChange} autoComplete="tel"
                className={input()} placeholder="e.g. 08134621576"
              />
            </Field>
            <Field label="Email">
              <input
                type="email" inputMode="email" name="email" value={form.email} onChange={handleChange}
                autoComplete="email"
                className={input()} placeholder="patient@example.com"
              />
            </Field>
            <Field label="Street address" className="sm:col-span-2">
              <input
                name="street_address" value={form.street_address} onChange={handleChange}
                autoComplete="street-address"
                className={input()} placeholder="Street address"
              />
            </Field>
            <Field label="City">
              <input
                name="city" value={form.city} onChange={handleChange}
                autoComplete="address-level2"
                className={input()} placeholder="City"
              />
            </Field>
            <Field label="Country">
              <select name="country" value={form.country} onChange={handleChange} className={input()}>
                {COUNTRIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
            <Field
              label="State / Region"
              className="sm:col-span-2"
            >
              {/* Every country that publishes subdivisions has a list, so
                  choosing a country fills this in. The handful with none —
                  and the moment before the list arrives — fall through. */}
              {statesForCountry === undefined ? (
                <select disabled className={input()}>
                  <option>Loading {form.country}…</option>
                </select>
              ) : statesForCountry ? (
                <select name="state" value={form.state} onChange={handleChange} className={input()}>
                  <option value="">Select state…</option>
                  {statesForCountry.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input
                  name="state" value={form.state} onChange={handleChange} className={input()}
                  placeholder={`State or province in ${form.country || "that country"}`}
                />
              )}
            </Field>
          </div>
        </Section>

        <Section step="4" title="Emergency contact" icon="🆘">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Full name">
              <input
                name="emergency_contact_name" value={form.emergency_contact_name}
                onChange={handleChange} className={input()} placeholder="Name of the person to call"
              />
            </Field>
            <Field label="Relationship">
              <input
                name="emergency_contact_relationship" value={form.emergency_contact_relationship}
                onChange={handleChange} className={input()} placeholder="e.g. Spouse, Mother"
                list="relationships"
              />
              <datalist id="relationships">
                {RELATIONSHIPS.map((o) => <option key={o} value={o} />)}
              </datalist>
            </Field>
            <Field label="Phone number">
              <input
                type="tel" inputMode="tel" name="emergency_contact_phone"
                value={form.emergency_contact_phone}
                onChange={handleChange} className={input()} placeholder="Phone number"
              />
            </Field>
            <Field label="Another number">
              <input
                type="tel" inputMode="tel" name="emergency_contact_alt_phone"
                value={form.emergency_contact_alt_phone}
                onChange={handleChange} className={input()} placeholder="If the first does not answer"
              />
            </Field>
            <Field label="Address" className="sm:col-span-2">
              <input
                name="emergency_contact_address" value={form.emergency_contact_address}
                onChange={handleChange} className={input()}
                placeholder="Where they can be found, if different from the patient's"
              />
            </Field>
          </div>
          {touched && form.emergency_contact_name && !form.emergency_contact_phone && (
            <Warning>
              {form.emergency_contact_name} has no phone number — the contact cannot be reached
              without one.
            </Warning>
          )}
        </Section>

        <Section step="5" title="Notes" icon="📝">
          <Field label="Short note">
            <textarea
              name="short_note" value={form.short_note} onChange={handleChange} rows={3}
              className={input()} placeholder="Anything the desk should know about this patient"
            />
          </Field>
        </Section>

        {/* Stays in view on a long form, so Create is never a scroll away. */}
        <div className="sticky bottom-0 -mx-4 border-t border-slate-200 bg-white/95 px-4 py-3 shadow-[0_-4px_16px_-8px_rgba(15,23,42,0.25)] backdrop-blur sm:-mx-1 sm:rounded-xl sm:border sm:px-4 sm:py-4 sm:shadow-lg">
          {touched && missing.length > 0 && (
            <p className="mb-3 text-sm font-medium text-red-700">
              Still needed: {missing.join(", ")}.
            </p>
          )}
          <div className="flex flex-col-reverse gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
            <button
              type="button"
              onClick={() => navigate("/patients")}
              className="w-full rounded-lg border border-slate-300 px-5 py-3 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 sm:w-auto sm:py-2.5"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="w-full rounded-lg bg-brand-600 px-6 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto sm:py-2.5"
            >
              {saving ? "Creating patient…" : "Create patient"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

/**
 * Age, asked for the way the desk thinks about it.
 *
 * Almost everybody is counted in years, so that is the only box on screen.
 * A baby is not "0" — ticking "under 1 year" swaps the years box for a
 * number plus days / weeks / months, which is what `age_unit` was added for.
 */
function AgeFields({ form, isInfant, onToggleInfant, onChange, overflow }) {
  const preview = form.age_value === "" ? null : formatAge(form.age_value, form.age_unit);

  return (
    <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-700">Age</span>
          {preview && (
            <span className="rounded-full bg-brand-50 px-2.5 py-0.5 text-xs font-semibold text-brand-700 ring-1 ring-inset ring-brand-200">
              {preview} old
            </span>
          )}
        </div>

        {/* The tick that reveals the small units. Whole row is the target,
            so it works on a phone as well as with a mouse. */}
        <label className="inline-flex cursor-pointer select-none items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400">
          <input
            type="checkbox"
            checked={isInfant}
            onChange={(e) => onToggleInfant(e.target.checked)}
            className="h-4 w-4 rounded border-slate-400 text-brand-600 focus:ring-brand-500"
          />
          Under 1 year old
        </label>
      </div>

      {isInfant ? (
        <div className="mt-3">
          <div className="flex gap-2">
            <input
              type="number" inputMode="numeric" name="age_value" value={form.age_value}
              onChange={onChange} min="0" max={INFANT_MAX[form.age_unit]}
              className={`${input(Boolean(overflow))} min-w-0 flex-1`}
              placeholder="How many"
              aria-label={`Age in ${form.age_unit}`}
            />
            {/* Segmented rather than a dropdown: three options, and on a
                phone a tap beats a picker. */}
            <div
              role="group" aria-label="Age unit"
              className="flex shrink-0 rounded-lg border border-slate-300 bg-white p-1"
            >
              {INFANT_UNITS.map((u) => {
                const active = form.age_unit === u.value;
                return (
                  <button
                    key={u.value}
                    type="button"
                    aria-pressed={active}
                    onClick={() => onChange({ target: { name: "age_unit", value: u.value } })}
                    className={`rounded-md px-2.5 py-1.5 text-xs font-semibold transition sm:px-3 sm:text-sm ${
                      active
                        ? "bg-brand-600 text-white shadow-sm"
                        : "text-slate-600 hover:bg-slate-100 hover:text-slate-800"
                    }`}
                  >
                    {u.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-3">
          <div className="relative">
            <input
              type="number" inputMode="numeric" name="age_value" value={form.age_value}
              onChange={onChange} min="0" max={MAX_YEARS}
              className={`${input(Boolean(overflow))} pr-16`}
              placeholder="Age in years"
              aria-label="Age in years"
            />
            <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-slate-500">
              years
            </span>
          </div>
          <p className="mt-2 text-sm text-slate-600">
            Under a year old? Tick the box above to record days, weeks or months.
          </p>
        </div>
      )}

      {overflow && <p className="mt-2 text-sm font-medium text-red-600">{overflow}</p>}
    </div>
  );
}

function Section({ step, title, icon, hint, children }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
      <div className="mb-4 flex items-start gap-3 border-b border-slate-100 pb-3">
        <span
          aria-hidden="true"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-base"
        >
          {icon}
        </span>
        <div className="min-w-0">
          <h2 className="font-semibold text-slate-900">
            {step && <span className="mr-2 text-sm font-normal text-slate-500">Step {step}</span>}
            {title}
          </h2>
          {hint && <p className="mt-0.5 text-sm text-slate-700">{hint}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

function Field({ label, required, invalid, warning, note, className = "", children }) {
  return (
    <div className={`min-w-0 ${className}`}>
      <label className="mb-1 block text-sm font-medium text-slate-700">
        {label}{required && <span className="text-red-600"> *</span>}
      </label>
      {children}
      {invalid && <p className="mt-1 text-sm text-red-600">{label} is required.</p>}
      {warning && <p className="mt-1 text-sm text-red-600">{warning}</p>}
      {note && <p className="mt-1 text-sm text-amber-700">{note}</p>}
    </div>
  );
}

function Warning({ children }) {
  return (
    <p className="mt-4 flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      <span aria-hidden="true">⚠️</span>
      <span>{children}</span>
    </p>
  );
}

// One input style, so every box on the form looks and focuses the same.
// `text-base` on phones is deliberate: anything smaller makes iOS Safari
// zoom the page in on focus and leave it there.
function input(invalid = false, estimated = false) {
  const base = "w-full rounded-lg border bg-white px-3 py-2.5 text-base text-slate-900 outline-none transition placeholder:text-slate-400 sm:py-2 sm:text-sm";
  if (invalid) return `${base} border-red-400 bg-red-50 focus:border-red-500 focus:ring-2 focus:ring-red-100`;
  if (estimated) return `${base} border-amber-400 bg-amber-50 focus:border-amber-500 focus:ring-2 focus:ring-amber-100`;
  return `${base} border-slate-300 focus:border-brand-500 focus:ring-2 focus:ring-brand-100`;
}
