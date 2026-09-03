# HMIS — Hospital Management Information System

Django + DRF backend, React (Vite) frontend, Celery for scheduled alerts,
Anthropic API for AI agents. Built for ~50 users; scales comfortably on a
single VPS.

## Structure

```
backend/hmis/
  manage.py
  hmis/                 # settings, urls, celery config
  apps/
    accounts/            # custom User model + role permissions
    patients/             # Patient + the 9 health-record tiles
    clinical/             # Vitals & ConsultationNote (lock-after-save)
    inventory/             # Item, Batch (expiry-tracked), StockMovement
    pharmacy/                # Prescription + atomic dispense service
    sales/                     # Sale, SaleItem with discounts
    appointments/                # Calendar/appointments
    ai_agents/                     # Claude API integration points
    core/                            # shared mixins (LockedRecordMixin)

frontend/
  src/pages/       # PatientsList, PatientDetail, PrescribeDrug, InventoryDashboard
  src/components/  # HealthRecordTile
  src/api/client.js
```

## Key design decisions (from our discussion)

- **Roles**: `accounts.Role` (admin/doctor/nurse/reception/pharmacist) + DRF
  permission classes in `accounts/permissions.py`.
- **Lock-after-save**: `core.mixins.LockedRecordMixin` — used by `Vitals`
  and `ConsultationNote`. First save locks the record; only admin can pass
  `admin_override=True` to edit afterward. Doctors can only edit their own
  notes before they lock (enforced in `clinical/views.py`).
- **Prescribing → stock deduction**: `pharmacy/services.py
  create_prescription_and_dispense()` — wraps the whole thing in
  `transaction.atomic()` with `select_for_update()`, dispenses FEFO
  (earliest-expiring batch first), and writes a `StockMovement` audit row
  per deduction.
- **Doctor sees availability, not counts**:
  `inventory.ItemForPrescribingSerializer` returns `available: true/false`
  instead of raw quantity for the doctor's drug picker.
- **Low stock / expiry alerts**: `inventory/tasks.py`, scheduled nightly
  via Celery Beat (see `CELERY_BEAT_SCHEDULE` in settings).
- **AI agents**: `ai_agents/services.py` — note summarizer, drug
  interaction advisory check, reorder-list drafting. All advisory only;
  never auto-blocks a clinical decision.

## Setup

### Backend
```bash
cd backend/hmis
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DB creds + ANTHROPIC_API_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
Requires PostgreSQL and Redis running locally (or point `.env` at hosted
instances). Celery worker + beat, once you're ready for background alerts:
```bash
celery -A hmis worker -l info
celery -A hmis beat -l info
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```
Vite proxies `/api` to `http://localhost:8000` (see `vite.config.js`).

## Not yet built (next steps)
- Auth screens (login, token storage) — currently assumes a token in
  `localStorage.authToken`
- "Add" modals for each health-record tile (the manual's add/edit panels)
- Sales/discount checkout UI
- Appointments calendar UI
- Wiring `ai_agents/services.py` into actual views/buttons
- Tests
