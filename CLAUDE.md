# NMHS — Hospital Management Information System

Ngozi Maternity and Hospital Services (NMHS).

Instructions for Claude Code working in this repo. Read this before making
changes so new code stays consistent with decisions already made.

## What this is

An HMIS for **Ngozi Maternity and Hospital Services (NMHS)**, built for
~50 concurrent users. Backend: Django + DRF.
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
    laboratory/                              # Test catalogue + results (sparse)
    core/                                      # LockedRecordMixin, TimeStampedModel

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
   them. (Dispensing raises its charge the same way ordering a lab test does
   — see rule 24. Referring to ultrasound or the eye clinic still does not:
   those have no priced order behind them yet.)
7. **Doctors never see raw stock counts** — the `/items/` endpoint returns
   `available: true/false` for doctors (see `ItemForPrescribingSerializer`),
   not quantities. Keep this distinction if you touch the drug picker.
8. **Batches are FEFO** (first-expiry-first-out) — `Batch` ordering and the
   dispense service both rely on `expiry_date` ascending. Don't reorder
   this without updating both. FEFO is always applied *within one location*
   (rule 30): the earliest-expiring lot **on that shelf**, never the
   earliest in the building.
9. **One definition of "assigned patient"**, and a role keeps the patients it
   has actually served — `patients/access.py`
   (`doctor_patient_q` / `nurse_patient_q`). Patient identity, the health
   record tiles, notes, Vitals and nursing notes all filter through it, so a
   doctor who can open a patient can always read that patient's vitals. Add
   new ways to assign a patient there, not in an individual viewset.

   A role's list must not be conditioned on money still being owed. The
   pharmacist's used to require an unpaid charge beside a dispensed
   prescription, so a patient vanished from the pharmacy's Patients page the
   moment they paid — and "who did I hand that to on Tuesday?" became
   unanswerable. It is now simply "pending or dispensed". Beware, too, of
   two multi-valued relations in one `Q`: they match independently, so
   `Q(prescriptions__status="dispensed", charges__status="unpaid")` meant
   *any* unpaid charge, not the pharmacy's.
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
12. **A charge's balance is `amount − amount_paid − amount_discounted −
   amount_waived`** —
   money is settled by a `Payment` (allocated across open charges
   oldest-first, `billing.services.allocate_to_charges`) or written off by a
   percentage discount (`apply_percentage_discount`). Both maintain the
   charge's own figures as well as the ledger, and both go through
   `billing/services.py` — never write `amount_paid`, `amount_discounted` or
   `status` from a view, or charges drift back to reading "unpaid" forever
   while the ledger says otherwise. A discount is capped at what is still
   owed, and waiving credits only the remainder, so no path can push a
   patient into credit.
13. **Discounts and waivers are approvals, not data entry** — `POST
   /charges/<id>/discount/` (a percentage), `/discount-amount/` (a flat sum),
   `/charges/discount-balance/` (everything a patient owes) and `/waive/`
   (part or all) each take a mandatory reason and record an `Adjustment`
   naming the approver. They sit with the roles that already waive (cashier,
   accountant, admin), *not* reception — same boundary as rule 16. Pay-later
   is the exception reception *can* authorise, because the money stays owed
   (rule 26).
14. **A notification goes to whoever can act on it, and to nobody else.**
   `route_targets` never returns the person who raised the route — a doctor
   being told about the referral she just wrote is noise — and it treats two
   kinds of role differently. `POOLED_ROLES` (nurse, laboratory, radiology,
   optometrist, ophthalmologist) work a shared queue, so unassigned work is
   broadcast to all of them: that is how a lab request finds whoever is on
   the bench tonight. **A doctor is not a pool.** Doctor-work
   (consultation, procedure) goes to the named person, else to the doctors
   actually holding that patient (`patients.access.doctors_for_patient`),
   else to nobody — the route still sits in `/queue`, and `refer` answers
   with `notified` and a `notice` so the referrer knows to name somebody.
   Telling every doctor in the hospital about one dressing is how a bell
   stops being read.
15. **A laboratory result is addressed to one doctor** — `LabOrder.report_to`,
   defaulting to the doctor who referred, redirectable by the bench
   (`notify_doctor` on `save-results` or `verify`) for when that doctor is
   off. Submitting is what tells them; verifying the same test again does
   not send it twice.
16. **A route's `purpose` decides who it reaches, not department
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
17. **Reception queues work; it never marks work done** — appointments and
   patient routes both move through their viewset actions only (`status` is
   read-only on both serializers). Reception can raise a route and *cancel*
   it (the patient left, or it was a mistake); saying the work started or
   finished belongs to the clinician it was sent to.

   **And it sees only the work it raised.** `work_routes_for` used to hand
   reception every route in the hospital, which put the clinical routing on
   the front-desk screen: a doctor's laboratory referral reading "Do malaria
   test", a nurse's hand-off reading "No vitals recorded for this visit".
   Those notes are one clinician writing to another — the chart, not the
   desk's queue. Reception's queue is now `routed_by__role="reception"`:
   scoped to the desk rather than to one person, because a route raised on the
   morning shift still has to be cancellable in the afternoon. The same
   boundary is drawn wherever those rows are reachable — the printable
   `document` endpoint, and the finding on it, which the front desk never
   reads even on a route it raised itself. The laboratory request form drops
   its `clinical_notes` for the desk for the same reason. Held by
   `apps/workflow/tests/test_reception_limits.py`.
18. **The pharmacy takes its own money** — a pharmacist can read ledgers and
   charges and record a `Payment` (stamped `channel="pharmacy"` from their
   role, never from the request body), but can never waive, void or adjust.
   See `COLLECTING_ROLES` in `billing/views.py`.
19. **AI agents are advisory only** (`ai_agents/services.py`) — they must
    never auto-block or auto-approve a clinical or stock decision. A human
   (doctor/pharmacist/admin) always makes the final call. When wiring a new
   agent in, keep that boundary.
20. **No laboratory parameter is mandatory, and a blank is not a result** —
   `LabParameter.is_required` defaults to False and nothing in the seeded
   catalogue sets it True. `laboratory/services.save_values` skips every
   empty value, **deletes** a row that is cleared, and never raises on one; a
   `LabResultValue` exists only where the bench measured something, so "no
   row" is the single meaning of "not done" and the report prints only what
   was found. A test submits with one value out of fourteen. Do not add a
   required-field check to the entry form, the serializer or the view to
   "make the report tidy" — the tidiness is the point of the sparse rows.
21. **The laboratory catalogue is data, never code** — `LabTest` +
   `LabParameter` rows hold every test name, unit, reference range, result
   type and option, seeded by `laboratory/catalog.py` and edited afterwards
   on `/lab-catalogue`. Adding a test is a row, not a deployment, and
   `LabResultEntry.jsx` must never hard-code a parameter list. A test or
   parameter is **retired** (`is_active=False`), never deleted: results point
   at it.

   **Retiring is a state, so it has to be reversible** —
   `POST /lab-tests/<id>/restore/` and `/lab-parameters/<id>/restore/` are the
   mirrors of the two soft deletes, lab + admin like the retire, each leaving
   an audit row. Restoring is its own endpoint rather than a PATCH of
   `is_active`: it is one decision, so it cannot carry the rest of a
   half-filled form back to the server with it.

   And an **active-only filter belongs to `list`, never to `get_object()`**.
   `LabTestViewSet.get_queryset()` applied its browsing default to every
   action, so the moment a test was retired the API could no longer find it by
   id — the detail route, the PATCH and the restore all answered "No LabTest
   matches the given query", and retiring was a one-way door. A detail route
   already names the row the caller means; hiding it there only breaks the way
   back. `apps/laboratory/tests/test_retire_and_restore.py` holds both
   directions, repeatedly, along with the history that must not move.

   But the catalogue is *not* what an existing order reads. `LabOrderTest`
   holds an order-time **snapshot** — test name, category, specimen, price
   and every parameter's name, type, unit, range, bounds and options
   (`services.snapshot_of`) — and the entry form, the flagging, the report
   and the charge all read that copy. Re-price FBC to ₦4,000 or widen a
   reference range and yesterday's bill still says ₦3,500 and yesterday's
   report still says what it was read against. `LabOrderTest.parameters`
   falls back to the live catalogue only for rows written before snapshots
   existed. Never make a display path read `order_test.test.<field>` — that
   is exactly how history starts moving.
22. **Draft → Submitted → Verified/Released, and only the last is a report.**
   `LabOrderTest.status` carries it; verifying the order stamps each
   submitted test with `verified_by`/`verified_at`, closes the referral and
   files the chart copy. An unverified result is shown to the doctor — the
   ward sometimes needs the figure now — but always labelled provisional, on
   screen and on the printed sheet. Editing a **released** result requires a
   reason and writes a `LabResultAmendment`; the server refuses it without
   one (`code: "amendment_reason_required"`). A draft edit is just typing.
23. **A flag is arithmetic, not a diagnosis** — `services.flag_for` compares
   a number with that parameter's own bounds and answers low / normal /
   high. Where a range depends on sex, age or method the catalogue leaves the
   bounds unset and only prints the text range: nothing is flagged, which is
   the safe direction to fail in. A scientist's manual flag
   (`flag_is_manual`) is never overwritten by a recalculation, and no code
   here may name a condition.
24. **Ordering a laboratory test raises its charge; the lab never touches the
   money afterwards.** This is a deliberate exception to rule 6's "referring
   raises no charge", and it applies to the laboratory only: `services
   .add_tests` calls `billing.services.add_charge` for each test, one charge
   per `LabOrderTest` (`source_type="lab_test"`), priced from the order-time
   snapshot. The doctor's order *is* the debt — nobody retypes it at the
   counter, and a test cannot be run without appearing on a bill. Everything
   after that is the cash desk's: payment, discount, waiver, pay-later, all
   through `billing/services.py`, one ledger, one debtors list. The lab reads
   `billing` on the order and on each test and is never gated by it — a
   sample already drawn is run and the desk chases the balance. Removing a
   test cancels its charge unless money has already been taken against it.
25. **Original − discount − waiver − payments = outstanding**, and all four
   are kept. `Charge.amount` is never rewritten by an adjustment: a discount
   adds to `amount_discounted` (percentage *or* flat sum), a waiver to
   `amount_waived` (partial *or* whole), and each writes an `Adjustment`
   naming the approver and the reason. `Charge.settlement_status` is derived
   from those figures — never a stored boolean — and `payable` is the face
   value less what was given away. Adding a new way to reduce a bill means a
   new column and a new `Adjustment` kind, never an edit to `amount`.
26. **"Pay later" is an authorisation, not a payment** — `PaymentDeferral`
   records who let the patient proceed owing, when, for how much and why. It
   is deliberately *not* a charge status: the charge keeps its balance, stays
   on `?owing=true`, and still takes the next payment allocation, which is
   what closes the deferral. `settlement_status` reports "deferred" so a
   screen can say so, but nothing in the money treats it as settled. Never
   mark a charge paid to represent it.

27. **Every endpoint declares who it is for, and a test holds it to that.**
   `apps/core/tests/test_api_permissions.py` walks every `/api/` path against
   every role and compares the result with the `REACHABLE` table written in
   that file. It fails on a difference in *either* direction, and it fails by
   name on an endpoint with no entry at all — so a new viewset cannot ship
   open. It also calls every path unauthenticated and fails on any 2xx or any
   500. (`/api/items/` used to 500: `get_permissions` read
   `request.user.role` before authentication, and `AnonymousUser` has no
   `role`. In a permission path, always `getattr(user, "role", None)`.)

   **`IsAuthenticated` is not a permission here** — it means "the cashier
   too". `apps/inpatient` had it on all five viewsets, so any signed-in user
   could list every inpatient, admit somebody, move them between beds and
   discharge them; `apps/sales` still had it while `SaleItem` moved no stock.
   Reach for a role group instead.

28. **A set of roles is named once, in `accounts/permissions.py`** —
   `ADMIN_ROLES`, `PATIENT_LOOKUP_ROLES`, `WARD_ROLES`, `BILLING_ROLES`,
   `STOCK_ROLES` — and mirrored in `frontend/src/auth/roles.js`, which
   `main.jsx`'s guards and `AppShell`'s nav both read. A retyped list is how a
   page and the endpoint behind it drift apart until a role sees a nav item
   that only 403s at them.

   The frontend guard is a courtesy, never the control: it keeps somebody off
   a page that would only fail, and keeps the nav honest. `RequireAuth` says
   so in as many words, and the API is what actually refuses. Never move a
   check to the client to "save a round trip".

29. **Administration configures inventory; Pharmacy operates pharmacy stock.**
   One shared inventory, two workspaces, and the line between them is *what
   you may change*, not what you may see.

   - **Configuration** — products, categories, units, stock locations — is
     read by everyone who works stock and **written only by an admin**. The
     pharmacy reads the catalogue constantly (every dispense resolves a
     product, a unit and a price) and must never be shut out of it; what it
     cannot do is rename a category or retire a drug.
   - **Operations** — receiving, transferring, counting, writing off,
     dispensing — stay with `STOCK_ROLES`. That is the pharmacy's actual job.
   - Removing the navigation link is housekeeping, never the control:
     `apps/inventory/tests/test_workspace_boundary.py` calls each
     configuration endpoint as a pharmacist and expects 403, and calls each
     operation and expects it to work.

   `/inventory` is Administration's stock desk (admin + inventory manager);
   the pharmacy works its own shelf from `/pharmacy`. Both render the **same
   components** — `components/StockPanels.jsx` — with the pharmacy passing
   `lockedLocation`, so a pharmacist counting "the shelf" cannot post the
   count against the Main Store and a pharmacy transfer can only bring stock
   *in*. Two workspaces, one implementation; there is no second stock screen
   to drift.

   A card that opens a page the role's own guard bounces is as dead as one
   pointing at nothing — the pharmacist's low-stock alert kept aiming at
   `/inventory` after the desk moved. `ROUTE_ROLES` in
   `apps/workflow/tests/test_dashboard_links.py` mirrors `main.jsx`'s guards
   and fails on it; keep the two in step.

30. **Stock is product + batch + location, and there is no total anywhere.**
   `Item` is the product, `Batch` is the lot (batch number, expiry, cost, sale
   price — defined once, because a lot is the same lot wherever it stands),
   and **`StockRecord` is the quantity of one batch in one location**. That
   row is the unit of stock in this system. `Batch.quantity` used to be a
   column and is gone: with one number, the store and the dispensing shelf
   were the same pile and moving between them was an unaudited edit.
   `Batch.total_quantity` and `Item.total_quantity` still answer "what does
   the hospital own", but they are derived by summing locations, so they can
   never disagree with the parts.

   **Two locations run this deployment, but locations are rows, not code.**
   `StockLocation` is seeded by `inventory/migrations/0004` with `main-store`
   and `pharmacy`. The workflow is carried by two flags, never by a name:
   `is_default_receiving` (deliveries land here — Main Store) and
   `is_dispensing_point` (patients are dispensed from here — Pharmacy), read
   through `receiving_location()` / `dispensing_location()`. Adding a theatre
   store is a row; nothing in the services counts to two.

   The flow is **Supplier → Receipt → Main Store → Transfer → Pharmacy →
   Dispensing → Patient**, and each arrow is a service:
   - `receive_stock()` — a delivery, into the receiving location unless the
     caller names another. Creating a `Batch` through the API *is* receiving:
     `opening_quantity` (aliased as `quantity` for the existing form) is
     handed to the service, and is write-only so it cannot be PATCHed later.
   - `transfer_stock()` — the **only** way stock moves between locations. It
     writes a `StockTransfer` (`TRF-000123`) with a line per batch and a
     movement at each end (`transfer_out` at the source, `transfer_in` at the
     destination), applied all-or-nothing under `select_for_update`. A line
     names a *batch*, never just a product, or the expiry dates on the two
     shelves stop meaning anything; `fefo_lines_for()` turns "100 units of
     paracetamol" into batch lines first.
   - `post_stock_count()` / `record_stock_count()` — physical inventory, **per
     location**. A `StockCount` (`CNT-000123`) keeps what the system believed,
     what was found and the difference per line, and posts one adjustment per
     line that differs. `/batches/<id>/count/` **requires** a `location` and
     has no default: guessing the shelf would invent stock in one place and
     destroy it in the other.
   - `write_off_expired()` — clears every location holding the lot unless one
     is named. Expired is expired on both shelves.
   - `dispense_prescription()` (pharmacy) — draws **only** from
     `dispensing_location()`. Stock in the Main Store is not dispensable, and
     `available_quantity()` and the doctor's `available` flag both mean
     "available at the pharmacy" for that reason.

   **A quantity moves through a service or it does not move.**
   `apply_stock_change()` writes the `StockMovement` and the new quantity in
   one transaction, and `StockRecord.save()` raises `PermissionDenied` on any
   quantity change that did not come through it — the `LockedRecordMixin`
   idiom applied to stock. Over the API that is three layers saying the same
   thing: no writable quantity field on any serializer, `stock-records` and
   `stock-movements` are read-only viewsets, and the model refuses whatever
   gets past both. (`BatchSerializer` was `fields = "__all__"` with `quantity`
   writable — `PATCH /api/batches/<id>/ {"quantity": 9999}` was a valid
   request until this change.) `apps/inventory/tests/test_locations.py` holds
   all of it, including the worked example: 500 in the store, transfer 100,
   store 400 / pharmacy 100 / hospital 500.

   In a test, `apps/inventory/testing.py` (`stock_the_pharmacy` /
   `stock_the_store`) is how you put stock somewhere — a bare
   `Batch.objects.create()` now puts stock nowhere at all.

31. **One set of models, two administration interfaces.** The HMIS
   administration screens (`/admin`, `pages/admin/`) and Django admin are two
   front doors onto the same Django models — a product added on
   Administration → Products *is* the `inventory.Item` row Django admin lists,
   and a price changed in Django admin is what the HMIS loads next. Neither
   may grow its own copy of anything;
   `apps/core/tests/test_configuration.py` writes through each door and reads
   through the other, for departments, services, products, categories, units,
   locations, wards, beds, the price list and the laboratory catalogue.

   The HMIS screens are config-driven: `pages/admin/configResources.js`
   describes each configurable entity (columns, fields, what makes a row
   undeletable) and `ConfigResource.jsx` renders all of them. Adding a
   configurable entity is an entry there, not another page — the same way
   `tileConfig.js` serves the nine health-record tiles. `AdminHome.jsx` is the
   hub, and it *links* the dedicated pages that already existed (Departments,
   Users, Lab Catalogue, Billing Catalog) rather than rebuilding them.

   **Delete what was never used; deactivate what history points at.**
   `apps/core/config.py` holds the rule once: `ProtectedConfigMixin` for the
   API (409 with the counts and "deactivate it instead") and
   `ProtectedConfigAdmin` for Django admin (the delete button disappears on
   the same rows). A category nobody filed a product under is a typo and goes;
   one that fifty products point at is retired. Stock, receipts, movements,
   dispensing, invoices and lab orders are never deleted from either
   interface — they are corrected by adjustment, reversal or amendment.

   **Django admin is a technical interface, not a bypass.** It is held to the
   same rules the API is: `StockRecord` is read-only there (quantities move
   only through the services — rule 30), locked clinical records and the
   money models refuse changes, and `Batch` has no quantity field to type
   into. What Django admin adds is breadth for administration —
   `list_editable`, `list_filter`, `search_fields`, `autocomplete_fields`,
   `fieldsets` and activate/deactivate actions on the configuration models.

   **Product categories and units of measure are rows, not strings.**
   `ItemCategory` and `UnitOfMeasure` (migration `inventory/0006`, backfilled
   from the free text that was there) — so a category can be renamed once and
   reach every product, and `Item.unit_label` is what a prescription and a
   dispensing label print. `apps/inventory/testing.py`'s `product()` is how a
   test makes one; `Item.objects.create(unit="tablet")` is now a `ValueError`.

   **Settings are only settings if they do something.** `core.HospitalSettings`
   is a singleton holding the hospital's identity — which lived in a
   JavaScript constant, so a rename meant a deployment; it is loaded by
   `HospitalProvider` in `AppShell` and every printed document follows — plus
   the three thresholds the dashboards alert on (`expiry_warning_days`,
   `vitals_wait_alert_minutes`, `unpaid_charge_alert_hours`), each of which
   was a hard-coded number. `core.NotificationSetting` is read by
   `core.services.notify()` itself, so switching a category off actually stops
   those notifications; `clinical` is in `ALWAYS_ON` and cannot be switched
   off from either interface — a result reaching the clinician who ordered it
   is not a preference. Never add a setting nothing reads.

32. **Two identifiers per person, and they are not interchangeable.**
   `apps/core/identifiers.py` holds the format once.

   - **A record is addressed by an id.** `Patient.uuid` is that identifier for
     the API: `GET /api/patients/<uuid>/` works on every route and action,
     including `overview/`. The integer primary key is still there and still
     answers, because every clinical, billing, pharmacy, laboratory, ward and
     routing table holds a `patient_id` pointing at it and every nested
     `?patient=` filter passes it — `PatientViewSet.get_object()` accepts
     either and 404s on anything else. **Do not swap that primary key for the
     UUID**: it would mean rebuilding those tables, which is the one migration
     a hospital record cannot afford to get wrong. New callers use the UUID.
   - **A person is identified by a hospital number.** `NMHS-P000001` for a
     patient (`Patient.patient_number`, which is what `file_number` was
     renamed to), `NMHS-S000001` for a member of staff (`User.staff_number`).
     The letter names the register, so the two can never be confused on a form.
     Both are `editable=False`, absent from every writable serializer, and
     issued by the model on first save from the row's own primary key —
     sequential, never reused after a deletion, and never derived from the
     UUID, because a number that gets read down a phone line has to be short.
     A number already held never moves: `identifiers.adopt_number` prefers it.
   - **Only staff are numbered.** `User.is_hospital_staff` is a role or the
     superuser flag; an account with neither reaches nothing in the
     application and stays without a number rather than joining the payroll
     list. `staff_number` is nullable for exactly that reason.
   - **`file_number` is an alias, not a second field.** It is a read-only
     property on `Patient` and a read-only serializer field carrying the same
     string, because it is the key ~40 existing call sites already read —
     nested rows still send `patient_file_number` too. On the frontend
     `patientNumber()` in `components/patientIdentity.js` is the one place
     that decides which key to look in; never read `file_number` directly and
     never print an id or a UUID at a person.
   - **Neither identifier is an authorisation.** The UUID is unguessable and
     the number is not, but both come out of `patient_queryset_for` either
     way, so knowing one gets a clinician with no claim on the patient the
     same 404 as knowing nothing. `apps/patients/tests/test_identifiers.py`
     and `apps/accounts/tests/test_staff_number.py` hold all of it, including
     that boundary.


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

- **An admin reads every notification; their bell still counts only their
  own.** `GET /notifications/?scope=all` returns the whole system (admin
  only, with `recipient_name`/`recipient_role` on each row, filterable by
  category / recipient / read state and searchable by staff name), and
  `/notifications/overview/` counts them per person and per category —
  "is anybody reading the lab's queue?" without scrolling thousands of rows.
  Everything else stays personal on purpose: `unread-count` and
  `mark_all_read` always use the caller's own inbox, and marking one read is
  refused for somebody else's. An admin bell counting the hospital's traffic
  would never clear, and one press of "mark all as read" would silently
  clear every nurse's and doctor's unread notifications.

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

- **One design vocabulary: `components/ui.jsx`.** Page, PageHeader, Section,
  Card, Button, IconButton, TextLink, Field/Input/Select/Textarea, SearchInput,
  Badge, TableWrap, Modal, TabBar, Skeleton, EmptyState, ErrorState, Alert.
  Reach for a primitive before writing a fifth spelling of the same button.

  **A text action is still a control.** `Button`'s `link` / `linkDanger` /
  `linkMuted` variants are the "Waive", "Cancel", "Show more", "Pay in full"
  actions that used to be spelled `text-xs text-brand-600 hover:underline` —
  a 16px-tall hit area in a hospital where the screen is a phone held in one
  hand. There were 36 hand-written copies of that string at 5 different sizes;
  they are one `size="xs"` box now (36px, still tight beside its neighbours).
  `TextLink` is the deliberate exception: a link *inside a sentence*, which
  stays inline because forcing a 36px box into flowing prose breaks the line
  box. It pays for the smaller target with a permanent underline.

  Three more rules are baked in so no page has to remember:
  1. **Controls are >=44px on a phone**, tightening to ~38px from `sm`, and
     inputs are 16px there — anything smaller and iOS zooms the page on focus.
  2. **`min-w-0` on anything that can hold wide content.** Flex and grid
     children default to `min-width:auto`, so one chip strip or table silently
     widens the whole page. That single omission was the cause of every
     horizontal-scroll bug the audit found (`/lab-catalogue` was 722px too
     wide at 375px).
  3. **A table lives in `TableWrap`**, which scrolls itself rather than the
     page. Tables become cards only where a row genuinely does not fit —
     `LabResultEntry` and the lab catalogue's parameters do; most do not.
  Icons are `components/icons.jsx` (inline SVG on one 24px stroke grid), never
  emoji: emoji cannot take a colour, sit off the baseline and render
  differently on every platform.

- **A navigation destination opens with a `PageHeader`.** One masthead shape
  across the application — contextual icon, title, one line of what the page is
  for, an optional `meta` strip of live figures (`MetaStat`), the primary
  actions, and an optional `toolbar` row for search and filters. Every slot is
  optional, so a page whose layout genuinely differs takes the title alone and
  arranges the rest itself; `NotFound` and `Login` are deliberately outside the
  system. The pages used to open with a bare
  `<h1 className="text-2xl font-semibold">` inside a hand-rolled
  `max-w-* mx-auto p-6` — which is also how the phone gutter bug kept coming
  back, since `p-6` spends 15% of a 320px screen on margin. Reach for
  `Page` + `PageHeader`, not a new wrapper.

  **Actions belong in the header, and only once.** The primary action of a page
  sits in `actions` — but check first whether the page already offers it
  further down: Billing's counter already had "Different patient", so putting
  it in the header too would have asked the same question twice.

  **Tabs are `TabBar` + `Tab`.** Three pages had hand-rolled tab strips
  (two identical `TabButton` copies, plus the chart's `TabLink`) and two more
  had segmented pill controls; they scrolled differently, sized differently and
  none of them was touch-safe. `TabBar` scrolls itself on a phone rather than
  wrapping into three rows or widening the page.

- **The navigation must work on a phone.** The sidebar was `hidden md:block`
  with nothing behind it, so below 768px there was no way to reach any page but
  the dashboard. `AppShell` now has a rail from `lg` and a drawer below it,
  both rendering the same role-filtered list. **`NAV_ITEMS`' roles are the
  source of truth and are not to be widened for layout reasons**; the `group`
  column is presentation only, and a group with nothing visible in it does not
  render, so grouping can never surface a link a role could not already reach.

- Backend: DRF ModelViewSets + routers, one router per app in `urls.py`,
  included under `/api/` in the root `hmis/urls.py`.
- Frontend: TanStack Query for all server state (no manual `useEffect`
  fetching), Tailwind utility classes only (no CSS files besides
  `index.css`'s Tailwind directives), one page per route in `src/pages/`.
- **Printing is browser printing, never a generated PDF.** A document is a
  `PrintSheet` (`components/PrintSheet.jsx`) rendering into `#print-area`;
  the `@media print` block in `index.css` hides everything else, so the page
  that comes out is the document alone. No library, works with whatever
  printer the desk has, and "Save as PDF" is in the same dialog. Reception's
  documents live in `PrintDocuments.jsx` — `PatientCardSheet`, `BillSheet`,
  `PatientBillSheet`, `ReceiptSheet` — and every other department's in
  `DepartmentDocuments.jsx`. All of them print black on white, because a
  coloured panel either burns toner or is dropped by the driver. Hospital name
  and address are the `HOSPITAL` constant in `PrintSheet.jsx`: edit once,
  every document follows. Shared parts — `SheetHeader`, `PatientBlock`,
  `SheetSection`, `Stamp`, `WriteInLines`, `SheetStatus`, `SheetFooter` — are
  in `PrintSheet.jsx`; reach for one before spelling a second patient block.

