# NMHS — Hospital Management Information System

Instructions for Claude Code working in this repo. Read this before making
changes so new code stays consistent with decisions already made.

## What this is

An HMIS for **NMHS**, built for ~50 concurrent users. Backend: Django + DRF.
Frontend: React + Vite + TanStack Query + Tailwind. UI/UX is modeled on the
PocketPatientMD reference manual (patient tiles, health-record sections,
note archiving) — see "UI reference" below — plus our own additions:
role-based locking, pharmacy stock deduction, and AI agents via the
Anthropic API.

## Project structure

```
backend/hmis/
  manage.py
  hmis/                    # settings, root urls, celery config
  apps/
    accounts/               # custom User model, roles, token auth
    patients/                 # Patient + 9 health-record tiles
    clinical/                   # Vitals & ConsultationNote (lock-after-save)
    inventory/                    # Item, Batch (expiry/FEFO), StockMovement
    pharmacy/                       # Prescription + atomic dispense service
    sales/                             # Sale, SaleItem, discounts
    appointments/                        # Calendar
    ai_agents/                             # Claude API integration
    core/                                    # LockedRecordMixin, TimeStampedModel

frontend/src/
  auth/          # AuthContext, RequireAuth
  pages/         # PatientsList, PatientDetail, PrescribeDrug, InventoryDashboard, Login
  components/    # HealthRecordTile, AddAllergyModal (template for other tiles)
  api/client.js  # axios instance, token auth, 401 -> /login redirect
```

## UI reference: PocketPatientMD manual

The patient list, patient profile, and 9-tile health record (Allergies,
Medications, Medical Conditions, Medical Devices, Surgical History, Family
Medical History, Social History, Vaccinations, Medical Tests & Diagnostics)
all follow that manual's layout and interaction pattern:
- Alphabetical patient list, live search, letter-jump nav
- Each tile: title + "Add" button + list rows + trash icon per row
- Clicking a tile item opens an edit panel (not yet built for most tiles —
  `AddAllergyModal.jsx` is the reference implementation to copy)
- Medical notes are timestamped, most-recent-first, and archive a full
  snapshot on every edit (see `ConsultationNoteAmendment` — wire this up
  when building the note-edit flow)
- Vitals have a history graph per vital sign, plotted by year (not yet built
  on the frontend — backend model already supports it via `Vitals` rows)

Match this manual's interaction patterns by default. Deviate only when the
person explicitly asks for something different.

## Non-negotiable design rules (do not silently change these)

1. **Roles**: admin, doctor, nurse, reception, pharmacist. Defined in
   `accounts.Role`. Admin can do everything; other roles are scoped per
   `accounts/permissions.py`.
2. **Lock-after-save**: `Vitals` and `ConsultationNote` use
   `core.mixins.LockedRecordMixin`. Once saved, only a caller passing
   `admin_override=True` may update them — this must never be bypassed to
   "make a feature easier." Corrections go through an Amendment record
   (see `ConsultationNoteAmendment`), never an in-place overwrite.
3. **Doctors edit only their own notes** before lock; they can *view* any
   doctor's notes. Enforced in `ConsultationNoteViewSet.perform_update`.
4. **Reception records lock immediately after save** — same pattern as
   Vitals; if you add a reception-intake model, mix in
   `LockedRecordMixin` too.
5. **Appointments are a queue, not a diary** — reception picks patient +
   doctor and nothing else. `start_time`/`end_time` are read-only in the
   serializer and stamped by the doctor's own `start`/`end` transitions, so
   they record what happened rather than what was booked. Don't reintroduce
   a time picker for reception.
6. **Prescribing and dispensing are two steps, and both live in
   `pharmacy/services.py`** — never create a `Prescription` or write to
   `Batch.quantity` from a view. A doctor's `create_prescription()` only
   queues the request (status `pending`, no stock touched); a pharmacist's
   `dispense_prescription()` is what deducts FEFO, writes the
   `StockMovement` audit rows and raises the patient charge, all inside one
   `transaction.atomic` with `select_for_update`.
   `create_prescriptions()` writes a whole script — several drugs for one
   patient — inside one transaction, so a stock refusal on the third drug
   cannot leave the first two queued and the rest lost.
   `create_prescription_and_dispense()` remains as the both-at-once wrapper.
   If you need a new way to move stock, add a function to
   `pharmacy/services.py` or `inventory/services.py` — don't route around
   them.
