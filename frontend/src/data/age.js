// Age and date of birth, kept in step.
//
// Only one of the two is ever the real fact. A date of birth gives an exact
// age; an age given verbally gives only an estimated date, and the estimate
// is "born this many <units> before today" — right on the day it is taken,
// drifting slowly afterwards. Anything more precise would be inventing a
// birthday and letting it look authoritative later.
//
// Age carries a unit because "0 years" is how a three-day-old disappears
// from the record, and on a maternity ward that is most of the register.

export const AGE_UNITS = [
  { value: "days", label: "Days", perUnit: 1 },
  { value: "weeks", label: "Weeks", perUnit: 7 },
  { value: "months", label: "Months", perUnit: 30 },
  { value: "years", label: "Years", perUnit: 365 },
];

const DAY = 24 * 60 * 60 * 1000;

function daysBetween(from, to) {
  return Math.floor((to - from) / DAY);
}

// From a date of birth, the largest unit that still says something useful:
// a newborn in days, an infant in weeks then months, a child upward in years.
export function ageFromBirthdate(birthdate) {
  if (!birthdate) return { value: "", unit: "years" };
  const dob = new Date(`${birthdate}T00:00:00`);
  if (Number.isNaN(dob.getTime())) return { value: "", unit: "years" };

  const today = new Date();
  const days = daysBetween(dob, today);
  if (days < 0 || days > 130 * 365) return { value: "", unit: "years" };

  if (days < 14) return { value: String(days), unit: "days" };
  if (days < 60) return { value: String(Math.floor(days / 7)), unit: "weeks" };
  if (days < 730) return { value: String(Math.floor(days / 30)), unit: "months" };

  // Years are counted by calendar, not by dividing days, so a birthday
  // next week does not round the child up early.
  let years = today.getFullYear() - dob.getFullYear();
  const beforeBirthday = today.getMonth() < dob.getMonth()
    || (today.getMonth() === dob.getMonth() && today.getDate() < dob.getDate());
  if (beforeBirthday) years -= 1;
  return { value: String(years), unit: "years" };
}

export function birthdateFromAge(value, unit = "years") {
  const amount = Number(value);
  if (value === "" || Number.isNaN(amount) || amount < 0) return "";

  const today = new Date();
  let estimate;
  if (unit === "years") {
    if (amount > 130) return "";
    estimate = new Date(today.getFullYear() - amount, today.getMonth(), today.getDate());
  } else if (unit === "months") {
    if (amount > 1560) return "";
    estimate = new Date(today.getFullYear(), today.getMonth() - amount, today.getDate());
  } else {
    const days = amount * (unit === "weeks" ? 7 : 1);
    if (days > 130 * 365) return "";
    estimate = new Date(today.getTime() - days * DAY);
  }
  return estimate.toISOString().slice(0, 10);
}

// "3 days" / "7 months" / "42 yrs" — for anywhere the API has not already
// supplied age_display.
export function formatAge(value, unit) {
  if (value === "" || value == null) return "";
  const amount = Number(value);
  if (unit === "years") return `${amount} yrs`;
  const singular = { days: "day", weeks: "week", months: "month" }[unit] ?? unit;
  return `${amount} ${singular}${amount === 1 ? "" : "s"}`;
}

// --- Infants ---
//
// Under a year old, "years" is the wrong box to type into: a three-day-old
// is not 0. The registration form hides the small units behind a tick, so
// the ordinary case is one number and the maternity case is one tick away.

export const MAX_YEARS = 130;

export const INFANT_UNITS = AGE_UNITS.filter((u) => u.value !== "years");

// The largest a unit can go and still be under a year — past this the desk
// meant the next unit up, so the form says so rather than storing "70 weeks".
export const INFANT_MAX = { days: 364, weeks: 52, months: 11 };

export function isInfantUnit(unit) {
  return unit === "days" || unit === "weeks" || unit === "months";
}

// Is this date of birth within the last year? Used to tick the infant box
// for the desk when they enter a date rather than an age.
export function bornWithinTheYear(birthdate) {
  if (!birthdate) return false;
  const dob = new Date(`${birthdate}T00:00:00`);
  if (Number.isNaN(dob.getTime())) return false;
  const days = daysBetween(dob, new Date());
  return days >= 0 && days < 365;
}
