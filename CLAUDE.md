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
   See rule 41 for what reads the category.

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
     the API *and for the browser*: `/patients/<uuid>` is the chart's URL and
     `GET /api/patients/<uuid>/` works on every route and action, including
     `overview/`. The integer primary key is still there and still answers,
     because every clinical, billing, pharmacy, laboratory, ward and
     routing table holds a `patient_id` pointing at it and every nested
     `?patient=` filter passes it — `PatientViewSet.get_object()` accepts
     either and 404s on anything else. **Do not swap that primary key for the
     UUID**: it would mean rebuilding those tables, which is the one migration
     a hospital record cannot afford to get wrong.

     **Where each of the three goes, on the frontend.** `PatientDetail` and
     `PrescribeDrug` read the route parameter (`:patientUuid`), fetch the
     patient once, and hand each child the identifier it actually needs — the
     UUID for links and `/patients/<…>/` reads, the integer pk for `?patient=`
     filters, FK write bodies **and every TanStack query key**. The keys stay on
     the pk deliberately: `ReferPatient`, `DepartmentStation`, `LabResultEntry`
     and `GenericTileModal` all invalidate `["patient-overview", <pk>]` from
     outside the chart, so a key moved to the UUID would silently stop a
     doctor's chart refreshing after a referral. `components/patientIdentity.js`
     is the one place that decides which key holds which — `patientNumber()`
     for the printed identity, `patientUuidOf()` for the routing one — and the
     print sheets in `printing.jsx` keep the pk on purpose, because they build
     no link and every list they print filters on it.

     A chart URL we raise ourselves carries the UUID too (notification
     `action_url`s, dashboard task hrefs). Rows written before the move keep
     their integer, so `PatientDetail` opens either and then rewrites a legacy
     `/patients/13` to the UUID form with `replace: true` — which is also what
     keeps old bookmarks working. Both link tests normalise a UUID segment as
     well as an integer one for that reason.
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

