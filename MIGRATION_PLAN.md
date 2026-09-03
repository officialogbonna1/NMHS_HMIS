# HMIS refactoring plan

The prototype has been retained and extended rather than replaced. The implementation is organized around a single patient journey:

1. `patients` remains the master patient record, with a reception-safe demographic serializer.
2. `departments` defines configurable teams and billable services.
3. `workflow` opens visits and routes patients into department queues.
4. `clinical`, `diagnostics`, and `pharmacy` capture care events; diagnostics and dispensing create ledger charges in their transactions.
5. `billing` owns immutable financial events and derives the patient balance.
6. `inpatient` controls wards, beds, transfers, admissions, and discharge summaries.
7. `core` supplies append-only audit records and user notifications.

Database migrations are additive. Before a production deployment, back up the database, set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, PostgreSQL settings, email/SMS delivery, and per-role permission assignments. Run `python manage.py migrate` only after reviewing the generated migrations.