- **What "Print" means is decided by the department, in one place:
  `components/printing.jsx`.** The chart's Print button used to be the patient
  card for everybody — right at the front desk, a wasted sheet at the bench.
  It now asks `chartDocuments({role, tab})`, and every other page passes an
  explicit list to `<PrintButton>`. **The first document it resolves is what
  the button prints**; anything else that role may print is behind a caret,
  never a second button that looks the same. The registry is the contract:

  1. **`roles` mirrors the backend, and never widens it.** Each sheet fetches
     its own data from an endpoint that role already passes — the clinical
     summary from `/patients/<id>/overview/` (ClinicalRecordAccess), the
     dispensing note from `/prescriptions/` (doctor + pharmacist), the lab
     request form from `/lab-orders/<id>/request-form/` (WORKLIST_ROLES, which
     is why a nurse is *not* on that one). The list exists so a role is never
     offered a button that would only 403; the API is what actually refuses.
  2. **`needs` is honesty about context** — a document with no record behind
     it is not offered, so no button ever opens an empty sheet.
  3. **A patient-level document belongs to the page header; a per-record one
     belongs to its row.** *This* laboratory order and *this* scan print from
     the row, because a header button would have to guess which you meant.
     A tab in `TAB_OWNS_ITS_DOCUMENTS` (billing) prints its own, and the
     header does not offer a second copy under a different name.

  Two endpoints exist for the paperwork itself.
  `GET /lab-orders/<id>/request-form/` is the sheet that travels with the
  specimen — what was asked for, on what sample, at the order-time price, and
  **never a result value**, which is exactly why the desk may print it while
  `/report/` stays laboratory-and-doctor.
  `GET /patient-routes/<id>/document/` answers the referral form and the
  report from one payload, so a form cannot disagree with the report stapled
  to it; the `result` half is omitted for reception. Both read wider than the
  queue in one direction only — time, not ownership: a document outlives the
  errand (`work_routes_for(user, statuses=None)`). Held by
  `apps/laboratory/tests/test_request_form.py` and
  `apps/workflow/tests/test_printable_documents.py`.
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
- **A referral answers back, and the answer is kept.** The unit conducts the
  test and files typed values, an uploaded report, or both, via
  `POST /patient-routes/<id>/record-result/` (multipart) or by passing the
  same fields to `complete/`. Never by PATCHing the row — `result`,
  `result_by` and `result_at` are `read_only` on the serializer so a finding
  is always stamped with who wrote it.

  It is filed **twice, on purpose**: on the `PatientRoute` so the station and
  queue can show it, and as a `MedicalTest` (`source_route` FK) so it is
  still on the patient's Tests & Diagnostics record next year, after the
  visit has closed. Re-saving updates that same row rather than filing a
  second copy. `PURPOSE_TEST_TYPE` maps the unit to the test type.
  Saving notifies the doctor who raised the referral; they read it on the
  chart, with the document attached. Referral notifications link to the
  unit's own station (`PURPOSE_STATION`), not the generic queue.
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
- **A card opens the page it counts** — and
  `apps/workflow/tests/test_dashboard_links.py` walks every role's cards,
  alerts and tasks and fails if one points at a route `main.jsx` does not
  have. Two did (`/admissions`, `/investigation-orders`); nothing was
  guarding them, because the notification-link test only reads
  `Notification.action_url`. Keep both route lists in step.
