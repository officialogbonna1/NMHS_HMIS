// Whether a patient route has been accepted, and whether the Accept control
// should still be offered.
//
// One module, because the same question is asked wherever a route is worked —
// the department stations (`/laboratory`, `/ultrasound`, `/eye`) and the
// vitals station today — and a row that reads "Accept" on one screen while
// another calls it taken is how the same patient gets claimed twice.
//
// **There is no `accepted` flag, and this does not invent one.** The server's
// `accept` action sets `assigned_to` and moves `status` to "in_progress" in a
// single compare-and-set, so the route's own status is already the record of
// whether it was accepted. `can_accept` is the server's own answer to
// "may this reader take it" (`workflow/serializers.py`, reading
// `workflow/access.py` — the same rule `accept` refuses with), so where the
// payload carries it, it is what decides. The local reading is the fallback
// for a row cached before that field existed, and for nothing else.
//
// It is a *courtesy*, never the control: `accept` re-reads the row and answers
// 409 to the loser of a race, so a stale screen loses that argument rather
// than winning it. What this buys is that nobody is offered a button the
// server would only refuse.

/**
 * What the acceptance control should say for this route and this reader.
 *
 * Returns `{ accepted, mine, by, canAccept }`:
 *
 *   accepted  — the work has been taken up and is being done
 *   mine      — this reader is the one holding it
 *   by        — their name, where the payload carries it
 *   canAccept — the Accept control is offered
 *
 * **Accepted is a status, not an assignment.** Reception may *name* a nurse
 * on a hand-off, and a doctor may name a radiologist on a referral: that sets
 * `assigned_to` while `status` stays "queued", and the work has not been
 * taken up yet. `accept` is exactly what moves it to "in_progress", so that
 * is what this reads — calling a named-but-untouched route "Accepted" would
 * tell the unit somebody had picked the patient up when nobody had.
 *
 * The two are independent on purpose: a closed route is neither accepted nor
 * acceptable, so a finished queue keeps the plain row it has always had
 * rather than gaining a badge for a step everybody can see is behind it.
 */
export function acceptanceOf(route, user) {
  const assignedTo = route?.assigned_to ?? null;
  const accepted = route?.status === "in_progress";
  return {
    accepted,
    mine: assignedTo != null && user?.id != null && assignedTo === user.id,
    by: (assignedTo != null && route?.assigned_to_name) || null,
    // Accepted is accepted: the control is never offered on work somebody has
    // already taken up, whatever else the payload says. The fallback matches
    // the server's own `get_can_accept` — unclaimed, and still queued — for a
    // row cached before that field existed.
    canAccept: Boolean(
      !accepted && (route?.can_accept ?? (route?.status === "queued" && assignedTo == null)),
    ),
  };
}