33. **Revenue is money received. A write-off is not revenue.**
   `apps/billing/reporting.py` is the one place the hospital's money is
   aggregated, and it reports a period **two ways, never blended**, because
   they answer different questions and only one of them reconciles.

   - **Collections (cash basis)** — `Payment` rows between the two dates.
     This is Total Revenue. A discount is not revenue and a waiver is not
     revenue: nobody handed anything over. A **part payment is** revenue —
     the money is real — and is counted again separately as "money taken on a
     bill still owing", which is what `Charge.status in (unpaid, partial)`
     means.
   - **Charges (cohort basis)** — the bills *raised* between the two dates and
     what has become of them since. This is the only basis the identity closes
     on, per department and down the Total row alike:

         gross − discounts − waivers             = net due
         net due − collected against those bills = outstanding

   Mixing them — this month's bills against this month's cash — is how a
   reconciliation ends up short with nobody able to say why. The API keeps
   them in separate blocks and the page labels each one; never subtract across
   the two.

   **A payment records which charge it settled.** `PaymentAllocation`
   (payment → charge → amount) is written by `services.allocate_to_charges`
   inside the same transaction as the allocation it records. Before it, the
   spreading moved `Charge.amount_paid` and threw the link away, so "what did
   the laboratory collect in June?" had no answer at all — the money knew when
   it arrived and the charge knew its department, and nothing joined them. It
   is an audit row: it changes no figure, no workflow and no ordering, and
   `Charge.amount_paid` is still the charge's own settled amount. Migration
   `billing/0014` reconstructs it for history, capped at what each charge
   already shows as paid, so it can under-attribute but never invent.

   **A department comes from `Charge.source_type`, not from the department
   FK.** That column is nullable and in practice almost never filled in — the
   laboratory and the pharmacy raise charges through services that never set
   it. What is always set is the source type: the counter posts the
   `BillingItem` category, the lab stamps `lab_test`, the pharmacy stamps
   `prescription`. `reporting.department_for` maps it, falls back to the
   department FK, then to "Other / Unclassified" — a charge is never dropped
   from the breakdown, and a department the map has never heard of gets a
   bucket rather than being folded away.

   **Anything the figures cannot place is named, never hidden.** Two ways that
   happens, both reported rather than silently absorbed:
   `departments()` adds an **Unattributed** row for cash whose allocation
   predates the allocation rows, so the chart's bars always sum to the
   headline revenue figure; and `adjustments()` reports `unlinked` — money
   written off through `POST /api/adjustments/` against a patient's *ledger*
   with no charge behind it, which therefore reduces what they owe without
   ever appearing in a department's column. A number the reader cannot
   reconcile is worse than one that says which part it cannot place.

   **It is aggregated in the database, in a fixed number of queries.**
   `GET /api/finance/report/` costs the same on 26 charges as on 26,000 —
   `apps/billing/tests/test_financial_report.py` asserts exactly that, and it
   is what caught `Charge.settlement_status` querying its deferral once per
   row (the fix is `settlement_with(deferral)`, the same rule taking a
   prefetched one). Never sum a page of transactions in React.

   **Department attribution happens at the chokepoint, and the backend wins.**
   `add_charge` is the only place a `Charge` is created, so it is the only
   place attribution belongs: `resolve_department` reads the charge's own
   `source_type` and fills the FK in. The precedence, in order —

   1. **the authoritative `source_type` map** (`billing/departments.py`);
   2. **an explicitly supplied department**, only for a source the map does
      not cover;
   3. **`None`**, for a source that is genuinely unattributable.

   A supplied department **cannot override** an authoritative source type:
   `source_type=prescription` with `department=reception` yields a Pharmacy
   charge, never a Reception one. It is ignored rather than rejected, the same
   way `Payment.channel` is stamped from the collector's role and never
   trusted from the client (rule 18) — a 400 over a field the frontend does
   not even send would fail a real transaction to correct a field nobody
   typed, and the response carries the resolved department either way.
   Step 3 still raises the charge: money owed is never refused over a
   reporting field.

   **`investigation`, `other` and a blank source are deliberately unmapped.**
   Diagnostics passes `catalog.department`, and that row knows which unit
   performs the study better than a hard-coded default; a write-in charge has
   no department the system can know, and inventing one is the guesswork this
   attribution exists to replace.

   **Nothing is backfilled.** The 19 historical charges with neither field
   stay "Other / Unclassified". A department is never inferred from a
   description, a route, a timestamp or a naming convention — trustworthy
   history beats artificially complete history.

   **A discount or a waiver must name the charge it forgives.**
   `AdjustmentSerializer.validate` refuses an unlinked one (400,
   `code: "charge_required"`), closing the generic `POST /api/adjustments/`
   path beside the charge-scoped actions that always named one.

   **A refund is never posted as an adjustment.** The same `validate` refuses
   `kind="refund"` outright (400, `code: "refund_workflow_required"`), on
   create *and* on update — it reads the resulting kind, so a PATCH cannot turn
   a discount into a refund or edit the ledger row the workflow wrote beside
   its `Refund`. There is exactly one way money goes back: the Refund workflow
   (`POST /api/payments/<id>/refund/`, `refund_charge`, `cancel_and_refund`),
   which writes a `Refund` through `refund_payment` — the record Total Facility
   Revenue, net revenue retained and the refunds report subtract. It used to be
   allowed here, and a refund raised that way moved the ledger while every
   revenue figure said nothing went back. The workflow writes its own
   `Adjustment(kind="refund")` in `billing/services.py`, never through the
   serializer, so it is unaffected. Both rules are on the serializer and never
   on the model, so `Adjustment.charge` stays nullable and the rows history
   already holds stay readable — and stay reported, as `adjustments.unlinked`.
   Held by `apps/billing/tests/test_refund_only_through_workflow.py`.

   **Total Facility Revenue is all time: payments received − refunds
   processed.** `reporting.facility_revenue()` sums every `Payment.amount` and
   subtracts every `Refund.amount` — nothing else, no period, no desk, no user —
   and the report carries it as `facility_revenue` beside the period's blocks,
   shown on `/finance` above the summary cards. A `Payment` row *is* a
   successful payment: the model has no status or void state, the API never
   edits or deletes one, and a part payment is just a smaller row. Every refund
   path writes a `Refund` through `refund_payment`, so partial refunds add up.
   Discounts and waivers are **never** subtracted (the money was never
   received; subtracting them from payments would count them twice), and bills
   still owed or cancelled before payment add nothing. It is not
   gross − discounts − waivers − outstanding — that is receivables. Two known
   edges: `POST /api/adjustments/` can still record `kind="refund"` with no
   `Refund` row, which this figure (like `collections.refunds`) does not see;
   and a patient purged from Django admin (rule 37) takes their payments and
   refunds out of it. If `Payment` ever gains a void state, teach this function
   to exclude it — `test_facility_revenue.py` fails until you do.

   **Who reads it**: `FINANCE_REPORT_ROLES` — cashier, accountant, admin.
   Deliberately narrower than `BILLING_ROLES`: reception bills at a window,
   and hospital-wide revenue by department is a management figure, the same
   boundary rule 13 draws on discounts and waivers. Mirrored in
   `frontend/src/auth/roles.js`; `/finance` is one page for both audiences,
   with the cashier additionally seeing what their own desk took.


34. **The seven revenue departments are seeded, and `code` is their identity.**
   `apps/billing/departments.py` holds the registry once — reception,
   consultation, laboratory, pharmacy, radiology, eye, theatre — with the
   `source_type` values that resolve to each. `add_charge` attributes from it,
   `billing/reporting.py` groups by it, and
   `departments/migrations/0002_seed_revenue_departments.py` creates a
   `Department` row per entry. The migration repeats the codes and names as
   literals because a migration must be self-contained;
   `apps/departments/tests/test_department_registry.py` fails if the two
   copies drift.

   Departments had **no seed at all** before this — `0001_initial` made the
   table and nothing filled it — which is why a fresh install had none, why
   `Charge.department` was null on 96% of charges, and why a laboratory
   referral was routed to "General Medicine" (the routing falls back to
   whichever active department is first).

   **The seed never overwrites.** `code` is the machine identity and `name` is
   a label, so renaming "Radiology / Ultrasound" to "Imaging" keeps every
   attribution working and the seed leaves that name alone on every future
   run. It does not reactivate a department an admin retired, and it does not
   touch departments it did not create. Where a hand-made department already
   carries a seeded name, it **adopts** it by taking the stable code — `name`
   is unique, so creating a second one would fail the constraint.

   **A department that has taken money cannot be deleted.**
   `Charge.department` is `on_delete=PROTECT` and `charge_set` is in
   `DepartmentViewSet.protected_relations` and `DepartmentAdmin`'s — it used
   to be `SET_NULL` and listed in neither, so deleting a department silently
   erased which unit had earned every charge it ever took. Retire it with
   `is_active=False`: an inactive department stops being offered for new work
   and keeps its history, which still reads.