- **A card opens the page it counts.** Doctors get **My patients** →
  `/patients` (counted with `patients.access.patient_queryset_for`, the same
  rule the Patients page filters by, so the number and the list agree), then
  **My queue** → `/queue`, then unread and seen-today. `/queue` is only
  routable for reception, doctors and nurses, so other roles keep
  `/patients` — a card must never link somewhere the role's guard bounces.
- **The chart has a tab per department, not one long page** —
  `PatientDetail.jsx`: Overview · Health Record · Medical Notes · Vitals ·
  **Lab · Ultrasound · Eye · Procedures · Admission · Pharmacy** · Billing.
  Each is a route (`/patients/:id/lab`, `/ultrasound`, …) so a notification
  can land straight on the answer — `workflow.views.chart_url_for` and the
  laboratory's own notification both deep-link to the right tab instead of
  the chart's front page.
  - **Lab** (`LabResultsTab.jsx`) — every order, every parameter that has a
    value, with the unit and reference range *as they stood when the test
    was run*, the flag, amendments, and a printable report. Provisional
    results are labelled; released ones name the verifier.
  - **Ultrasound / Eye / Procedures** (`ReferralResultsTab.jsx`) — one
    config-driven component for the three, the way `DepartmentStation`
    serves all three units. Read from the overview payload the chart already
    fetches, so opening a tab costs no extra request and cannot disagree
    with the Overview.
  - **Admission** (`AdmissionTab.jsx`) — ward and bed as the largest thing
    on the panel, because that is what somebody reads before walking to the
    ward; plus days on the ward, attending doctor, and the stay history.
    Read-only: admitting and moving beds stay on `/admissions`, where the
    bed is held under `select_for_update`.
  - **Pharmacy** (`PharmacyTab.jsx`) — what was actually dispensed, kept
    apart from what was only written. "She is on metronidazole" means
    something different depending on whether the pharmacy ever filled it.
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
  counter, plus a pharmacy-payments tab filtered on `channel=pharmacy` — and
  four stock tabs pinned to the dispensing shelf (stock on hand, request from
  the store, physical count, movement history), rendered from the same
  `StockPanels.jsx` components Administration uses (rule 29). The tab is in
  the URL, so the dashboard's stock alert lands on the shelf it warns about.