7. **Doctors never see raw stock counts** — the `/items/` endpoint returns
   `available: true/false` for doctors (see `ItemForPrescribingSerializer`),
   not quantities. Keep this distinction if you touch the drug picker.
8. **Batches are FEFO** (first-expiry-first-out) — `Batch` ordering and the
   dispense service both rely on `expiry_date` ascending. Don't reorder
   this without updating both.
9. **One definition of "assigned patient"** — `patients/access.py`
   (`doctor_patient_q` / `nurse_patient_q`). Patient identity, the health
   record tiles, notes, Vitals and nursing notes all filter through it, so a
   doctor who can open a patient can always read that patient's vitals. Add
   new ways to assign a patient there, not in an individual viewset.
10. **The overview is the full chart** — `/patients/<id>/overview/`
   (`patients/overview.py`) returns notes, vitals, prescriptions and
   appointments in one payload, so it is gated on `ClinicalRecordAccess`
   (doctors + admin), *not* the broader read permission the rest of the
   patient viewset uses. Reception and nurses must never reach it. The
   billing block is only added for roles that already read money.
11. **Nurses own vitals and nursing notes** — both lock on save
   (`LockedRecordMixin`), both are created only by nurses, and a nurse reads
   back only their own. There is no edit path by design: a correction is a
   new record. `NursingNoteViewSet` is POST/GET only.
12. **A charge's balance is `amount − amount_paid − amount_discounted`** —
   money is settled by a `Payment` (allocated across open charges
   oldest-first, `billing.services.allocate_to_charges`) or written off by a
   percentage discount (`apply_percentage_discount`). Both maintain the
   charge's own figures as well as the ledger, and both go through
   `billing/services.py` — never write `amount_paid`, `amount_discounted` or
   `status` from a view, or charges drift back to reading "unpaid" forever
   while the ledger says otherwise. A discount is capped at what is still
   owed, and waiving credits only the remainder, so no path can push a
   patient into credit.
13. **Discounts are approvals, not data entry** — `POST
   /charges/<id>/discount/` (one charge) and `POST
   /charges/discount-balance/` (everything a patient owes) take a percentage
   plus a mandatory reason, and record an `Adjustment` naming the approver.
   They sit with the roles that already waive (cashier, accountant, admin),
   *not* reception — same boundary as rule 14.
14. **A route's `purpose` decides who it reaches, not department
   membership** — `workflow.views.PURPOSE_ROLE` maps vitals→nurse,
   consultation/procedure→doctor, laboratory→laboratory,
   ultrasound→radiology, eye→optometrist *and* ophthalmologist. Values are
   **lists**: a purpose worked by either of two roles must name both, or the
   patient strands when one of them is off. `WORKING_ROLES` (every role in
   that map) is what the queue's list/start/complete permissions and
   `/queue`'s route guard in `main.jsx` are built from — extend the map and
   all four follow. Unassigned work
   is shown and notified to every active user in that role, so a queue never
   silently strands a patient because nobody added staff to a Department.
   `department.staff` / `user.department` remain the fallback only for
   purposes with no role of their own. Change `work_routes_for` and
   `route_targets` together — they must agree, or somebody is notified about
   work they cannot see.
15. **Reception queues work; it never marks work done** — appointments and
   patient routes both move through their viewset actions only (`status` is
   read-only on both serializers). Reception can raise a route and *cancel*
   it (the patient left, or it was a mistake); saying the work started or
   finished belongs to the clinician it was sent to.
16. **The pharmacy takes its own money** — a pharmacist can read ledgers and
   charges and record a `Payment` (stamped `channel="pharmacy"` from their
   role, never from the request body), but can never waive, void or adjust.
   See `COLLECTING_ROLES` in `billing/views.py`.
17. **AI agents are advisory only** (`ai_agents/services.py`) — they must
    never auto-block or auto-approve a clinical or stock decision. A human
   (doctor/pharmacist/admin) always makes the final call. When wiring a new
   agent in, keep that boundary.