35. **Money is announced once, from the service, to the desks that work it.**
   `apps/billing/notifications.py` is the one place a financial event becomes a
   notification, and `billing/services.py` is the only thing that calls it — so
   a charge raised by the laboratory or the pharmacy reaches the cash desk
   exactly the way one typed at the counter does, and a view cannot forget.

   The events are the ones that change what a patient owes: a charge raised, a
   payment (announced as **Paid in full** or **Part payment** according to what
   the balance says *afterwards*, not what was intended at the window), a
   discount, a waiver, a refund, a pay-later authorisation and a cancelled
   bill. Recipients are `FINANCIAL_NOTICE_ROLES` — admin, hospital admin,
   cashier, accountant, reception — minus whoever performed the action, because
   a cashier told about the payment they just took is noise (rule 14). No
   clinical role is ever on it.

   **Once per decision**, which is a rule about loops as much as about views.
   `ChargeViewSet.create` no longer notifies on top of `add_charge`;
   `discount_patient_balance` and the laboratory's `add_tests` pass
   `notify=False` down and announce the total themselves, so a script of six
   drugs or an order of five tests is one line on the cash desk's bell rather
   than six. Any new service that loops over `add_charge` must do the same.

   The message carries the patient's name and hospital number, the amount, what
   was done, who did it, the reference and the balance afterwards — and nothing
   off the chart. `Charge.description` ("Laboratory: FBC") is a billed service
   and is deliberately as far as it goes. Held by
   `apps/billing/tests/test_financial_notifications.py`.