- Prescribing (`/patients/<id>/prescribe`, `PrescribeDrug.jsx`): a whole
  script, not one drug at a time — a searchable drug combobox (browse the
  catalogue or type; `/items/` is searched server-side now, so a drug past
  the first page is still findable), then per-drug quantity and directions,
  sent as one `POST /prescriptions/bulk/`. Out-of-stock drugs are shown and
  unpickable; doctors still never see counts. One notification reaches the
  pharmacy per script, not per drug.
- **Administration** (`/admin`, `pages/admin/`) — the hospital's own setup in
  one place, editing the same Django models Django admin edits (rule 31), and
  the only workspace that configures inventory (rule 29). Its **Inventory**
  section holds products, product categories, units of measure and stock
  locations, plus the stock desk itself — stock operations, physical counts,
  adjustments and write-offs, and the movement log, each opening `/inventory`
  on the tab it names.
  Also:
  services, wards and beds; hospital settings (identity + the three alert
  thresholds) and notification settings. The hub also links Departments,
  Users, the Laboratory Catalogue and the Billing Catalog, which keep their
  own pages. Every list shows what is in use by default with a "Show
  inactive" toggle, and offers Delete only where nothing points at the row —
  the API's 409 is the backstop, not the first anyone hears of it.
- Inventory (`/inventory`) — **Administration's** stock desk (admin +
  inventory manager; a pharmacist works their own shelf from `/pharmacy`
  instead, rule 29): five tabs against **two stock locations** (rule
  30) — **Stock on hand** (product / batch / location / expiry / quantity,
  filterable by location, with per-shelf count and write-off), **Receive**
  (into the Main Store by default), **Transfer** (Main Store → Pharmacy: pick
  batches off the source shelf, one document, its own `TRF-` reference),
  **Physical count** (per location — system qty, counted qty, difference per
  line, posted as one `CNT-` document) and the **Movement log** (filterable by
  location and kind, showing the transfer reference on both halves). Every
  quantity change goes through `inventory/services.py`, leaves a
  StockMovement, and cannot be made any other way.
