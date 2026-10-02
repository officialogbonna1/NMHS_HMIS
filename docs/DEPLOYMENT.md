# NMHS-HMIS-V1.0 — Deploying on Render.com

This guide deploys the HMIS on [Render](https://render.com) using the Blueprint
in the repository root (`render.yaml`). It describes the configuration in the
repository as it now stands. No real secret appears here; every credential is a
placeholder.

> **Nothing here has been deployed or tested on Render yet.** The configuration
> has been tested locally against Render's documented behaviour (see §11). Treat
> the first deployment as a rehearsal with test data before any patient is
> registered on it.

---

## 1. What changed in the code, and what you do in the dashboard

**Already done in the repository (code / configuration):**

| Area | Implementation |
|---|---|
| Environment selection | `DJANGO_ENV=development` (default: SQLite, no setup) or `production` — `backend/hmis/hmis/environment.py` |
| Database | Production: PostgreSQL from `DATABASE_URL` (Render's form) **or** `DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT`; missing → refuses to start; **never SQLite** |
| Render hostnames | `RENDER_EXTERNAL_HOSTNAME` / `RENDER_EXTERNAL_URL` (set by Render) are added to `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` automatically |
| HTTPS | Render terminates TLS and sends `X-Forwarded-Proto`; `SECURE_PROXY_SSL_HEADER`, HTTPS redirect, secure cookies and HSTS are on in production (no redirect loop — tested) |
| Static files | `collectstatic` at build; served by **WhiteNoise** in production (compressed, cache-busted) |
| Uploaded documents | Production must choose `DJANGO_MEDIA_STORAGE`: `cloudinary` = **private** Cloudinary assets (`authenticated`), delivered only through **expiring** private download links (`apps/core/storage.py`). `filesystem` is **refused on Render** |
| Client IP (login lockout) | `HMIS_CLIENT_IP_HEADER=CF-Connecting-IP` on Render (Render's proxy appends to `X-Forwarded-For`, so its first hop is client-controlled) |
| Django Admin | Optional `HMIS_ADMIN_ALLOWED_NETWORKS` CIDR allow-list (404 for everyone else) |
| CORS | `CORS_ALLOWED_ORIGINS` = the static site's `https://` origin; production refuses `http://` |
| Frontend | `VITE_API_BASE_URL` (build time) points the separately hosted React app at the API; unset = local `/api` proxy |
| Deploy gate | `manage.py check --deploy` errors `hmis.E001`–`E012` until production is secure (E011/E012 are Render-specific) |
| Blueprint | `render.yaml`: API, static site, PostgreSQL, Key Value, Celery worker, one Celery Beat |

**You do in the Render dashboard / elsewhere** (§3–§8): create and secure the
Cloudinary account, apply the Blueprint and type the secrets, check plans and prices,
create the first administrator, add custom domains, set up email (optional),
and run the smoke tests in §10.

---

## 2. Architecture on Render

```
                   ┌────────────────────────────┐
  Browser ─HTTPS─► │ hmis-web   (Static Site)    │  React build (frontend/dist)
     │             └────────────────────────────┘
     │  API calls (Authorization: Token …, CORS)
     ▼
  ┌────────────────────────────┐  private network  ┌─────────────────────────┐
  │ hmis-api   (Web Service)   │ ────────────────► │ hmis-db   (PostgreSQL)  │
  │ Gunicorn hmis.wsgi         │ ──────┐           └─────────────────────────┘
  │ WhiteNoise /static/        │       │           ┌─────────────────────────┐
  │ /healthz/  /admin/  /api/  │       ├─────────► │ hmis-redis (Key Value)  │
  └────────────┬───────────────┘       │           └─────────────────────────┘
               │ expiring links        │                     ▲
               ▼                       │   ┌─────────────────┴──────────┐
  ┌────────────────────────────┐       │   │ hmis-celery-worker (worker)│
  │ Cloudinary (outside Render)│ ◄─────┘   │ hmis-celery-beat  (worker, │
  │ authenticated raw assets   │           │   exactly one instance)    │
  └────────────────────────────┘           └────────────────────────────┘
```

* **No WebSockets / ASGI.** The application has no `asgi.py`, no Channels and
  no WebSocket client; live data is polled (bell 30 s, station queues 60 s).
  WSGI under Gunicorn is the right server, and nothing needs WebSocket setup.
* **Two origins.** The React app (`hmis-web…`) and the API (`hmis-api…`) are
  separate. The API authenticates with a token in the `Authorization` header —
  never a cookie — so CORS is all that is needed; Django Admin lives on the API
  origin, so the React route `/admin` and Django's `/admin/` no longer collide.

### Exact commands (from `render.yaml`)

| Service | Build | Pre-deploy | Start |
|---|---|---|---|
| `hmis-api` (rootDir `backend/hmis`) | `pip install -r requirements.txt && python manage.py collectstatic --noinput` | `python manage.py migrate --noinput` | `gunicorn hmis.wsgi:application --bind 0.0.0.0:$PORT --workers ${WEB_CONCURRENCY:-2} --timeout 60 --forwarded-allow-ips="*" --error-logfile -` |
| `hmis-web` (rootDir `frontend`) | `npm ci && npm run build` → publish `./dist` | — | — (static) |
| `hmis-celery-worker` | `pip install -r requirements.txt` | — | `celery -A hmis worker -l info --concurrency 2` |
| `hmis-celery-beat` (1 instance) | `pip install -r requirements.txt` | — | `celery -A hmis beat -l info --schedule /tmp/celerybeat-schedule` |

* **Migrations run once per deploy**, in Render's pre-deploy step on a separate
  instance, before the new release takes traffic. A failed migration cancels the
  deploy and the previous release keeps running. They are never in the start
  command, where every instance would migrate.
* **`collectstatic` is in the build**, not the pre-deploy step: the pre-deploy
  instance's files never reach the running service.
* Health check path: `/healthz/`.

### Plans and limits (checked against Render's docs; verify current prices)

| Item | Requirement |
|---|---|
| Pre-deploy command (migrations) | **Paid** web service only |
| Background workers (Celery worker, Beat) | **No free tier** |
| Free PostgreSQL | **Expires after 30 days**, 1 GB, no backups — not for patient data |
| Free Key Value | **Does not persist** — a restart drops queued tasks |
| Free web service | Spins down after 15 min idle, no shell, no pre-deploy |
| Persistent disks | Paid only, one instance, no zero-downtime deploys — **not used** (Cloudinary instead) |
| Inbound IP rules for web services | Scale/Enterprise orgs only — hence `HMIS_ADMIN_ALLOWED_NETWORKS` |
| Log retention | Hobby 7 days, Pro 14, Scale/Enterprise 30 (log streams for longer) |
| Postgres point-in-time recovery | Hobby: past 3 days; Pro+: past 7 days |

`render.yaml` uses the smallest paid sizes in Render's Blueprint reference
(`0.5c-512mb` services, `0.5c-1g` database, `256mb` Key Value) and region
`frankfurt` (all resources must share a region for the private network). Change
them before applying if you need more capacity or a different region.

---

## 3. Before you start: Cloudinary for uploaded documents

Uploaded results (Medical Tests & Diagnostics files, which unit reports are
filed under, `medical_tests/YYYY/MM/`) are patient documents. Render's service
filesystem is wiped on every deploy and Render has no storage of its own for
them, so production keeps them in **Cloudinary**, privately.

### How the documents stay private (implemented in `apps/core/storage.py`)

* Each file is uploaded server-side (signed with the API secret) as a **`raw`**
  asset with delivery type **`authenticated`**, under the folder
  `nmhs-hmis/media/` (`CLOUDINARY_FOLDER`). An authenticated asset is refused
  to anyone without a signature: its ordinary `res.cloudinary.com` address does
  not work. `raw` keeps images, PDFs, Word and Excel files byte-for-byte and
  allows no transformations.
* The API **never returns a permanent address.** Every document link is a
  Cloudinary **private download URL** (`https://api.cloudinary.com/v1_1/<cloud>/raw/download?…`)
  with an `expires_at` time — `CLOUDINARY_LINK_EXPIRY_SECONDS`, default **900
  seconds (15 minutes)**. Cloudinary checks the expiry itself on every request,
  and those responses are not cached on its CDN. After expiry the link fails;
  reopening the record in the HMIS issues a fresh one.
* Ordinary **signed CDN URLs are not used**: Cloudinary's signatures on those
  never expire. Time-limited CDN *tokens* need Cloudinary's Advanced plan or
  higher; the download URL used here is available on every plan.
* Links are created only by the serializers and views that already decide who
  may read the record, so **the existing permissions decide who gets a link** —
  unchanged. Upload forms and the API's response shape are unchanged too.
* A link contains the cloud name, the **API key** (an identifier) and a
  signature. The **API secret** is never sent to the browser, never put in a
  link, never logged (the log redaction filter knows it), and must never be put
  in a `VITE_*` variable.

### Set up the Cloudinary account

1. Create a Cloudinary account (or use the hospital's) and a **product
   environment** for the HMIS only. Check Cloudinary's plan limits — in
   particular the maximum file size for raw files on your plan, which caps the
   size of a scan a desk can upload — and its data-protection terms and storage
   location for patient documents. **This is your decision.**
2. In the Cloudinary Console, open the API keys page and note the **cloud
   name**, an **API key** and its **API secret**. If the console lets you create
   additional keys, create one used by the HMIS alone, so it can be revoked
   without affecting anything else.
3. Secure the account: enable two-factor authentication for every console user,
   and limit who has console access — a console user can see every document.
4. Review the account's **upload presets**: the HMIS uploads only through signed
   server-side calls and needs none. Do not keep an *unsigned* preset, which
   would let anybody upload to the account.
5. Leave the Security setting **"Allow delivery of PDF and ZIP files"** as it
   is. Free accounts block PDF delivery on CDN URLs; the HMIS uses the API
   download endpoint instead, which is reported to serve them regardless. If
   PDFs nevertheless fail to open in testing (§10), turning the setting on does
   **not** make HMIS documents public — they stay `authenticated` — but test
   again afterwards.

Nothing is configured in Cloudinary by the Blueprint or by the HMIS; it only
uploads, links and deletes assets under `CLOUDINARY_FOLDER`.

## 4. Environment variables

The application reads the process environment only. On Render, values come
from the Blueprint, Render itself, and what you type into the dashboard.

**Set by the Blueprint** (group `hmis-settings`, shared by api/worker/beat):
`DJANGO_ENV=production`, `PYTHON_VERSION=3.12.8`, `TIME_ZONE=Africa/Lagos`,
`DJANGO_MEDIA_STORAGE=cloudinary`, `CLOUDINARY_LINK_EXPIRY_SECONDS=900`,
`HMIS_CLIENT_IP_HEADER=CF-Connecting-IP`, `DJANGO_LOG_LEVEL=INFO`.
Wired automatically: `DATABASE_URL` (from `hmis-db`, internal URL) and
`REDIS_URL` (from `hmis-redis`, internal URL).

**Set by Render itself:** `RENDER`, `RENDER_SERVICE_TYPE`,
`RENDER_EXTERNAL_HOSTNAME`, `RENDER_EXTERNAL_URL`, `PORT`, `WEB_CONCURRENCY`
(if not set, Gunicorn uses 2 workers).

**You type them in (prompted when the Blueprint is applied):**

| Variable | Services | Value |
|---|---|---|
| `DJANGO_SECRET_KEY` | api, worker, beat — **the same value in all three** | 50+ random characters: `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` (Render's "generate" makes a 44-character value, which the HMIS refuses) |
| `CORS_ALLOWED_ORIGINS` | api | the static site's origin, e.g. `https://hmis-web.onrender.com` (comma-separate a custom domain later) |
| `HMIS_BASE_URL` | api, worker | the React app's URL (links in admin emails) |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | api, worker, beat — **the same values in all three** | from §3; the secret is a secret — only ever in Render's environment |
| `VITE_API_BASE_URL` | web (static) | the API's origin, e.g. `https://hmis-api.onrender.com` — **public**, compiled into the bundle; never a secret |

The worker and Beat need the storage and secret variables because they load the
same settings, which refuse to start without them.

**Optional — add on a service's Environment page when needed:**

| Variable | Service(s) | Purpose |
|---|---|---|
| `RESEND_API_KEY`, `HMIS_EMAIL_FROM`, `HMIS_ADMIN_EMAIL` | api **and** worker | admin email (sent by the worker; inline from the API if the queue is down) |
| `HMIS_ADMIN_ALLOWED_NETWORKS` | api | Django Admin only from these CIDRs, e.g. the hospital's public IP `/32` |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | api | a custom API domain (`api.example-hospital.org` / `https://api.example-hospital.org`) |
| `DJANGO_SECURE_HSTS_SECONDS` | api | lower (e.g. `3600`) for the first days; default one year |
| `DB_CONN_MAX_AGE` | api | connection reuse (default 60 s) |

**Do not set on Render:** `DJANGO_DEBUG` (true is refused), `DJANGO_LOG_DIR`
(files are lost; `check --deploy` warns `hmis.W002`), `DJANGO_MEDIA_STORAGE=filesystem`
(refused). `backend/hmis/.env.example` lists every variable with placeholders;
`frontend/.env.example` documents `VITE_API_BASE_URL`.

---

## 5. Step by step: first deployment

1. **Push the repository to GitHub** (private repository). Nothing in it holds a
   secret; `.env` files are gitignored.
2. **Set up Cloudinary** (§3) and keep the cloud name, API key and API secret
   ready — enter them only into Render, never into a file or a chat.
3. In Render: **New → Blueprint**, connect GitHub, pick the repository and
   branch. Render reads `render.yaml` and lists six resources. Review plans and
   prices on that screen.
4. **Enter the prompted values** (§4). Use the expected names: if the names
   `hmis-api` / `hmis-web` are free your URLs will be
   `https://hmis-api.onrender.com` and `https://hmis-web.onrender.com`; if
   Render adds a suffix, correct `CORS_ALLOWED_ORIGINS`, `HMIS_BASE_URL` and
   `VITE_API_BASE_URL` afterwards on each service's Environment page and
   redeploy (a static site must be **rebuilt** to pick up `VITE_API_BASE_URL`).
5. **Apply.** Render creates the database and Key Value, builds the API (install
   + `collectstatic`), runs `migrate` in the pre-deploy step (this also seeds the
   departments — including **Procedure** and **Theatre** as separate
   departments — stock locations, catalogues and price lists), starts Gunicorn,
   and waits for `/healthz/` to answer `healthy`.
6. **Check the API**: open `https://hmis-api.onrender.com/healthz/` →
   `{"status": "healthy"}`. Then `https://hmis-api.onrender.com/admin/` shows a
   styled Django Admin login (proves WhiteNoise).
7. **Run the deploy check** in the API's **Shell** tab (paid instances):
   `python manage.py check --deploy` — must exit with no errors (W005/W021 about
   HSTS subdomains/preload are expected while those stay off).
8. **Create the first administrator** in the API's Shell:
   `python manage.py createsuperuser` (production password rules apply:
   10+ characters, not common, not numeric). Then sign in to Django Admin →
   Users, open that account and set **Role = Super Admin** — without a role an
   account reaches nothing in the HMIS. Never put this password in an
   environment variable or a file.
9. **Open the app** at `https://hmis-web.onrender.com`, sign in with that
   account, and configure the hospital (User Manual, section 24 "Administration": Hospital
   settings, departments and their staff — including Procedure staff — users,
   wards, beds, prices).
10. **Celery**: the worker and Beat logs should show them connected to Redis
    (`celery@… ready`, `beat: Starting…`). From the API Shell:
    `celery -A hmis inspect ping` → `pong` from the worker.

### Custom domains

1. API: Settings → Custom Domains → add `api.example-hospital.org`, create the
   DNS record Render shows, wait for the certificate. Add the domain to
   `DJANGO_ALLOWED_HOSTS` and `https://api.example-hospital.org` to
   `DJANGO_CSRF_TRUSTED_ORIGINS`.
2. Frontend: add `hmis.example-hospital.org` to the static site the same way.
   Add `https://hmis.example-hospital.org` to the API's `CORS_ALLOWED_ORIGINS`
   (comma-separated with the onrender origin) and update `HMIS_BASE_URL`.
3. Set the static site's `VITE_API_BASE_URL=https://api.example-hospital.org`
   and rebuild it.
4. Once HTTPS has worked for a few days, consider HSTS include-subdomains /
   preload — only if **every** subdomain of the domain is HTTPS.

---

## 6. Celery and the single scheduler

* `hmis-celery-worker` runs the existing tasks: admin email
  (`apps.core.tasks.send_admin_email_task`, with its retries and de-duplication)
  and the two nightly stock tasks Beat schedules. Scale it if email backs up.
* `hmis-celery-beat` is the **only** scheduler: `numInstances: 1`, never
  autoscaled, never `-B` on the worker, never in the web service. Two schedulers
  would run each nightly task twice. Its schedule file lives in `/tmp` and only
  records last run times; losing it on a redeploy is harmless.
* Nightly tasks (`CELERY_BEAT_SCHEDULE`, every 24 h): `check_low_stock` and
  `check_expiring_batches`. As before, they compute lists only and send no
  notification; the dashboards compute stock alerts live.
* `hmis-redis` is private (`ipAllowList: []`) with `maxmemoryPolicy: noeviction`
  so queued tasks are never evicted. It also holds the login-lockout counts, so
  every Gunicorn worker shares one count. If it is down, the lockout fails open
  and email is sent inline — sign-in and clinical work continue.

---

## 7. Logging on Render

Everything goes to stdout/stderr, which Render shows on each service's
**Logs** tab. Every line carries a request ID:

```
2026-10-02 10:02:03 INFO hmis.request [3f2a…] GET /api/patients/ 200 41ms user=12
```

* The ID is returned in the `X-Request-ID` response header (readable by the
  frontend through CORS), carried into Celery tasks, and logged with every
  unexpected error and its `RF-` reference — search the Logs tab for either.
* **Never logged:** passwords (including what was typed as a username on a
  failed sign-in), tokens, `Authorization` headers, cookies, request bodies,
  query strings (patient searches travel in them), the secret key, database /
  Redis / storage credentials, signed document links, and no patient records,
  diagnoses, results, prescriptions, notes, addresses or phone numbers. A redaction filter scrubs
  credential shapes and the configured secret values from every message and
  traceback as a safety net. SQL is never logged in production.
* **Audit trail** is unchanged: the database `AuditLog` (Django Admin → Core →
  Audit logs), now including `auth.login` / `auth.logout`.
* **Retention:** Render keeps logs 7/14/30 days by workspace plan. For longer
  retention add a **log stream** (Workspace → Log Streams) to a provider you
  trust with operational logs.
* **Render's own HTTP request logs** (Pro workspaces and above) are written by
  Render's proxy, not the HMIS, and may include full URLs **with query
  strings** — i.e. patient searches. Restrict who can view logs in the Render
  workspace accordingly.

---

## 8. Backups, recovery and maintenance

**Database**

* Render PostgreSQL (paid) has **point-in-time recovery**: past 3 days (Hobby
  workspace) or 7 days (Pro+). Recovery creates a **new** database at the chosen
  time; check it, then point `DATABASE_URL` at it.
* **Logical exports** from the dashboard (paid) are kept 7 days — download them
  and store them encrypted off Render for longer retention.
* For your own daily dumps: temporarily add your trusted IP `/32` to the
  database's access control, then
  `pg_dump -Fc "<external database URL>" > hmis-$(date +%F).dump`, encrypt it,
  store it off Render, and remove the IP again.
* **Restore test monthly**: `pg_restore` a dump into a scratch database and
  compare a few counts (patients, charges, payments).

**Documents** — the documents live in Cloudinary, not in the database
backups. Check what backup / restore options your Cloudinary plan offers and
use them; the database holds each document's name, which is its Cloudinary
public ID under `CLOUDINARY_FOLDER`. The HMIS does not delete Cloudinary assets
when records are purged, so a restored database still finds its documents.

**Updating the HMIS**

1. Take a logical export (or note the PITR timestamp).
2. Merge to the deploy branch. Render rebuilds; `migrate` runs in pre-deploy;
   a failed migration cancels the deploy and the old release keeps serving.
3. Watch the API's Logs for `RF-` errors; open `/healthz/`.
4. Roll back with **Manual Deploy → a previous commit**. If a migration had
   already run, restore the database (PITR) to before the deploy first.

**Disaster recovery**: re-apply the Blueprint in a new workspace/region, restore
the database from PITR or a dump, point the storage variables at the same (or
restored) Cloudinary product environment, re-enter secrets, re-point DNS.

---

## 9. Security checklist

- [ ] All six resources on paid plans appropriate for patient data
- [ ] Database and Key Value `ipAllowList: []` (private network only)
- [ ] Cloudinary: dedicated product environment, 2FA for every console user, no unsigned upload presets, API secret only in Render
- [ ] `DJANGO_SECRET_KEY` unique, 50+ chars, identical on api/worker/beat
- [ ] `CORS_ALLOWED_ORIGINS` = only the HMIS frontend origin(s)
- [ ] `HMIS_CLIENT_IP_HEADER=CF-Connecting-IP` (else `check --deploy` E011)
- [ ] `check --deploy` passes in the API Shell
- [ ] Superuser given the Super Admin role; few Super Admins; strong passwords
- [ ] Optional `HMIS_ADMIN_ALLOWED_NETWORKS` set to the hospital's IPs
- [ ] Render workspace members limited; 2FA on for every member
- [ ] GitHub repository private; `.env` never committed
- [ ] The repository's tracked `db.sqlite3.bak-20260903-073530` and `dump.rdb`
      reviewed for patient data and removed from version control
- [ ] PITR window known; first logical export downloaded and a restore tested

---

## 10. Smoke-test the HMIS after deploying

With **test patients only**, walk each critical workflow once:

1. Reception: register a patient, print the patient card, bill a consultation,
   take a payment and print the receipt.
2. Nurse: accept the vitals route, record vitals + nursing note, send to doctor.
3. Doctor: open the chart, write a note, refer for a laboratory test and a
   **Procedure**, prescribe a drug.
4. Laboratory: accept, enter a result, submit, verify; doctor sees it on the chart.
5. Procedure staff (a doctor/nurse added to the **Procedure** department):
   accept, record with materials, Save & mark done; print the Procedure Record.
6. Health record: upload a document to Medical Tests & Diagnostics, open it from
   the chart (it opens through an `api.cloudinary.com/…/download?…` link), and
   confirm the same link **stops working** after `CLOUDINARY_LINK_EXPIRY_SECONDS`
   and that the asset's ordinary `res.cloudinary.com` address without a
   signature is refused.
7. Pharmacy: pay the prescription line, dispense; POS sale with receipt.
8. Cashier: refund, service cancellation, financial report.
9. Admissions: admit to a bed, discharge, print the discharge letter.
10. Sign out; confirm a refreshed page returns to the login screen.
11. Five wrong passwords lock the username for 5 minutes from that client.

Then delete the test patients (Super Admin, Django Admin purge) before go-live.

---

## 10a. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Deploy fails at start-up with `ImproperlyConfigured` | A required production variable is missing or unsafe | The log names the variable (never its value); set it on the service's Environment page and redeploy |
| Deploy cancelled in the pre-deploy step | `migrate` failed | Read the deploy log; the previous release keeps running. Fix, then redeploy |
| Health check failing, `/healthz/` says `unhealthy` (503) | The API cannot reach PostgreSQL | Check `DATABASE_URL` is wired from `hmis-db` and both are in the same region |
| `400 Bad Request` on the API's custom domain | Domain not in `DJANGO_ALLOWED_HOSTS` | Add it (the onrender.com host is automatic) |
| Browser console: CORS error | Static site origin not in `CORS_ALLOWED_ORIGINS`, or `http://` | Add the exact `https://` origin, no trailing slash |
| App loads but every request fails / goes to the static site | `VITE_API_BASE_URL` wrong or not rebuilt | Set it to the API origin and **rebuild** the static site |
| Django Admin login: CSRF error | Custom API domain not in `DJANGO_CSRF_TRUSTED_ORIGINS` | Add `https://<api domain>` |
| Django Admin 404 | `HMIS_ADMIN_ALLOWED_NETWORKS` excludes your address | Add your network or unset the variable |
| `check --deploy` reports `hmis.E011` | `HMIS_CLIENT_IP_HEADER` not set | Set `CF-Connecting-IP` |
| Uploading a document fails | Cloudinary credentials wrong, or the file exceeds the plan's raw file-size limit | Check the three `CLOUDINARY_*` values (same on api/worker/beat); check the plan limit |
| A document link answers an error | The link has expired (default 15 min) | Reopen the record in the HMIS for a fresh link |
| Admin email not sent | `RESEND_*` not set on **both** api and worker | Set them on both services |
| Celery idle / tasks not running | Worker or Beat not running, or `REDIS_URL` not wired | Check both workers' logs; `celery -A hmis inspect ping` from the API Shell |
| Users see "Something went wrong … RF-xxxxxxxx" | An unexpected error | Search the API's Logs for that reference |

## 11. What was tested locally (not on Render)

Tested before the switch to Cloudinary (S3-compatible storage at the time):

* Backend and frontend test suites; `check`, `check --deploy`,
  `makemigrations --check`; frontend production build.
* Production settings imported in a clean process with the Render variables:
  PostgreSQL from `DATABASE_URL`, onrender host/origin added, WhiteNoise
  selected, Celery worker starting with no hostname, refusal without a database.
* `collectstatic` with the production storage (162 files, manifest written).
* Gunicorn started with the production settings behind a simulated proxy:
  WhiteNoise serves Admin CSS (200); `/healthz/` answered `unhealthy` 503 with
  no database (and logs no credentials); plain HTTP → 301 HTTPS; HTTPS with
  `X-Forwarded-Proto` → no redirect loop; unknown Host → 400; CORS preflight
  allowed only for the static site's origin; HSTS, nosniff, `DENY`,
  `same-origin` and `X-Request-ID` present.

**The Cloudinary storage has not been run or tested** — not locally, not
against a real Cloudinary account. Its tests were written
(`apps/core/tests/test_production_settings.py`: `TheMediaStorage`,
`ThePrivateCloudinaryStorage`) but not executed; run them after
`./venv/bin/pip install -r requirements.txt` installs the `cloudinary` package.

Not tested: a real Render deployment, a real Cloudinary account, a real
PostgreSQL server, real Key Value, or Render's health checker. Do those in §5
and §10 with test patients before go-live.

---

## Appendix — local development (unchanged)

```bash
cd backend/hmis && ./venv/bin/python manage.py runserver      # SQLite, db.sqlite3
cd frontend && npm run dev                                      # Vite :5173, proxies /api and /media
```

Nothing has to be installed or set: no PostgreSQL, Redis, Cloudinary or Render.
`VITE_API_BASE_URL` stays unset, so the app calls `/api` through the Vite proxy,
and uploads stay in `backend/hmis/media/`. `pip install -r requirements.txt`
adds WhiteNoise and the `cloudinary` SDK, which development does not load.

The previous self-hosted (Nginx + systemd) setup still works with the same
code: set `DJANGO_MEDIA_STORAGE=filesystem`, serve `/media/` from a restricted
Nginx location, leave `HMIS_CLIENT_IP_HEADER` unset and have Nginx overwrite
`X-Forwarded-For`.