36. **A refund is a transaction, never an undo.** The `Payment` row is never
   edited and never deleted — it is the evidence the money arrived — so the
   arithmetic is additive the way a ledger's always is:

       Payment  +10,000
       Refund    −3,000
       Net         7,000

   `Refund` points at the payment it answers and carries the amount, the
   reason, who processed it, who authorised it (separate columns even though
   this workflow does not force two people), the method and the time.
   `RefundAllocation` is the mirror of `PaymentAllocation`: it names the bills
   the money came back off, newest settlement first, decrements each charge's
   `amount_paid` and lets the balance read as owed again. `Charge.amount` is
   untouched, so rule 25's identity still closes. Every refund also writes the
   `Adjustment(kind="refund")` the ledger, the statement and the write-off
   register already read — one place money is recorded, not two.

   `Payment.refundable_balance` is what is left to give back, read under
   `select_for_update`, so partial refunds add up and can never pass the
   payment's own total. Refused: no reason, zero or negative, more than the
   refundable balance, and anything at all against a payment already fully
   refunded. Nothing is written on a refusal.

   **Who**: `REFUND_ROLES` — cashier, accountant, admin — the same boundary
   rule 13 draws on discounts and waivers. Reception may collect and never
   reverse; the pharmacy counter likewise (rule 18).
   `POST /api/payments/<id>/refund/` is the only way one is made;
   `GET /api/refunds/` is the read-only register, which BILLING_ROLES read so a
   statement adds up in front of the person collecting the rest.

   **One entry point, rendered twice.** `components/RefundAction.jsx` is the
   button, and both places a payment is listed render *it* rather than each
   spelling out its own control: the Billing counter's `PatientPayments` and
   Transaction History's statement. `components/refundPolicy.js` holds the
   decision behind it — who may refund, what is left, what the label says — so
   the counter can never offer a refund the statement calls spent. The label
   carries the figure (**"Refund ₦7,000"**, the *remaining* refundable amount,
   not the payment's face value), because that is the number a cashier needs
   before they click. It is `dangerOutline`, never a text link: a refund takes
   cash out of the drawer and must not look like "Show more". With nothing left
   it renders a plain "Fully refunded" badge rather than a greyed-out button —
   a disabled control invites clicking to find out why.

   The frontend refuses early only to spare a round trip; `refund_payment`
   re-reads the balance under `select_for_update` and is what actually decides,
   so a stale figure on screen loses that argument rather than winning it. A
   403 and a 400 are both surfaced in the dialog, which stays open so the
   amount can be corrected. On success the toast names the amount *and* says
   the original payment stays on the record, and every key the money touched is
   invalidated — ledger, ledgers, charges, payments, adjustments, refunds,
   dashboard, finance-report, unread-count and the chart's own
   `["patient-overview", <pk>]`.

   **In the report** (rule 33) refunds are their own category and are never
   netted into revenue: `collections.total` stays the gross the drawer took,
   `collections.refunds` is what went back, `collections.net` is what was kept,
   and the department table carries **Refunded** and **Net kept** beside
   **Received**, attributed through the allocation to the department that took
   the money. Held by `apps/billing/tests/test_refunds.py`.

37. **Only the Super Admin deletes a patient, and only where there is no
   history.** `Role.ADMIN` is spelled "Super Admin" in the role list and
   `hospital_admin` is the ordinary administrator beside it, so the distinction
   already existed; `IsSuperAdmin` in `accounts/permissions.py` is what reads
   it. `DELETE /api/patients/<uuid>/` used to be `IsReception()` — reception
   and every administrator could permanently delete a patient.

   Three gates, in order: **who** (`IsSuperAdmin`, and the role scoping still
   applies on the way out — a superuser with no HMIS role reaches no patient);
   **deliberateness** (the body must carry the patient's own hospital number in
   `confirm`, so a DELETE at the wrong URL cannot succeed); and **history** —
   anything in `patients.views.PROTECTED_HISTORY` and the answer is 409 with
   the counts, the same shape `ProtectedConfigMixin` uses for configuration
   (rule 31). Charges, payments, adjustments, refunds, deferrals, visits, lab
   orders, admissions and investigation orders are all `PROTECT` at the
   database as well, so the refusal is two layers deep and financial and audit
   history is never cascaded away to let a delete through.

   What does cascade is what only ever described that person: the ledger row,
   vitals, notes, the nine health-record tiles, appointments and prescriptions.
   That is the case this endpoint is for — a registration typed twice. The
   audit row is written **before** the delete and keeps the number, UUID and
   name in its `details`, because `object_id` would point at nothing.
   Held by `apps/patients/tests/test_deletion.py`.

   **The one way past it is a purge, and it is Django admin's alone.** A
   registration that was never a real patient — a demo, a screenshot, a run
   through the till — carries bills and payments as fictional as the person,
   and the 409 above leaves no way to remove it. `patients/services.py`
   `purge_patient()` does: Super Admin only (`accounts.permissions
   .is_super_admin`, the same test `IsSuperAdmin` reads), the hospital number
   typed and a mandatory reason, then every `PURGE_ORDER` relation and
   everything cascading from it deleted in one transaction, after an
   `AuditLog` row (`patients.purged`) naming the reason, the counts and the
   money charged / paid / refunded. It changes the past on purpose — purged
   payments leave the finance report — which is why it is exactly this narrow.
   Django admin's Delete button leads to its confirmation page (which lists
   everything that goes, from Django's own deletion collector), bulk delete is
   removed, and `DELETE /api/patients/<uuid>/` and every HMIS screen still
   answer 409: this is the sanctioned exception to rule 31's "Django admin is
   not a bypass", not a precedent for another. A new `PROTECT` foreign key to
   `Patient` must join `PURGE_ORDER`. Held by
   `apps/patients/tests/test_admin_purge.py`.


38. **Cancelling a service and refunding money are different decisions.**
   Getting them confused is how a patient ends up owing ₦4,000 for a laboratory
   test that was never run.

   - **Refund** — money goes back. `refund_payment` (payment-scoped) or
     `refund_charge` (one service). The bill **stays active** and becomes owed
     again, which is right when the service *was* delivered.
   - **Cancel** — `cancel_charge`. The obligation ends; no money moves. The row,
     its `amount`, its department and its history all stay; `status` carries the
     state and `cancelled_at` / `cancelled_by` / `cancellation_reason` carry who,
     when and why. `refresh_ledger` already excludes cancelled charges from
     `total_charges`, so the outstanding falls out of the arithmetic rather than
     being forced, hidden or deleted.
   - **Cancel & refund** — `cancel_and_refund`. The unused-service case: the
     bill is withdrawn **and everything paid for it goes back**, in one
     `transaction.atomic` under `select_for_update`. A failure anywhere rolls
     back the cancellation too, so there is no state where the bill is
     withdrawn but the money never went back.

   **Cancel & refund returns all of it, never a chosen amount.** The refund is
   `refundable_for_charge` — what the charge still holds — and is not a
   parameter. A ₦4,000 test cancelled with ₦1,000 refunded used to leave the
   patient ₦3,000 in credit against a bill nobody owed; there is no
   patient-credit account for that money to sit in, and inventing one is a
   business decision nobody has taken. The amount a screen sends (`amount`,
   read as `expected_amount`) is a **confirmation**: anything else is refused
   before anything is written, `code: "full_refund_required"` with the current
   `refundable`, which also stops a stale screen acting on a figure another
   cashier has since changed. Part of a payment going back for a service that
   stays active is `refund_payment`, on the Refunds desk. A part-paid service
   refunds what was paid, and its unpaid remainder stops being owed with the
   rest of the bill.

   **And it proves it landed.** Afterwards the charge must hold nothing and the
   patient's balance must have fallen by exactly what the bill still owed
   (`_check_withdrawal`; `cancel_charge` makes the same check when no money
   moves). Either failing raises `UntraceablePayment` / `LedgerMismatch` and
   rolls the whole operation back. The status is written **compare-and-set**,
   so two cashiers pressing at once cancel once and refund once even on SQLite,
   where `select_for_update` does nothing.

   **A forgiven part of a withdrawn bill is not forgiven twice.**
   `services.ledger_totals` leaves discounts and waivers on a *cancelled* charge
   out of the credits: the whole charge has already left `total_charges`, and
   counting its ₦1,000 discount as well read as −₦1,000. The adjustment row
   stays and still reports; refunds on a cancelled charge still count, because
   that money did go back.

   **A refund never cancels, and a cancellation is never a refund.** Neither
   service calls the other behind the caller's back; the combined operation has
   to be asked for by name.

   **Cancelling a charge that is still holding money is refused** —
   `RefundRequired`, surfaced as `code: "refund_required"` with the amount.
   Without that guard `total_payments` runs ahead of `total_charges` and the
   patient reads as being in **credit**, which is rule 12's prohibition seen
   from the other side. `refunding=True` is how `cancel_and_refund` says the
   money is going back in the same transaction.

   **A charge-scoped refund comes off that charge alone.** `_reverse_allocations`
   takes an `only_charge`, and `_payment_slices_for` finds the payments that
   actually settled it — so cancelling an unused scan cannot reopen the
   consultation the same ₦5,000 payment also covered. `Charge.outstanding` is
   the patient-facing figure (zero once cancelled); `Charge.balance` is the
   charge's own arithmetic and is deliberately left alone, because an audit
   needs to read what was withdrawn.

   **Money no allocation explains is refused, never guessed.**
   `UntraceablePayment` (`code: "untraceable_payment"`) stops Cancel & refund
   and `refund_charge` before anything is written; no payment is ever chosen by
   patient, date or amount. The application cannot create that state —
   `record_payment` writes the allocations in the same transaction as
   `amount_paid`, and nothing outside `billing/services.py` writes `amount_paid`
   (an AST scan in `test_payment_allocation_integrity.py` holds it). Only
   history or a hand edit can, and `manage.py billing_integrity`
   (`billing/integrity.py`, read-only, `--strict` to fail a deployment) reports
   it with unallocated payments, patients in credit and ledger drift.
   `ChargeSerializer.untraceable_amount` tells the screen before anybody presses.

   **One decision, one bell**: the two services are called with `notify=False`
   and the combined event announces once, as "Service cancelled & refunded". It
   links to `/transactions`, which every recipient — reception included — can
   open.

   **Two desks, two role groups.** `/refunds` (*Refunds*, `REFUND_ROLES`) is
   money going back: find the patient, pick the payment, refund all or part of
   it through `RefundAction`, plus the refund register headed by
   `/refunds/summary/`. `/service-cancellations` (*Service Cancellations*,
   `CANCEL_ROLES`) ends the responsibility for a service never received: every
   patient's bills, narrowed to one patient picked from the dropdown patient
   picker (browse the list or type a number, name or phone), filtered by
   state, department and billed date (`?created_from` / `?created_to` — list
   only, never `get_object()`), with services a department **withdrew but that
   are still billed** pinned first (`billing/withdrawn.py`: a `lab_test` charge
   whose line or order is cancelled or gone — read off the service, never
   inferred from a description) and counted by `/charges/cancellation-summary/`.
   `CANCEL_ROLES` holds the same people as `REFUND_ROLES` today and is named
   apart on purpose: Cancel & refund requires **both**, so reaching the
   cancellations desk is never on its own a way into the drawer. Both desks
   render the one `CancelServiceModal` (cancel / cancel & refund, no amount
   field) and `RefundModal` (payment refunds only) — there is no second
   implementation. The contextual actions on Transaction History, the Billing
   counter and the chart's Billing tab still work and reach the same services.
   Held by `apps/billing/tests/test_cancellation.py`,
   `test_cancel_and_refund_hardening.py`, `test_service_cancellations_desk.py`,
   `test_payment_allocation_integrity.py` and
   `frontend/src/pages/{Refunds,ServiceCancellations}.test.jsx`.

39. **The pharmacy POS is a second workflow on the same shelf, never a second
   pharmacy.** Doctor → prescription → pharmacist dispensing (rule 6) is
   untouched; the walk-in till (`/pharmacy/pos`) sits beside it. `apps/sales`
   holds only what had no model before — `PosRegister` (a till session),
   `Sale` + `SaleLine` (the receipt), `SaleItem` (the batch each unit came off)
   and `SaleReturn` + `SaleReturnLine` — and every row is written by
   `sales/services.py`. The API is read-only apart from POST actions.

   - **Stock** leaves through `inventory.services.consume_fefo`: the
     dispensing location only, earliest expiry first, expired lots excluded,
     under `select_for_update`, one `StockMovement(reason="sale")` per batch
     naming the `POS-` reference. There is no POS quantity anywhere: 100 on the
     shelf, 5 dispensed, 3 sold is 92, and both movements are in the log.
   - **Money** is one ordinary `Payment` per completed sale
     (`billing.services.record_pos_payment`, channel `pharmacy`). A **walk-in
     customer** is `Payment.patient = NULL`: no patient row is created, no
     charge, no ledger. A **registered patient** gets a Pharmacy charge
     (`source_type="pos_sale"`), the discount as an `Adjustment`, and the
     payment allocated to that charge alone — never spread over older bills.
     Collections, Total Facility Revenue and the Pharmacy department's
     *Received* see POS money without being told; a walk-in payment is placed on
     Pharmacy through its sale. `reporting.pos_sales` is the till's own block
     (gross − discounts = paid; `payments` must equal `paid`, or `reconciles`
     is False), shown on `/finance` and behind `/sales/summary/`.
   - **Discounts** are rule 13's approvals: `POS_DISCOUNT_ROLES` (cashier,
     accountant, admin — a pharmacist sells but cannot discount), a mandatory
     reason, product prices never changed. A line's own discount comes off that
     line first; a sale-wide percentage comes off what is left. No line goes
     below zero and the total discount must stay below the subtotal. **How
     large one may be, and who authorises an unusual one, is rule 42.**
   - **Returns** (`POS_RETURN_ROLES`: cashier + admin) are taken against a
     completed sale, never by editing it. The medicine goes to the
     `returns-quarantine` location (seeded by `inventory/0007`), which is
     neither receiving nor dispensing, so nothing there can be sold until
     somebody transfers it back or counts it out. The money goes back through
     `refund_payment(pos_return=True)` — the Refunds desk refuses a POS payment,
     because refunding there would leave the medicine unaccounted for. A
     registered patient's charge takes the goods back in its own column,
     `Charge.amount_returned` with `Adjustment(kind="return")` (rule 25), so the
     ledger closes; the report's reconciliation identity subtracts returns.
   - **A register reconciles money that exists** — float + cash sales − cash
     refunds = expected cash — and never creates any.
   - **No notifications.** Rule 35 announces changes to what a patient owes; a
     POS sale changes nobody's balance. Every sale, discount, return and
     register variance writes an `AuditLog` row instead.

   Held by `apps/sales/tests/test_pos.py` and
   `frontend/src/pages/PharmacyPOS.test.js`.