- Tests: 595 passing (`./venv/bin/python manage.py test` — the venv is at
  `backend/hmis/venv`; a bare `python` has no Django and fails misleadingly) — pharmacy dispensing +
  payment flow, charge settlement (full / half / later, oldest-first
  allocation), percentage discounts and their permission boundary, reception's boundaries on appointments and routes,
  appointment queue transitions, the nurse dashboard payload, vitals +
  nursing-note access, routing to a nurse, the overview payload and its
  permission boundary, stock control, plus: the nurse hand-off and its
  "no vitals" / "reassign" acknowledgements, the recorded-today list, the
  vitals notification and unread badge, all nine overview tiles, medical
  test upload vs typed result, the doctor dashboard cards, and the admin
  registration checks, plus the laboratory: partial results (three of
  fourteen FBC indices save and submit; a cleared value deletes its row; a
  comment alone is a result; nothing at all is not), flagging and its safe
  gaps, the catalogue's permission boundary, adding a test and its parameters
  without a deployment, and the referral round trip through to the amendment
  trail — plus the money: the nine end-to-end scenarios (paid, unpaid,
  partial, pay-later, discount, waiver, a catalogue re-price, a reference-range
  change, and an order holding only the one test the doctor asked for), and
  the write-off / deferral boundaries in `apps/billing/tests/
  test_deferrals_and_writeoffs.py` — plus the permission surface itself:
  `apps/core/tests/test_api_permissions.py` checks every API path against
  every role and against an anonymous caller.