## Django admin

Every app has an `admin.py` and every model is registered — `Patients` is
its own section, separate from `Users`, because a patient is somebody the
hospital treats and a user is staff who log in. `apps/core/tests/
test_admin_registration.py` fails if a model is added without an admin, if
any changelist 500s, or if `manage.py check` reports an admin problem.

Two rules the admin classes follow, and new ones should:

1. **Anything a service owns is read-only there.** Vitals and notes lock on
   save; charges, payments and the ledger are kept in step by
   `billing/services.py`; `StockMovement` is an audit row. Editing those
   through the admin either 403s on the model's own `save()` or moves one
   number without the other, so those admins set
   `has_change_permission = False` and say why.
2. **Passwords go through Django's own form.** `HMISUserAdmin` never exposes
   the `password` column for editing (typing a plain string in stores an
   unusable hash); each row carries a **Set password** link, and `role` is on
   the add form because an account without one can log in and reach nothing.

## Conventions

- **A notification nobody sees is not a notification.** The shell polls
  `GET /notifications/unread-count/` every 30s and shows the number on a
  bell in the header and on the sidebar row; reading one invalidates
  `["unread-count"]` so the badge clears without waiting for the poll.
  Categories are toned on the list (`clinical` red, `routing` amber).
- **Doctors are told when a reading lands on a chart they hold** —
  `VitalsViewSet.perform_create` → `_tell_the_doctors`, which uses
  `patients.access.doctors_for_patient()`: the mirror of `doctor_patient_q`,
  narrowed to *right now* (open visit's attending doctor, an active route,
  an open appointment). Finished work is history and must not keep pinging.
  Add a new way a doctor holds a patient to both functions together.

- **Every `notify()` needs an `action_url` the frontend can actually route.**
  A link to a page that does not exist is a dead end for whoever receives it.
  `apps/core/tests/test_notification_links.py` holds the route list and fails
  naming the notification when a page is renamed out from under one — keep
  that list in step with `frontend/src/main.jsx`. Unmatched addresses hit the
  catch-all `NotFound` page inside the shell, never a blank screen.

- Backend: DRF ModelViewSets + routers, one router per app in `urls.py`,
  included under `/api/` in the root `hmis/urls.py`.