40. **A stock count can be a spreadsheet, and it is still a count.**
   `GET /stock-counts/export/` is the count sheet as a CSV (a line per batch per
   location, keyed by the stock record's Line ID); `POST /stock-count-imports/`
   validates an upload and keeps a **preview** — nothing moves — and
   `POST …/<id>/apply/` posts it through `post_stock_count`, one `StockCount`
   per location, every difference a `StockMovement(reason="stock_count")`:
   system 100 counted 92 is −8, never an overwrite (`inventory/count_csv.py`).
   A file with one problem applies nothing; stock that moved since the export is
   a conflict, checked again under lock at apply; an import applies once and
   the same bytes are recognised; a failure part-way rolls the whole import
   back. **Who counts where** is `count_csv.countable_locations`: admin and the
   inventory manager anywhere, a pharmacist the dispensing shelf — checked for
   the uploader and again for whoever confirms. Both workspaces render the one
   `CountImportExport` panel from `StockPanels.jsx` (rule 29). Held by
   `apps/inventory/tests/test_count_csv.py` and
   `frontend/src/components/CountImportExport.test.jsx`.

41. **One category on the product, read by everything, deciding nothing.**
   `Item.category` → `ItemCategory` is the single relation (rule 31), so there
   is no category text on a batch, a stock record, a prescription, a sale line
   or a movement: re-file a drug and the stock screen, the movement log, the
   doctor's picker, the dispensing queue and the till all move with it, because
   there is nothing else to update. `inventory/migrations/0008` seeds the
   twenty-three groups a pharmacy starts with — **adopting** an existing
   spelling through its `ALIASES` rather than creating a second row beside it
   (the same adopt-by-identity rule 34 applies to departments), never renaming
   or reactivating what an administrator has set, and **classifying no
   product**: a drug nobody has filed keeps no category until somebody who
   knows what it is says so.

   **It is a label, never a gate.** Dispensing still resolves stock, expiry,
   FEFO and location exactly as rules 6, 8 and 30 describe; a retired category
   is a closed list for new filing, not a block on medicine, and `POST
   /api/adjustments/`-shaped money is untouched by any of it.
   `test_categories.py`'s last class dispenses, records every figure — charge,
   payment, allocation, ledger — then re-files the product and retires its
   category, and asserts the whole dict is unchanged.

   **Where it is read**: `/stock-records/` and `/stock-movements/` carry it and
   filter on `?batch__item__category=`; `/items/` filters on `?category=` and
   searches `category__name`, for the stock screens and the doctor's picker
   alike (rule 7 still holds — the doctor gets the category and never a count);
   `/prescriptions/` carries `item_category` for the dispensing queue;
   `/sales/products/` carries the POS chips, **built from the whole catalogue**
   rather than from the sixty results beside them, and served from there rather
   than from `/item-categories/` because a cashier works the till and is not in
   `STOCK_ROLES` — sending the POS to the configuration endpoint would widen
   who reads the catalogue in order to draw a row of buttons.
   `/sales/summary/`'s `categories` block decomposes the till's own figures and
   never recounts them: the categories' `net` sums to `sales.total`, and a
   product with no category is reported as **Uncategorised** rather than
   dropped, the way `reporting.department_for` never drops a charge.

   **The CSV count recognises a category; it never invents one and never
   applies one.** `count_csv.normalise_category` drops case and punctuation, so
   "Pain Relief", "pain relief" and "Pain-Relief" are one group — and is
   deliberately not fuzzy, so "Pain Relif" resolves to nothing, is reported by
   name in `summary.unknown_categories`, and creates no row. A cell naming a
   *real* category that is not that product's is refused too: a count sheet
   moves a quantity and nothing else, so re-filing a product is done under
   Products and not in a spreadsheet. Held by
   `apps/inventory/tests/test_categories.py`,
   `test_category_end_to_end.py` and `apps/sales/tests/test_pos.py`'s
   `PosCategoryTests`.

42. **A POS discount has a ceiling, and above it somebody else says yes.**
   Rule 39 already had the shape — a line's own discount, a sale-wide one on
   top, `POS_DISCOUNT_ROLES` only, a mandatory reason, and the product's price
   never changed. What it had no way to express was *how much*: the only
   ceilings were 100% and "something has to be paid", written into
   `sales/services.py`. `apps/sales/discount_policy.py` is those ceilings moved
   to where an administrator sets them, plus the approval that was missing.

       limit  — what a cashier may give on their own
       max    — what nobody passes, approved or not

   Both live on `core.HospitalSettings` (rule 31's singleton — **not** a second
   configuration system), as a percentage pair and a naira pair, beside
   `pos_discounts_enabled`, `pos_discount_types`, and the preset percentages
   and reasons the dialog offers. **The defaults are the behaviour that was
   there before** — 100%, no fixed ceiling, both kinds — so a hospital that
   never opens the screen sees no change.

   **The limit is judged on the amount, never on the wording.** A percentage
   limit that only read `type="percent"` would be walked past with a fixed sum:
   ₦500 off a ₦1,000 line is 50% however it was typed. So `check_amount` runs
   on the **priced** discount against the line it comes off, in
   `_price_discounts`, after FEFO has priced the cart — the first moment either
   number exists — and each line is judged separately so an over-limit one is
   refused by name.

   **An approver is authenticated, never asserted.** `approver_for` takes a
   supervisor's own username and password from the till, authenticates them,
   and checks `POS_DISCOUNT_APPROVAL_ROLES` (accountant + admins — a cashier is
   deliberately *not* on it, so nobody authorises their own). A user id in a
   request body would be an authorisation every cashier could grant themselves.
   An accountant or an administrator working the till approves by being who
   they are. `Sale.discount_approved_by` is written **only where the policy
   required one**, so the column means "this needed authorising" rather than "a
   manager was present".

   Three refusals, and the till tells them apart because they are different
   conversations: `discounts_disabled` / `discount_type_not_allowed` (400),
   `discount_over_maximum` (400 — no supervisor can clear it), and
   `discount_needs_approval` / `discount_approval_failed` (403 — one can). The
   POS opens its authorisation prompt on the last pair and refuses flatly on
   the rest.

   **Nothing else moves.** A discount is money, not medicine: the quantity that
   leaves the shelf, FEFO, the expiry exclusion, the location and the
   `StockMovement` are all exactly what they were. For a registered patient the
   charge keeps its face value and the discount is an `Adjustment` against it
   (rule 25); a return refunds **what was paid**, not what was billed, so a
   ₦4,000 line discounted to ₦3,000 gives back ₦3,000 and nobody lands in
   credit. And rule 39's "no notifications" is unchanged — a POS sale changes
   nobody's balance — so a discount writes an `AuditLog` row instead, now
   carrying **per line** the price, quantity, gross, discount as asked and as
   priced, and the net, because a receipt with one over-limit line among five
   read as a single number before.

   **And it is reachable from the cart, which is the only place it matters.**
   The engine above was complete while the screen in front of it was not: the
   cart's checkboxes were gated on `cart.length > 1`, so a cashier with one
   item on the till could not select it; the only per-line affordance was a
   `size="xs"` text link; and a **pharmacist** — who may work a till but never
   discount — saw none of it and no reason why. The cart is now Odoo-shaped:
   every line carries a checkbox with a selected state, "Select all" sits above
   the list, and one prominent **Discount** button in the action row beside
   Hold / Clear / Take Charge discounts the ticked lines. With nothing ticked
   it is disabled and says "Tick an item to discount it", with the sale-wide
   discount kept beside it as the separate thing it is. A pharmacist is told
   who may give one rather than shown an empty cart. Totals always carry a
   **Discount** row — at zero as well — and **Take Charge** spends
   `totals.total`, so the payment dialog can never open on the undiscounted
   figure. Selecting is how a line is edited too: one selected line opens the
   dialog pre-filled, and Remove restores its own price, which was never
   overwritten.

   The frontend mirror is `components/posDiscountPolicy.js` (the pure-module
   pattern `refundPolicy.js` set), and it refuses early only to spare a round
   trip. `/sales/summary/`'s `discounts` block reports the same figure split by
   reason, by cashier and by type, with what needed authorising called out;
   every grouping sums to `sales.discounts`, which `reconciles` asserts. Held by
   `apps/sales/tests/test_pos_discounts.py`,
   `frontend/src/components/posDiscountPolicy.test.js`,
   `frontend/src/pages/PharmacyPOS.discounts.test.jsx` (the cart walk: select,
   discount, recalculate, take payment) and
   `frontend/src/components/PosReceipt.discount.test.jsx`.

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

- **Notifications are archived, never deleted.** There is no DELETE on
  `/notifications/`, and `NotificationAdmin.has_delete_permission` is False:
  what somebody was told, and when, is kept. Archiving stamps `archived_at`
  (read-only on the serializer) and takes the row out of the inbox —
  `POST /notifications/<id>/archive/`, `/archive_selected/` (`{"ids": […]}`,
  refused whole with 403 if any id is somebody else's) and `/archive_all/`
  (the caller's inbox only, like `mark_all_read`); `/<id>/unarchive/` puts one
  back. `GET /notifications/` is the inbox and `?archived=true` the archive,
  and that split is applied to `list` only, never `get_object()`, so an
  archived row can still be marked read/unread and restored. Read/unread and
  archived are independent. The bell, the dashboard and the overview's `unread`
  count `Notification.objects.active()` — an archived notification never
  rings. The page is `/notifications?tab=archived`; selection and "Archive
  all" exist only on your own inbox, never on the admin's everyone view. A
  side effect worth knowing: Django admin refuses to delete a staff account
  that holds notifications, because the cascade would delete them —
  deactivate the account instead. Held by
  `apps/core/tests/test_notification_archive.py` and
  `frontend/src/pages/Notifications.test.jsx`.

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
  ("Last, First"). `variant="dropdown"` is the same picker as a select-style
  button that stays closed until clicked and opens a panel with the search box
  and the list — the Refunds and Service Cancellations desks use it; reach for
  the prop, not a copy. Its panel is portalled into `<body>` at a fixed
  position, because `Card` is `overflow-hidden` and clipped the list away. `PatientsList.jsx` keeps a plain filter box instead — it
  is a list being narrowed, not a value being chosen.
- New health-record tiles: copy `AddAllergyModal.jsx`'s structure, wire the
  new modal into `PatientDetail.jsx`'s `TileLoader` the same way Allergies
  is wired (see the `hasModal` branch — extend it per tile as you add
  modals, don't rewrite the loader).

## Setup / running locally

See `README.md` for install steps. Needs Postgres + Redis running (or
adjust `.env`). `ANTHROPIC_API_KEY` required only for the `ai_agents` app.

**Frontend tests: `npm test` (vitest + Testing Library, jsdom).** The suite
lives beside what it tests (`src/**/*.test.{js,jsx}`), with
`src/test/harness.jsx` providing the providers a component actually gets —
router, TanStack Query, the toaster, and an auth context holding whichever role
the test is about. `AuthContext` is exported for that reason and for no other;
`AuthProvider` is still the only thing the application renders. Reach for a
pure module and a plain unit test where the decision can be lifted out of the
DOM — `refundPolicy.js` is the pattern — and a component test only for what
genuinely needs rendering.

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
  30) — **Stock on hand** (product / category / SKU / unit / location / batch /
  expiry / quantity / status, filterable by location and by category and
  searchable across all four, with per-shelf count and write-off), **Receive**
  (into the Main Store by default), **Transfer** (Main Store → Pharmacy: pick
  batches off the source shelf, one document, its own `TRF-` reference),
  **Physical count** (per location — system qty, counted qty, difference per
  line, posted as one `CNT-` document) and the **Movement log** (filterable by
  location and kind, showing the transfer reference on both halves). Every
  quantity change goes through `inventory/services.py`, leaves a
  StockMovement, and cannot be made any other way.
- **Financial reporting** (`/finance`, `FinanceReport.jsx`) — the cash desk's
  and administration's revenue view over any date range: Today / Yesterday /
  This week / This month / Last month / This year / a custom From–To. One
  `GET /finance/report/` behind all of it (rule 33), so nothing is summed in
  the browser. Total revenue collected, part payments, transactions, gross
  charges, discounts, waivers, net due and outstanding; revenue collected per
  department as a chart; the payment-method breakdown, which reconciles to the
  collected total; the department table with its Total row, every row of which
  reconciles; and the period's transactions — time, patient (by UUID and
  hospital number, never a pk), department, service, due, discount, waiver,
  paid, balance, method and settlement status. The dashboards' money cards
  open it on the period they count.
- Tests: 1,070 passing, 1 skipped (backend; frontend `npm test`: 174) — the PostgreSQL-only two-thread race in
  `test_cancel_and_refund_hardening.py` (`./venv/bin/python manage.py test` — the venv is at
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
  every role and against an anonymous caller — plus the financial report
  (`apps/billing/tests/test_financial_report.py`): that a discount and a
  waiver each add nothing to revenue, that a part payment is counted as
  collected *and* named as part-paid, that the reconciliation closes per
  department and down the Total row after every kind of settlement, that a
  department is read off the charge rather than guessed, that cash is
  attributed through the allocation to the bill it settled, that the method
  breakdown reconciles with the collected total, that the write-off register
  and the charge cohort are allowed to differ and the unlinked amount says by
  how much, that each preset asks a different question of the same rows, and
  that the whole report costs the same number of queries on 26 charges as
  on 1.

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

- **Prescriptions carry their directions.** Beside the dose, a script line
  has frequency, duration, route (`Prescription.ROUTE_CHOICES`) and notes, and
  a product has an optional strength and dosage form. `components/
  prescriptionDirections.js` is the one place they are joined for the queue,
  the chart and the printed sheets; the doctor's picker still shows
  availability, never a count (rule 7).
- **Pharmacy POS and the spreadsheet count** — rules 39 and 40. The Pharmacy
  navigation group opens each job directly (`/pharmacy?tab=…`, POS, Sales,
  Returns & Refunds); `AppShell.navItemActive` is what tells tabs of one page
  apart.

Not yet built:
- Partial dispensing. `dispense_prescription` fills a line whole or refuses it;
  a part-fill needs its own status and charge rule before anyone adds it.
- A quarantine desk. Returned POS stock is released or destroyed with the
  existing transfer and count screens; there is no inspection workflow of its
  own.
- The on-screen `/stock-counts/` endpoint still accepts any location from any
  STOCK_ROLE, as it always has; only the CSV import applies
  `countable_locations`. Narrowing the older endpoint is a separate decision.
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