- **Outstanding** (`/outstanding`) and **Waived & Written Off**
  (`/waivers`) — reception, cashier, accountant and admin. The debtors list
  is `GET /ledgers/?owing=true`: filtered and ordered **in the database**,
  biggest first, so settling in full is what removes somebody — nothing is
  ticked by hand and nobody lingers because a page only fetched 25 rows. It
  carries the file number and phone, because the list is worked by phone.
  The write-off register is `GET /adjustments/?kind=` over the three kinds
  (waiver / discount / refund), each showing the amount, the charge it
  forgives, the reason and who approved it. Read-only: a new waiver is
  granted on the billing counter, against the charge in front of you.
- **Laboratory** (`apps/laboratory`, `/laboratory` + `/lab-catalogue`) — a
  configurable test catalogue and the results entered against it.
  - **Catalogue**: 66 tests / 278 parameters seeded across haematology,
    chemistry, microbiology, parasitology, urinalysis, serology and
    endocrinology (FBC, RFT, LFT, lipids, OGTT, HbA1c, MP + mRDT, urinalysis,
    every C&S swab, semen analysis, Widal, hormones, PSA…), plus panels
    (antenatal booking, malaria screen) that **reference** catalogue tests
    rather than duplicating them. Seeded by
    `laboratory/migrations/0002_seed_catalogue.py`; re-runnable with
    `manage.py seed_lab_catalogue`, which never overwrites a row the hospital
    has edited.
  - **Entry**: `LabResultEntry.jsx` inside the laboratory station. The bench
    picks tests off the catalogue, fills in **only what it ran**, saves a
    draft or submits, and verifies to release. Parameters are grouped
    (Physical / Chemical / Microscopy) where the catalogue says so, flagged
    low/high live as you type, and the value box is text — a bench writes
    "< 5", "Nil", "3+" and "Trace", which a number input silently discards.
  - **Order**: raised from the doctor's existing referral
    (`POST /lab-orders/for-route/`, idempotent), so `PatientRoute`,
    the queue and the notifications keep working unchanged. Verifying closes
    the route, writes the summary onto it and files the permanent
    `MedicalTest` copy, exactly as the other units do.
  - **Report**: `LabReportSheet.jsx`, browser printing like every other
    document, carrying hospital, patient, order and specimen identity, the
    requesting doctor, tested-by / verified-by and times — and **only the
    parameters that have a result**. Unverified prints stamped "Provisional".
  - **Retire / restore**: a test or a parameter is retired from the catalogue
    page and brought back from the same panel — the row, its parameters and
    every order pointing at it survive both. A retired test drops off the
    picker, the default listing and the category counts, and cannot be added
    to a new order; `?all=1` is what the catalogue page asks for to see it,
    behind a "Show N retired" toggle so the working list stays what is in use.
  - **The catalogue page is master–detail** (`LabCatalogue.jsx`): a filtered
    list beside the editor on a wide screen, and on a phone two *views* — the
    list steps aside when a test is opened and a "← All tests" link brings it
    back. It used to be one column stacked, so tapping a test scrolled the
    editor in below a 60vh list, off the bottom of the screen, with nothing
    saying it had happened. Tests are grouped under category headings, and
    parameters under their own Physical / Chemical / Microscopy headings —
    the same grouping the bench's entry form uses. The parameter table becomes
    cards under `md`, because a 40rem table dragged sideways under a finger is
    not a table.
  - **Corrections**: changing a value that has already been reported writes a
    `LabResultAmendment` naming the person and the reason; a draft edit does
    not. The lab admin classes are read-only over results for the same reason
    the billing ones are.
  - **Money**: ordering a test raises its own charge, there and then
    (`source_type="lab_test"`, one per test, priced from the order-time
    snapshot). The patient settles it at Reception or the cash desk through
    the billing counter like any other charge — in full, in part, discounted
    by percentage or by flat sum, waived in part or whole, or authorised as
    **Pay later** (`PaymentDeferral`, which records the approver and leaves
    the money owed). The bench sees the state per test — Paid / Part paid /
    UNPAID / Pay later — read-only, and never a gate.
  - **Only what was asked for**: an order holds the tests the clinician
    named, full stop. Panels are behind "Add a test" and confirm what they
    are about to add and what it will cost, because they used to sit as
    one-click chips on the order page and could put twelve tests on a
    malaria request. Anything the bench adds is marked `source="laboratory"`
    and billed identically.
  - **Who reads what**: the catalogue is a price list, so reception and the
    cash desk read it; a *result* is clinical, so `retrieve` and `report` are
    laboratory + doctor + admin only, and the list answers with a summary
    (order, patient, status, tests — no values) unless a clinical role asks
    for `?detail=1`.
