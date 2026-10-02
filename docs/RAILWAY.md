# NMHS-HMIS-V1.0 — Deploying on Railway

An alternative to the Render deployment (`render.yaml`, `docs/DEPLOYMENT.md`),
which is unchanged. Both use the same code; nothing here affects Render or
local development.

> **Not deployed or tested on Railway.** The configuration was checked locally
> against Railway's documented behaviour (§11). Make the first deployment a
> rehearsal with test patients.

---

## 1. Architecture

```
            ┌──────────────────────────┐
 Browser ─► │ hmis-web  (Railpack:     │  React build served by Caddy
            │ Vite + Caddy)            │  (frontend/Caddyfile)
            └────────────┬─────────────┘
                         │ API calls (Authorization: Token …, CORS)
                         ▼
            ┌──────────────────────────┐  private network  ┌──────────────┐
            │ hmis-api  (Gunicorn)     │ ────────────────► │ Postgres     │
            │ /api/ /admin/ /healthz/  │ ───────┐          └──────────────┘
            └────────────┬─────────────┘        │          ┌──────────────┐
                         │ expiring links       ├────────► │ Redis        │
                         ▼                      │          └──────────────┘
            ┌──────────────────────────┐        │                ▲
            │ Cloudinary (outside      │ ◄──────┘   hmis-celery-worker
            │ Railway): private docs   │            hmis-celery-beat (ONE)
            └──────────────────────────┘
```

Six Railway services in one project: **hmis-api**, **hmis-web**,
**hmis-celery-worker**, **hmis-celery-beat** (this repository), **Postgres**
and **Redis** (Railway database services). No WebSockets (the app polls), so
no ASGI.

---

## 2. What the repository provides

| File | Purpose |
|---|---|
| `railway/api/railway.toml` | API: Dockerfile build, pre-deploy migration, Gunicorn, `/healthz/` |
| `backend/hmis/Dockerfile.api`, `backend/hmis/.dockerignore` | The API image: `python:3.12-slim`, `python -m pip install -r requirements.txt`, `collectstatic` at build, Gunicorn; no variables or secrets baked in |
| `backend/hmis/hmis/settings_build.py` | Build-only settings for `collectstatic` (development configuration + the production static-file storage) |
| `railway/worker/railway.toml` | Celery worker |
| `railway/beat/railway.toml` | Celery Beat (the single scheduler) |
| `railway/web/railway.toml` | React app: Railpack Vite build + Caddy, `/health` |
| `frontend/Caddyfile` | How Caddy serves the React app on Railway: index.html fallback + security headers |
| `backend/hmis/hmis/environment.py` | On Railway (`RAILWAY_PROJECT_ID`/`RAILWAY_ENVIRONMENT_ID` present): adds `RAILWAY_PUBLIC_DOMAIN` and `healthcheck.railway.app` to `ALLOWED_HOSTS`, `https://$RAILWAY_PUBLIC_DOMAIN` to CSRF origins; a service with no public domain (worker, Beat) needs no hostname; `DJANGO_MEDIA_STORAGE=filesystem` is refused |
| `backend/hmis/apps/core/checks.py` | `check --deploy` error `hmis.E012` on Railway unless documents are on Cloudinary |

Everything else is the existing production configuration: `DATABASE_URL`
parsing, `REDIS_URL` for Celery and the lockout cache, HTTPS behind a proxy,
WhiteNoise, Cloudinary private storage, request IDs and log redaction.

Railway config files **do not follow the service's root directory**: each
service's *Config File Path* is the absolute path from the repository root.
Config-as-code overrides the dashboard for the settings it names; it cannot set
variables (those are §5).

---

## 3. Services, root directories and commands

| Service | Root Directory | Config File Path | Build | Pre-deploy | Start |
|---|---|---|---|---|---|
| `hmis-api` | `/backend/hmis` | `/railway/api/railway.toml` | **Dockerfile** `Dockerfile.api`: `python -m pip install -r requirements.txt`, then `collectstatic` (custom Build Command must be empty) | `python manage.py migrate --noinput` | `gunicorn hmis.wsgi:application --timeout 60 --forwarded-allow-ips=* --error-logfile -` |
| `hmis-celery-worker` | `/backend/hmis` | `/railway/worker/railway.toml` | Railpack install | — | `celery -A hmis worker -l info --concurrency 2` |
| `hmis-celery-beat` | `/backend/hmis` | `/railway/beat/railway.toml` | Railpack install | — | `celery -A hmis beat -l info --schedule /tmp/celerybeat-schedule` |
| `hmis-web` | `/frontend` | `/railway/web/railway.toml` | Railpack: `npm` install + `vite build` | — | Caddy (Railpack), serving `dist/` with `frontend/Caddyfile` |
| `Postgres` | — | — | Railway database template | — | — |
| `Redis` | — | — | Railway database template | — | — |

