// One reading of a DRF error response, so a failed save says what the server
// actually objected to instead of "could not save". DRF answers either
// {"detail": "..."} for a whole-request refusal or {"field": ["..."]} for a
// rejected value, and swallowing the second kind leaves a form that fails
// with nothing on screen to act on.
export function readError(err, fallback = "Could not save. Please try again.") {
  const data = err?.response?.data;
  if (!data) return err?.message ? `${fallback} (${err.message})` : fallback;
  if (typeof data === "string") return data;
  if (data.detail) return data.detail;

  const problems = Object.entries(data)
    .map(([field, messages]) => {
      const text = [].concat(messages).join(" ");
      return field === "non_field_errors" ? text : `${label(field)}: ${text}`;
    })
    .filter(Boolean);
  return problems.length ? problems.join(" · ") : fallback;
}

function label(field) {
  return field.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

/**
 * The rest of what a refusal carries, now that every endpoint answers in one
 * shape (`apps/core/exceptions.py`): `detail` and `code` always, per-field
 * errors alongside them, and `reference` on the rare 500.
 *
 * `readError` above is unchanged and is still what a form calls — it reads the
 * same bodies and joins the field errors into one sentence. These are for the
 * cases it does not cover: branching on a code, quoting a support reference,
 * marking up individual inputs, and saying something useful when the request
 * never arrived at all.
 */

/** The backend's machine-readable token, for a caller that branches on it. */
export function errorCode(error) {
  const code = error?.response?.data?.code;
  return typeof code === "string" ? code : "";
}

/** The support reference a 500 carries, so a person can quote it. */
export function errorReference(error) {
  const reference = error?.response?.data?.reference;
  return typeof reference === "string" ? reference : "";
}

/**
 * A sentence to show, including the case `readError` cannot speak to: a
 * request that never reached the hospital at all, where there is no response
 * to read and "could not save" tells the person nothing about why.
 */
export function errorMessage(error, fallback = "Something went wrong. Please try again.") {
  if (error && !error.response) {
    return "Could not reach the hospital system. Check your connection and try again.";
  }
  return readError(error, fallback);
}

/** Per-field messages, for a form that marks up its own inputs. */
export function fieldErrors(error) {
  const data = error?.response?.data;
  if (!data || typeof data !== "object") return {};
  const fields = {};
  for (const [key, value] of Object.entries(data)) {
    if (["detail", "code", "reference"].includes(key)) continue;
    if (Array.isArray(value) && value.length && typeof value[0] === "string") {
      fields[key] = value[0];
    } else if (typeof value === "string") {
      fields[key] = value;
    }
  }
  return fields;
}