- Printing: registering a patient ends on a confirmation with the file
  number and **Print patient card** (reprintable any time from the chart
  header); the billing counter and the patient's Billing tab print an
  **Invoice** (open charges) or a **Statement** (everything), and offer a
  **Receipt** the moment a payment is taken — the patient is still at the
  desk. Receipts carry the amount in words, method, channel, who received
  it and the balance left. Transaction History prints the statement too, and
  the pharmacy counter now prints its own receipt as it takes the money.
- **Every department prints its own document, chosen for it.** The Print
  button reads `components/printing.jsx` (see the convention above), so:
  reception keeps the **patient card** (plus the bill behind the caret), the
  laboratory prints the **request form** at the order header and the
  **report** from the results panel, ultrasound / the eye clinic / procedures
  print the **request form** while the work is open and the **report** once a
  finding is written — from the station *and* from the chart's own tab, per
  referral — the pharmacy counter prints the **dispensing note** (with the
  script behind the caret), the bed board prints the **admission slip** off
  the row it belongs to, the vitals station prints the **observation record**,
  and a doctor prints the **clinical summary**: allergies, latest
  observations, the last three notes, what the units reported and current
  medication. Nothing prints a registration card by accident any more.
- **Admissions** (`/admissions`, `Admissions.jsx`): the bed board per ward,
  who is on the ward, admit / move bed / discharge. The `apps/inpatient` API
  already existed with no page in front of it. A bed is held under
  `select_for_update`, so two patients cannot be put in one; discharging
  frees it.
