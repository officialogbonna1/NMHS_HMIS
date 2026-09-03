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
