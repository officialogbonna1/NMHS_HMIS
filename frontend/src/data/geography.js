// Countries and their states, provinces and regions.
//
// Picking any country fills the State field with that country's own list, so
// nobody has to type theirs from memory. Nigeria's list is hand-kept here —
// it is the one this hospital actually uses, and it spells the capital
// territory the way the desk says it ("FCT — Abuja"), which is what is
// already stored against existing patients.
//
// Everywhere else lives in `worldStates.js`, generated from ISO 3166-2 data.
// That file is 100KB of subdivision names, so it is **imported on demand**
// by `statesFor()` rather than statically: almost every patient is Nigerian,
// and the doctor reading a chart should not be carrying the provinces of
// Kazakhstan around to do it.

export const NIGERIAN_STATES = [
  "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno",
  "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "FCT — Abuja", "Gombe",
  "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos",
  "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto",
  "Taraba", "Yobe", "Zamfara",
];

// Cached after the first non-Nigerian country is chosen, so switching
// between countries afterwards is instant.
let worldStates = null;

/**
 * What can be answered without fetching anything: Nigeria always, everywhere
 * else once the world table has been pulled in. `undefined` means "not known
 * yet, ask `statesFor`" — `null` means the country publishes none. Lets the
 * field render its list on the first paint instead of flashing "Loading".
 */
export function statesKnownFor(country) {
  if (!country) return null;
  if (country === "Nigeria") return NIGERIAN_STATES;
  if (!worldStates) return undefined;
  return worldStates[country] ?? null;
}

/**
 * The subdivisions of a country, or `null` where it publishes none (the
 * caller falls back to a free-text box). Async because everywhere outside
 * Nigeria is fetched as its own chunk the first time it is needed.
 */
export async function statesFor(country) {
  if (!country) return null;
  if (country === "Nigeria") return NIGERIAN_STATES;
  if (!worldStates) ({ WORLD_STATES: worldStates } = await import("./worldStates.js"));
  return worldStates[country] ?? null;
}

// Nigeria and its neighbours first — that is who walks through the door —
// then the rest alphabetically.
export const COUNTRIES = [
  "Nigeria", "Benin", "Cameroon", "Chad", "Ghana", "Niger", "Togo",
  "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda",
  "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain",
  "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Bhutan", "Bolivia",
  "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso",
  "Burundi", "Cambodia", "Canada", "Cape Verde", "Central African Republic", "Chile",
  "China", "Colombia", "Comoros", "Congo (Brazzaville)", "Congo (Kinshasa)", "Costa Rica",
  "Côte d'Ivoire", "Croatia", "Cuba", "Cyprus", "Czechia", "Denmark", "Djibouti",
  "Dominica", "Dominican Republic", "Ecuador", "Egypt", "El Salvador",
  "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia", "Fiji", "Finland",
  "France", "Gabon", "Gambia", "Georgia", "Germany", "Greece", "Grenada", "Guatemala",
  "Guinea", "Guinea-Bissau", "Guyana", "Haiti", "Honduras", "Hungary", "Iceland",
  "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy", "Jamaica",
  "Japan", "Jordan", "Kazakhstan", "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan", "Laos",
  "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania",
  "Luxembourg", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta",
  "Mauritania", "Mauritius", "Mexico", "Moldova", "Monaco", "Mongolia", "Montenegro",
  "Morocco", "Mozambique", "Myanmar", "Namibia", "Nepal", "Netherlands", "New Zealand",
  "Nicaragua", "North Korea", "North Macedonia", "Norway", "Oman", "Pakistan", "Panama",
  "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar",
  "Romania", "Russia", "Rwanda", "Samoa", "San Marino", "São Tomé and Príncipe",
  "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore",
  "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea",
  "South Sudan", "Spain", "Sri Lanka", "St Kitts and Nevis", "St Lucia",
  "St Vincent and the Grenadines", "Sudan", "Suriname", "Sweden", "Switzerland",
  "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Tonga",
  "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Tuvalu", "Uganda",
  "Ukraine", "United Arab Emirates", "United Kingdom", "United States", "Uruguay",
  "Uzbekistan", "Vanuatu", "Vatican City", "Venezuela", "Vietnam", "Yemen", "Zambia",
  "Zimbabwe",
];

export const RELATIONSHIPS = [
  "Spouse", "Mother", "Father", "Son", "Daughter", "Brother", "Sister",
  "Grandmother", "Grandfather", "Aunt", "Uncle", "Cousin", "Guardian",
  "Friend", "Neighbour", "Employer", "Other",
];