* **Gunicorn** binds `0.0.0.0:$PORT` on its own when `PORT` is set (Railway sets
  it) and takes its worker count from `WEB_CONCURRENCY`, so the start command
  needs no shell expansion. The image's `CMD` is the same command; the config
  file states it too so a dashboard start command cannot override it.
* **Why a Dockerfile for the API:** the Railpack build failed with
  `python: not found` — Railpack had not detected a Python project, so it never
  installed Python. The image takes Python from `python:3.12-slim` instead, so
  nothing depends on detection. (The worker and Beat still use Railpack; if they
  hit the same error, they can use the same `Dockerfile.api` with their own
  start commands.)
* **Migrations** run once per deploy in Railway's pre-deploy step — a separate
  container, before the new release takes traffic. A failure stops the deploy
  and the previous release keeps serving. Never in the start command (every
  replica would migrate) and never per request.
* **Static files** are collected while the image is built. A Docker build gets
  **no** Railway variables, so it runs with `DJANGO_SETTINGS_MODULE=hmis.settings_build`
  — no secret or database needed — which writes the same hashed files and
  `staticfiles.json` manifest that WhiteNoise serves in production. (The
  pre-deploy container's files never reach the app, so it is not done there.)
* **Health checks:** API `/healthz/` (200 when the database answers). Railway
  sends `Host: healthcheck.railway.app` over plain HTTP; that host is allowed
  automatically and `/healthz/` is exempt from the HTTPS redirect. Railway uses
  the health check **only at deploy time**, not for continuous monitoring.
  Web: `/health` (Caddy answers 200).
* **Celery Beat: exactly one.** Leave `hmis-celery-beat` at one replica, never
  add `-B` to the worker, never run Beat in the API.
* Do **not** generate a public domain for the worker or Beat: without one the
  settings treat them as processes that serve no HTTP.

---

## 4. Plans and cost (Railway's docs, checked 2 October 2026)

Railway is **usage-based** and not free for this architecture:

| Plan | Price | Notes |
|---|---|---|
| Trial | one-time **$5** credit, up to 30 days | **Limited to 5 services per project** — this architecture has **6**; 1 GB RAM, shared vCPU |
| Free | **$1/month** credit | 0.5 GB RAM and 1 vCPU per service, 1 replica |
| Hobby | **$5/month**, includes $5 of usage | Pay for usage above that |
| Pro | **$20/month**, includes $20 of usage | |

Usage prices: RAM **$10/GB/month**, CPU **$20/vCPU/month**, volume storage
**$0.15/GB/month**, egress **$0.05/GB**. Postgres and Redis are services and are
billed for their own CPU, RAM and volume. Six always-on services will exceed
the Free plan's $1 credit; expect a Hobby subscription plus usage. Check
current prices and limits before creating anything.

To test on the **Trial** (5 services) you would have to drop one service —
e.g. host the React app elsewhere — which is a deployment decision, not a code
change. Cloudinary is billed separately by Cloudinary.

---

## 5. Variables

Railway's config files cannot set variables; set them in each service's
**Variables** tab. Use Railway **shared variables** (project settings) for
values the API, worker and Beat all need, and reference them with
`${{shared.NAME}}`.

### 5.1 Provided by Railway

| Variable | Where | Used for |
|---|---|---|
| `DATABASE_URL` | Postgres service | Reference it: `DATABASE_URL=${{Postgres.DATABASE_URL}}` on api, worker, beat (private network URL) |
| `REDIS_URL` | Redis service | Reference it: `REDIS_URL=${{Redis.REDIS_URL}}` on api, worker, beat |
| `PORT` | every service | Gunicorn / Caddy listen port |
| `RAILWAY_PUBLIC_DOMAIN` | services with a public domain | Added to `ALLOWED_HOSTS` and CSRF origins automatically |
| `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID` | every service | How the settings know they are on Railway |

(Use the service names your Postgres/Redis services actually have in the
`${{…}}` references.)

### 5.2 Required — enter them (api, worker and beat unless noted)

| Variable | Value |
|---|---|
| `DJANGO_ENV` | `production` |
| `DJANGO_SECRET_KEY` | 50+ random characters (`python3 -c "import secrets; print(secrets.token_urlsafe(64))"`), **same on all three** — a shared variable |
| `DJANGO_MEDIA_STORAGE` | `cloudinary` |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | From the Cloudinary console (docs/DEPLOYMENT.md §3), same on all three |
| `CLOUDINARY_LINK_EXPIRY_SECONDS` | `900` |
| `RAILPACK_PYTHON_VERSION` | `3.12.8` (Railpack's default is 3.13; Render uses 3.12.8) |
| `HMIS_CLIENT_IP_HEADER` | `X-Real-IP` (api) — see §7 |
| `CORS_ALLOWED_ORIGINS` | api: the web service's origin, e.g. `https://hmis-web-production.up.railway.app` |
| `HMIS_BASE_URL` | api and worker: the React app's URL |
| `WEB_CONCURRENCY` | api: `2` (Gunicorn workers) |
| `VITE_API_BASE_URL` | **hmis-web only**: the API's origin, e.g. `https://hmis-api-production.up.railway.app` — read at build time, public, never a secret; redeploy the web service after changing it |

Optional for the web service: `RAILPACK_NODE_VERSION` (e.g. `22`; otherwise
Railpack uses the current LTS).

### 5.3 Optional

| Variable | Service(s) | Purpose |
|---|---|---|
| `RESEND_API_KEY`, `HMIS_EMAIL_FROM`, `HMIS_ADMIN_EMAIL` | api **and** worker | Admin email |
| `HMIS_ADMIN_ALLOWED_NETWORKS` | api | Django Admin only from these CIDRs (judged by `HMIS_CLIENT_IP_HEADER`) |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | api | Extra hosts/origins (a second custom domain); the Railway domain is automatic |
| `DJANGO_SECURE_HSTS_SECONDS` / `_INCLUDE_SUBDOMAINS` / `_PRELOAD` | api | HSTS tuning (default one year, no subdomains/preload) |
| `DJANGO_LOG_LEVEL`, `DJANGO_REQUEST_LOG` | api, worker, beat | Logging (defaults INFO / on) |
| `TIME_ZONE` | api, worker, beat | Default `Africa/Lagos` |
| `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS` | api | Lockout (5 / 300) |
| `DB_CONN_MAX_AGE` | api | Connection reuse (60 s) |

**Never set:** `DJANGO_DEBUG=true` (refused), `DJANGO_LOG_DIR` (lost on deploy;
`check --deploy` warns), `DJANGO_MEDIA_STORAGE=filesystem` (refused), any
secret in a `VITE_*` variable, any S3/AWS variable (not used).

---

## 6. Step by step

1. Push the repository to a **private** GitHub repository.
2. Set up Cloudinary exactly as in `docs/DEPLOYMENT.md` §3 (private
   `authenticated` raw assets, 2FA, no unsigned presets).
3. Railway: **New Project → Deploy from GitHub repo**; then add **Postgres** and
   **Redis** from **+ New → Database**.
4. Create four services from the same repository and, in each one's
   **Settings**, set the **Root Directory** and **Config File Path** from §3.
   Name them `hmis-api`, `hmis-web`, `hmis-celery-worker`, `hmis-celery-beat`.
5. Add the shared variables and each service's variables (§5).
6. **hmis-api → Settings → Networking → Generate Domain.** Do **not** generate
   domains for the worker or Beat.
7. **hmis-web → Generate Domain**; put its URL in the API's
   `CORS_ALLOWED_ORIGINS` and `HMIS_BASE_URL`, and the API's URL in the web
   service's `VITE_API_BASE_URL`; redeploy both.
8. Deploy. The API builds (install + `collectstatic`), runs `migrate` in
   pre-deploy (seeding departments — Procedure and Theatre separately —
   locations, catalogues and price lists), then must answer `/healthz/` with 200
   before it takes traffic.
9. Open `https://<api domain>/healthz/` → `{"status": "healthy"}`, and
   `/admin/` → a styled Django Admin login.
10. Open a shell **inside** the running `hmis-api` service (Railway CLI:
    `railway ssh`, or the dashboard's shell where your plan offers it) and run
    `python manage.py check --deploy` (no errors; HSTS W005/W021 expected) and
    `python manage.py createsuperuser`; then in Django Admin → Users set that
    account's **Role = Super Admin**. (`railway run` executes on *your* machine,
    which cannot reach `postgres.railway.internal`.)
11. Worker and Beat logs show `celery@… ready` and `beat: Starting…`.
12. Custom domains: add them under each service's Networking; for the API also
    add it to `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` only if it
    differs from `RAILWAY_PUBLIC_DOMAIN`; update `CORS_ALLOWED_ORIGINS`,
    `HMIS_BASE_URL` and `VITE_API_BASE_URL` (redeploy the web service).
13. Walk the smoke tests in `docs/DEPLOYMENT.md` §10, including the Cloudinary
    privacy checks, with test patients; purge them before go-live.

---

## 7. Client IP for the login lockout

The lockout groups failed sign-ins by client address. Railway's own docs do not
state how its edge treats client-supplied headers; Railway staff on its
community forum state that the edge strips client-supplied
`X-Forwarded-For` and that `X-Real-IP` is "a single source of truth for the
connecting IP" (another thread reports `X-Real-IP` carrying a CDN address when
traffic passes Railway's CDN layer). Set `HMIS_CLIENT_IP_HEADER=X-Real-IP`, then
**verify after deploying**: sign in wrongly once while sending a forged
`X-Forwarded-For: 1.2.3.4` and `X-Real-IP: 1.2.3.4`, and confirm the API log's
`Sign-in failed: client=…` shows your real address, not 1.2.3.4. If it does
not, stop and report it — the lockout could be bypassed.

---

## 8. Cloudinary

Unchanged from the Render deployment: `DJANGO_MEDIA_STORAGE=cloudinary`, files
uploaded server-side as `raw` assets of type `authenticated`, links are private
download URLs that expire after `CLOUDINARY_LINK_EXPIRY_SECONDS` (900), the
existing permissions decide who gets one, and the API secret never leaves the
server. Account set-up and verification: `docs/DEPLOYMENT.md` §3 and §10.

---

## 9. Logs, backups, maintenance

* Logs: each service's **Deploy Logs / Observability** show stdout with the
  request IDs; the same never-logged list as on Render applies.
* Backups: Railway volume backups (daily kept 6 days, weekly 27, monthly 89)
  can cover the Postgres volume — check that your plan includes them and enable
  them. Keep your own encrypted `pg_dump` copies outside Railway too (enable
  Postgres public access only while dumping, then remove it). Documents are in
  Cloudinary (see `docs/DEPLOYMENT.md` §8).
* Updates: push to the branch; each service rebuilds (watch patterns limit
  rebuilds to its own directory); the API migrates in pre-deploy.

---

## 10. Local development (unchanged)

```bash
cd backend/hmis && ./venv/bin/python manage.py runserver   # SQLite
cd frontend && npm run dev                                   # Vite proxy for /api and /media
```

No Railway, PostgreSQL, Redis or Cloudinary needed.

---

## 11. What was checked locally

* `apps/core/tests/test_production_settings.py` (Railway and Render classes),
  `test_logging_safety.py`, `accounts/tests/test_login_lockout.py`.
* With Railway-shaped variables: `collectstatic`; `check --deploy` exit 0;
  Gunicorn from the exact start command bound `0.0.0.0:$PORT` with
  `WEB_CONCURRENCY` workers; a request with `Host: healthcheck.railway.app` to
  `/healthz/` over plain HTTP was accepted (503 only because no database was
  running); HTTPS via `X-Forwarded-Proto` without a redirect loop; unknown host
  400; Admin CSS 200; CORS only for the web origin; no secret in the log; the
  worker settings load with no hostname and the Celery app has the existing
  broker, Beat schedule and tasks; filesystem media refused.

Not checked: Railway itself (Railpack builds, `{{.DIST_DIR}}` templating in
`frontend/Caddyfile`, watch patterns, the pre-deploy step, private networking,
the edge's IP headers, the health checker).