- Billing is no longer two categories: `BILLING_CATEGORIES` covers cards,
  consultation, laboratory, ultrasound, eye, procedure and **other**, and
  both billing panels fall back to a write-in (description + amount) for
  "other" *or* for any category with nothing priced yet — so a one-off is
  billed and shows on the statement instead of dead-ending on an empty
  dropdown.

Not yet built:
- Sales/discount checkout UI. `apps/sales` is now **admin-only** rather
  than open to every signed-in role, because creating a `SaleItem` still
  deducts no stock — it sells something the shelf believes it has. Route it
  through `pharmacy/services.py`, then widen the permission and its row in
  `test_api_permissions.py` together.
- `apps/diagnostics` is still unreachable, and `apps/laboratory` has now
  answered the question it was left open on: the laboratory owns catalogued,
  priced, parameterised tests, and it rides on `PatientRoute` +
  `MedicalTest` rather than replacing them. What is left in `diagnostics` is
  imaging-shaped (`InvestigationCatalog`/`Order`/`Result`) with no page in
  front of it — either give ultrasound the same treatment the lab just got,
  or delete the app. Nothing depends on it.
- Vitals history graph (backend data supports it; no chart yet —
  `chart_display_v0` tool is available in chat for quick charts, or
  build a dedicated page with recharts)
- Patient Profile edit page (separate from the New Patient form)
- AI agents are backend-only; no frontend buttons call them yet

When picking up a task from this list, check this file's "non-negotiable
design rules" first so new code doesn't drift from what's already decided.