- Frontend: TanStack Query for all server state (no manual `useEffect`
  fetching), Tailwind utility classes only (no CSS files besides
  `index.css`'s Tailwind directives), one page per route in `src/pages/`.
- **Text contrast**: no `text-gray-*` anywhere — the rungs are `slate-500`
  for labels and meta, `slate-600` for supporting copy, `slate-700` for
  instructions, `slate-800`/`900` for content. `slate-400` is reserved for
  genuinely de-emphasised things (a struck-through cancelled row, a hover
  icon, a zero count). Clinical content is never `text-xs`.
- **Choosing a patient is always `PatientPicker.jsx`** — a combobox that
  opens as a browsable list and filters as you type, over the same
  `/patients/` the role is scoped to, so what you can scroll to and what you
  can find are the same set. Never hand-roll another search-box-plus-dropdown:
  five pages each had one, and each behaved slightly differently. Its
  `patientLabel()` is the one place a patient's name is formatted
  ("Last, First"). `PatientsList.jsx` keeps a plain filter box instead — it
  is a list being narrowed, not a value being chosen.
- New health-record tiles: copy `AddAllergyModal.jsx`'s structure, wire the
  new modal into `PatientDetail.jsx`'s `TileLoader` the same way Allergies
  is wired (see the `hasModal` branch — extend it per tile as you add
  modals, don't rewrite the loader).

## Setup / running locally

See `README.md` for install steps. Needs Postgres + Redis running (or
adjust `.env`). `ANTHROPIC_API_KEY` required only for the `ai_agents` app.

**After any model change, run `python manage.py migrate` — not just
`makemigrations`.** The dev server runs against `backend/hmis/db.sqlite3`,
so an unapplied migration doesn't fail loudly: the screen that first touches
the new column 500s and the UI just says it couldn't load. A green test run
proves nothing here, since tests build their own database from scratch.

## Current status / what's not built yet

Done:
- Auth (login/me/logout), route guards by role
- "Add New Patient" full page form (`PatientsNew.jsx`)
- All 9 health-record tiles have working Add modals via
  `GenericTileModal.jsx` + `tileConfig.js` — one config-driven modal
  instead of 9 bespoke files. Add a tile by adding a config entry.
- **Medical Tests & Diagnostics** is the ninth tile: a result is a document
  (image, PDF, doc, spreadsheet — `type: "file"` in the config, sent as
  multipart) *or* a typed finding in `impressions`, or both, never neither.
  `MedicalTest.file` is optional and `MedicalTestSerializer.validate`
  enforces the pair; `config.validate` says the same in the modal before the
  round trip. Editing the findings omits the untouched file field so the
  stored document survives the PATCH. Uploaded files are served from
  `/media/` (root-relative, proxied in `vite.config.js`).
- Medical Notes tab: list, add, edit-own-before-lock, and admin
  amendments now actually create `ConsultationNoteAmendment` snapshots
  server-side (see `ConsultationNoteViewSet.perform_update`) and are
  shown in the note editor.
- Vitals tab: nurses add a reading **and the note that goes with it** —
  `NursingNoteForm.jsx` is shared with the vitals station, so a repeat
  reading taken from the chart is recorded the same way as one taken at the
  station. No edit, by design: both lock immediately after save. Doctors get
  the same tab read-only for their own patients. Every figure the entry form
  captures is shown, the most recent reading is badged, and the list asks
  for `page_size` so an older reading is never silently off page one.
- Nurse hand-off chain: reception routes for vitals (to a named nurse or to
  anyone) → every eligible nurse is notified and sees it as **Unclaimed** →
  one **accepts**, which claims it and drops it from the others' queues →
  vitals + nursing note → **Send to doctor**, which closes the nurse's route,
  opens a consultation route in that doctor's name, makes them attending
  (this is what opens the chart) and notifies them. Sending with nothing
  recorded is a **warning, not a wall**: both hand-off paths answer
  `{"code": "no_vitals"}` the first time, and the nurse can repeat the call
  with `acknowledge_no_vitals` — a patient can be too distressed for a
  reading. When they do, "No vitals recorded for this visit." goes onto the
  route, into the doctor's notification title and into the audit row, so
  nobody opens a chart and discovers it. **Mark done** on a vitals route
  stays blocked outright: that is a claim the work happened, not a judgement
  call about what the doctor needs.
- **Each referral unit is a department in its own right** — `/laboratory`,
  `/ultrasound`, `/eye`, all one `DepartmentStation.jsx` driven by the
  `STATIONS` config, the way nursing works from `/vitals`. Queue → accept →
  do the work → write the finding → **Save & mark done**. Admin roles see
  all three in the nav.
- **A referral answers back.** `PatientRoute.result` / `result_by` /
  `result_at` are written by `POST /patient-routes/<id>/record-result/` or
  by passing `result` to `complete/` — never by PATCHing the row, so the
  finding is always stamped with who wrote it (they are `read_only` on the
  serializer). Saving notifies the doctor who raised the referral and the
  result appears on their chart under that visit. Referral notifications
  link to the unit's own station (`PURPOSE_STATION`), not the generic queue.
- **Refer Patient** (`/refer`, `ReferPatient.jsx`) — the doctor's hand-off,
  mirroring nursing's: lab, ultrasound, eye clinic or procedure, via
  `POST /patient-routes/refer/`. The consultation **stays open** (a referral
  is work done during it, not the end of it) and referring raises **no
  charge** — the counter bills the service from the Billing Catalog, so
  money is only created by the roles that collect it. The receiving unit
  accepts / starts / completes from `/queue`.
- **Billing Catalog** now covers laboratory, ultrasound, eye and procedure
  as well as cards and consultation fees. `BILLING_CATEGORIES` in
  `BillingItemsAdmin.jsx` drives both that page and the billing counter's
  tabs, so a priced category always has somewhere to bill it from. Cashiers
  and accountants can price it, not just admin — they are the ones who find
  out a service has no price with a patient at the window.
- **Send to Doctor** (`/send-to-doctor`, `SendToConsultation.jsx`) is the
  same hand-off as its own nav item for nurses, for the patient whose vitals
  route they already closed: pick the patient (their current queue, or a
  search of the patients routed to them), pick the doctor, priority and a
  handover note. Both paths run through `_queue_consultation` in
  `workflow/views.py` — keep them there so the two cannot drift. The doctor
  is notified ("Queued for consultation"), lands on the chart, and starts /
  finishes the consultation from **My Queue**.
- **A patient is moved, not blocked.** `send-to-doctor` answers
  `{"code": "reassign", "current_doctor": …}` when somebody else already
  holds the patient; acknowledging with `acknowledge_reassign` **cancels**
  the stale consultation (cancelled, not completed — that doctor never saw
  them), opens the new one, moves `attending_doctor` so the chart follows,
  and tells the doctor who lost the patient. This is the answer to "the
  doctor who saw them last is off". Sending to the *same* doctor twice is
  still a 409. Each warning is acknowledged by its own field name, so
  waving past "no vitals" is not consent to reassign.
- The vitals station has two tabs: **My queue** (live work) and **Recorded
  today** — the nurse's own day, from `GET /vitals/recorded-today/`: each
  reading with its figures, the nursing note written alongside it, and the
  doctor the patient went on to. `sent_to` is the first consultation route
  raised *after* the reading (the hand-off it caused); `with_doctor` is
  whoever was already holding the patient when it was taken, so a re-check
  reads "Already with Dr X" rather than "not sent to a doctor yet". Only
  when both are empty has nobody picked the patient up. The dashboard's two "today" cards link to
  `/vitals?tab=today` rather than the queue, which by then has emptied of
  the patients they count.
- Nurses get their dashboard at `/` like everyone else
  (`DashboardView._nurse_cards`): waiting for vitals, in progress, vitals
  and notes recorded today, unread — every card linking to `/vitals`, plus
  an alert when someone has waited over 30 minutes. `/vitals`
  (`VitalsStation.jsx`) is the *working* page, not a second dashboard: a
  summary line, a searchable queue sorted by priority then wait time, and
  the station itself — open a patient → record vitals and a nursing note →
  Start / Mark done on the route. Reception routes patients here with purpose "Vitals"
  (`PatientRoute.purpose`), optionally naming the nurse. `/nursing`
  redirects here for notifications raised before the rename.
- The `/dashboard/` cards carry a stable `key` as well as a label; the
  Vitals station reads its day totals by key rather than counting a
  paginated list client-side. Nurses get their own card set
  (`DashboardView._nurse_cards`), all linking to `/vitals`, plus an alert
  when someone has waited over 30 minutes.
- **The cash desk's dashboard is money, not a queue**
  (`DashboardView._cash_desk_cards`) — taken today, cash today, taken at
  this desk (`received_by=user`, so a shift reconciles), part-paid and
  unpaid counts, outstanding balance, and discounted / waived / refunded
  today, each opening `/billing` or `/transactions`. A cashier is never
  routed a patient, so the generic "My queue" card was always zero. They
  also get an alert for charges raised in the last two hours that are still
  unpaid, and a `billing` notification whenever anyone else raises a charge
  (`billing.views._tell_the_cash_desk`) — the front desk bills a patient and
  walks them over.
- **A card opens the page it counts.** Doctors get **My patients** →
  `/patients` (counted with `patients.access.patient_queryset_for`, the same
  rule the Patients page filters by, so the number and the list agree), then
  **My queue** → `/queue`, then unread and seen-today. `/queue` is only
  routable for reception, doctors and nurses, so other roles keep
  `/patients` — a card must never link somewhere the role's guard bounces.
- Doctor's patient Overview tab (`PatientOverview.jsx`) — the default tab
  for doctors: allergy alerts, latest vitals, nursing + consultation notes,
  prescriptions, appointments and routing, all from the single
  `/patients/<id>/overview/` call. All nine health-record tiles travel with
  their contents, not just a count — allergies (with reactions), conditions,
  medications, surgeries, vaccinations, devices, tests (with a link to the
  uploaded file), family and social history. `counts` and the lists are
  built together in `overview.py`; a tile counted there must have a list
  beside it (`test_overview_record.py` fails if one goes missing).
- Health-record tiles reworked: all fields visible up front, required-field
  validation, server errors surfaced instead of swallowed, chips instead of
  checkbox lists, and click-a-row-to-edit (PATCH) — the edit flow that used
  to be missing. Row summaries and severe-allergy flags come from
  `tileConfig.js` (`summary` / `isDanger`).
- One shared `VitalsEntryForm.jsx` behind both the nursing station and the
  Vitals tab, with units and range checks at entry (vitals lock on save).
- Appointments: reception queues patient → doctor with no time set; the
  doctor accepts / starts / ends, and those transitions stamp the times.
- Reception billing counter (`/billing`, `Billing.jsx`): find the patient,
  see what they owe, bill a card or consultation from the catalog, then
  settle it — **Pay in full / Pay half / Pay later** in one action (the
  charge is always raised; "pay later" simply leaves it on the balance).
  Plus payment against the balance with the split worked out for you
  (quarter / half / three quarters / everything owed, each showing the
  actual figure), a "still owing" list and the day's takings. Charge rows
  show what is left to pay, what was paid and what was discounted.
- Percentage discounts: pick 5/10/20/25/50/100% or type your own, give a
  reason, and the panel previews the exact amount before you commit. Works
  on a single charge or on the patient's whole outstanding bill. Cashier /
  accountant / admin only.
- Transaction History (`/transactions`, `TransactionHistory.jsx`): pick a
  patient and read their whole statement — charges, payments, discounts,
  waivers and refunds on one timeline with the balance after each entry,
  filterable by kind, plus the four totals. Read-only; billing actions stay
  on the counter. Reception can read adjustments (list/retrieve) so the
  statement reconciles, but still cannot create one.
- Pharmacy counter (`/pharmacy`, `Pharmacy.jsx`): dispensing queue →
  Dispense (deducts stock, raises the charge) → take payment at the
  counter, plus a pharmacy-payments tab filtered on `channel=pharmacy`.
- Prescribing (`/patients/<id>/prescribe`, `PrescribeDrug.jsx`): a whole
  script, not one drug at a time — a searchable drug combobox (browse the
  catalogue or type; `/items/` is searched server-side now, so a drug past
  the first page is still findable), then per-drug quantity and directions,
  sent as one `POST /prescriptions/bulk/`. Out-of-stock drugs are shown and
  unpickable; doctors still never see counts. One notification reaches the
  pharmacy per script, not per drug.
- Inventory (`/inventory`): stock on hand, receive a delivery, per-batch
  stock count, expired write-off, and the movement log. Every quantity
  change goes through `inventory/services.py` and leaves a StockMovement.
- Tests: 193 passing (`./venv/bin/python manage.py test` — the venv is at
  `backend/hmis/venv`; a bare `python` has no Django and fails misleadingly) — pharmacy dispensing +
  payment flow, charge settlement (full / half / later, oldest-first
  allocation), percentage discounts and their permission boundary, reception's boundaries on appointments and routes,
  appointment queue transitions, the nurse dashboard payload, vitals +
  nursing-note access, routing to a nurse, the overview payload and its
  permission boundary, stock control, plus: the nurse hand-off and its
  "no vitals" / "reassign" acknowledgements, the recorded-today list, the
  vitals notification and unread badge, all nine overview tiles, medical
  test upload vs typed result, the doctor dashboard cards, and the admin
  registration checks.

Not yet built:
- Sales/discount checkout UI. Note `apps/sales` is still unguarded:
  `SaleViewSet` is `IsAuthenticated` for every role and creating a
  `SaleItem` does not deduct stock. Route it through
  `pharmacy/services.py` before putting a UI on it.
- Vitals history graph (backend data supports it; no chart yet —
  `chart_display_v0` tool is available in chat for quick charts, or
  build a dedicated page with recharts)
- Patient Profile edit page (separate from the New Patient form)
- AI agents are backend-only; no frontend buttons call them yet

When picking up a task from this list, check this file's "non-negotiable
design rules" first so new code doesn't drift from what's already decided.
