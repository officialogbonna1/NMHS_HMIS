# NMHS-HMIS-V1.0 — Deployment and production configuration

This describes the configuration in the repository as it now stands. No real
secrets appear here; every credential is a placeholder.

## 1. Two environments, chosen explicitly

`DJANGO_ENV` decides, and nothing else does (`DEBUG=False` does **not** imply
production). The rules live in `backend/hmis/hmis/environment.py`.

| | `development` (default) | `production` |
|---|---|---|
| Database | SQLite, `backend/hmis/db.sqlite3` (unchanged) | PostgreSQL from `DB_*` — **required** |
| If something is missing | Development defaults | Refuses to start (`ImproperlyConfigured`, names the variable) |
| DEBUG | On unless `DJANGO_DEBUG` is not `True` | Off; `DJANGO_DEBUG=true` is refused |
| Secret key | Development key | `DJANGO_SECRET_KEY`, 50+ chars, not a placeholder — required |
| Hosts / CSRF origins | localhost and the Vite origins | Required; origins must be `https://` |
| HTTPS settings | Off | On (redirect, secure cookies, HSTS, proxy header) |
| Password validators | None | Length 10+, common, numeric, similarity |
| Logging | Console, DEBUG for the application | Console (journald), INFO; optional rotating files |
| SQL logging | Optional (`DJANGO_SQL_DEBUG=true`) | Never |

### Local development (unchanged)

```
SQLite  →  python manage.py runserver (:8000)  →  npm run dev (Vite, :5173, proxies /api and /media)
```

```bash
cd backend/hmis && ./venv/bin/python manage.py runserver
cd frontend && npm run dev
```

Nothing has to be installed or set. The existing `db.sqlite3` is used as is.

### Production

```
Browser ─HTTPS─► Nginx ─┬─ /            React build (frontend/dist)
                        ├─ /api/        ─► Gunicorn (WSGI, hmis.wsgi:application) ─► PostgreSQL
                        ├─ /healthz/    ─► Gunicorn
                        └─ /media/      uploaded files (restricted)
Admin host ─HTTPS─► Nginx ─► Gunicorn (Django Admin, /static/ from STATIC_ROOT)
Redis ◄── Celery worker (admin email) · Celery Beat (nightly stock checks) · login-lockout cache
```

There is **no WebSocket / ASGI component**: the application has no `asgi.py`,
no Channels and no WebSocket client. Live data is polled (notification bell
every 30 s, station queues every 60 s). WSGI under Gunicorn is the right server.

## 2. Environment variables

The application reads the process environment only — it does **not** load a
`.env` file. Put the values in a root-owned file (`/etc/nmhs-hmis/hmis.env`,
mode `600`) and load it with `EnvironmentFile=` in each systemd unit. Keep every
comment on its own line (systemd does not strip trailing comments).
`backend/hmis/.env.example` lists all of them with placeholders.

**Required in production**

| Variable | Example (placeholder) |
|---|---|
| `DJANGO_ENV` | `production` |
| `DJANGO_SECRET_KEY` | 50+ random characters (`python3 -c "import secrets; print(secrets.token_urlsafe(64))"`) |
| `DJANGO_ALLOWED_HOSTS` | `hmis.example-hospital.org,hmis-admin.example-hospital.org` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://hmis.example-hospital.org,https://hmis-admin.example-hospital.org` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` | `hmis`, `hmis`, `<strong-password>`, `127.0.0.1` |

**Recommended**: `REDIS_URL` (Celery broker and the shared login-lockout count;
without it each Gunicorn worker counts failures separately — `check --deploy`
warns, `hmis.W001`).

**Optional, with secure defaults**: `DB_PORT` (5432), `DB_CONN_MAX_AGE` (60),
`CACHE_URL`, `DJANGO_BEHIND_HTTPS_PROXY` (true), `DJANGO_SECURE_SSL_REDIRECT`
(true), `DJANGO_SECURE_HSTS_SECONDS` (31536000),
`DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` (false), `DJANGO_SECURE_HSTS_PRELOAD`
(false), `CORS_ALLOWED_ORIGINS`, `DJANGO_STATIC_ROOT`, `DJANGO_MEDIA_ROOT`,
`DJANGO_LOG_LEVEL`, `DJANGO_LOG_DIR`, `DJANGO_LOG_MAX_BYTES`,
`DJANGO_LOG_BACKUPS`, `DJANGO_REQUEST_LOG`, `TIME_ZONE`,
`LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`, `RESEND_API_KEY`,
`HMIS_EMAIL_FROM`, `HMIS_ADMIN_EMAIL`, `HMIS_BASE_URL`,
`RESEND_TIMEOUT_SECONDS`, `ANTHROPIC_API_KEY`.

HSTS subdomains and preload are opt-in because they commit the whole domain to
HTTPS; `check --deploy` lists them as Django warnings (W005, W021) by design.

## 3. Failing safely

* **Start-up**: any missing required variable stops the process with a message
  naming the variable — never its value — and there is no SQLite fallback.
* **`manage.py check --deploy`** exits non-zero (errors `hmis.E001`–`E010`) when:
  not production, DEBUG on, not PostgreSQL, no HTTPS redirect, insecure cookies,
  HSTS off, redirect without the proxy header, wildcard/empty hosts, non-HTTPS
  CSRF origins, or SQL logging on.
* **API errors**: expected failures keep their message and a `code` (validation
  errors stay readable). An unexpected failure answers `500` with
  `{"detail": "...", "code": "server_error", "reference": "RF-xxxxxxxx"}` only —
  no exception, traceback, SQL or path. `IntegrityError` answers `409` with a
  fixed sentence. With DEBUG off, Django's non-API pages show the plain error
  page, never the debug page.

## 4. Logging

All output goes to stdout/stderr. Under systemd that is **journald** — the
recommended destination — so each service's log is `journalctl -u <service>`.
Every record carries a request ID: `... INFO hmis.request [<request-id>] ...`.

| Logger | Contents |
|---|---|
| `hmis.request` | `METHOD /path STATUS 12ms user=<id>` — never the query string, headers, cookies or body |
| `hmis.api` | Unexpected errors with traceback + `RF-` reference; 409 conflicts (rule name only) |
| `hmis.security`, `django.security` | Sign-in success/failure/lockout, sign-out, permission denials (user id, role, method, path, view) |
| `hmis.email` | Admin email delivery outcomes (fixed subjects, hospital number only) |
| `hmis.health` | Health-check failures (exception class only) |
| `celery` | Worker and Beat, through the same handlers (task name and id; never arguments) |

**Never logged**: passwords (including what was typed as a username on a failed
sign-in), tokens, `Authorization` headers, cookies, CSRF tokens, request bodies,
query strings, the secret key, database or Redis passwords, API keys, private
keys, and patient records, diagnoses, results, prescriptions, notes, addresses,
phone numbers or financial details. Identifiers only (user id, record path with
its UUID, hospital number in email keys). A `RedactingFilter` on every handler
scrubs credential shapes and the configured secret values from messages and
tracebacks as a safety net.

**Audit trail** (database, `core.AuditLog`, Django Admin → Core → Audit logs):
the existing system, now also recording `auth.login` and `auth.logout`
alongside `auth.login_locked`, user changes, password resets and every clinical
and financial action. Failed sign-ins go to the security log only (not the
database) so a password-guessing run cannot grow a table.

**Request IDs**: a valid incoming `X-Request-ID` (8–64 of `A-Za-z0-9._-`) is
kept, otherwise one is generated; it is returned in the `X-Request-ID` response
header, added to every log line, and carried to Celery in a message header.
Beat tasks use `task-<id>`.

### Rotation and retention

* **journald (recommended)** — in `/etc/systemd/journald.conf`:
  `SystemMaxUse=2G`, `MaxRetentionSec=90day`, then
  `systemctl restart systemd-journald`. Ninety days keeps enough for incident
  investigation without keeping operational logs forever.
* **Files (optional)** — set `DJANGO_LOG_DIR=/var/log/nmhs-hmis` (owned by the
  service user, mode 750) to add `hmis.log`, `error.log` and `security.log`,
  each a `RotatingFileHandler` capped at `DJANGO_LOG_MAX_BYTES` (10 MB) ×
  `DJANGO_LOG_BACKUPS` (10). With several Gunicorn workers prefer journald;
  rotating files are per process and can interleave at rotation.
* **Nginx** — logrotate (installed with Nginx) keeps 14 days by default. Use a
  log format **without the query string** — patient searches travel in it:

```nginx
log_format hmis '$remote_addr - [$time_local] "$request_method $uri $server_protocol" '
                '$status $body_bytes_sent $request_time "$http_x_request_id"';
access_log /var/log/nginx/hmis.access.log hmis;
```

## 5. Health check

`GET /healthz/` → `200 {"status": "healthy"}` when the database answers, `503
{"status": "unhealthy"}` when it does not. Nothing else is returned; the reason
goes to the server log. Unauthenticated by design, outside `/api/`, exempt from
the HTTPS redirect for loopback probes. Redis is not part of it (nothing stops
when Redis is down). From the server:
`curl -s -H "Host: hmis.example-hospital.org" http://127.0.0.1:8000/healthz/`.

## 6. Deploying

```bash
# PostgreSQL
sudo -u postgres psql -c "CREATE ROLE hmis LOGIN PASSWORD '<strong-password>';"
sudo -u postgres psql -c "CREATE DATABASE hmis OWNER hmis ENCODING 'UTF8';"

# application
cd /srv/nmhs-hmis/backend/hmis
python3.12 -m venv venv && ./venv/bin/pip install -r requirements.txt
set -a; . /etc/nmhs-hmis/hmis.env; set +a
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
./venv/bin/python manage.py createsuperuser      # then set Role = Super Admin in Django Admin
./venv/bin/python manage.py check --deploy       # must exit 0
cd ../../frontend && npm ci && npm run build      # serve frontend/dist
```

systemd units (all with `User=hmis`, `WorkingDirectory=/srv/nmhs-hmis/backend/hmis`,
`EnvironmentFile=/etc/nmhs-hmis/hmis.env`, `Restart=on-failure`):

```
nmhs-gunicorn   ExecStart=…/venv/bin/gunicorn hmis.wsgi:application --bind 127.0.0.1:8000 --workers 5 --timeout 60
nmhs-celery     ExecStart=…/venv/bin/celery -A hmis worker -l info
nmhs-celerybeat ExecStart=…/venv/bin/celery -A hmis beat -l info --schedule /srv/nmhs-hmis/celerybeat-schedule
```

Gunicorn's own access log is off by default — leave it off (`hmis.request`
replaces it without query strings).

### Nginx essentials

* Two server names: the staff app (React + `/api/` + `/healthz/` + `/media/`) and
  Django Admin on its own host — the React route `/admin` and Django's `/admin/`
  collide on one hostname.
* `proxy_set_header X-Forwarded-Proto $scheme;` (Django's HTTPS detection),
  `proxy_set_header X-Request-ID $request_id;` (shared request IDs), and
  **`proxy_set_header X-Forwarded-For $remote_addr;`** — overwrite, do not
  append: the login lockout groups attempts by the first `X-Forwarded-For` hop,
  so an appended client-supplied value would let an attacker rotate addresses.
* `location / { try_files $uri /index.html; }` for client-side routes;
  `location /static/` → `STATIC_ROOT`; `location /media/` → `MEDIA_ROOT`,
  **restricted to the hospital network** (uploaded documents have no access
  control of their own).
* Headers on the static React files (Django sets them on its own responses):
  `X-Content-Type-Options nosniff`, `X-Frame-Options DENY`,
  `Referrer-Policy same-origin`. HSTS comes from Django on API responses; add
  `Strict-Transport-Security` in Nginx too for the static files. No WebSocket
  `Upgrade` block is needed.

## 7. Production checklist

- [ ] PostgreSQL created; `hmis` role with a strong password; listening on localhost only
- [ ] `/etc/nmhs-hmis/hmis.env` written (mode 600): `DJANGO_ENV=production`, secret key, hosts, CSRF origins, `DB_*`, `REDIS_URL`
- [ ] `DJANGO_DEBUG` unset or `False`
- [ ] Redis installed, bound to localhost, password set if shared
- [ ] HTTPS certificates issued; HTTP redirects to HTTPS
- [ ] `migrate` applied; `makemigrations --check` reports no changes
- [ ] `collectstatic` run; Django Admin is styled
- [ ] Media directory on persistent storage, owned by the service user, `/media/` restricted
- [ ] `nmhs-gunicorn`, `nmhs-celery`, `nmhs-celerybeat` active; `celery -A hmis inspect ping` answers
- [ ] WebSockets: not applicable (none used)
- [ ] Logging visible: `journalctl -u nmhs-gunicorn` shows request lines with request IDs
- [ ] Log rotation: journald limits set (or `DJANGO_LOG_DIR` rotation verified); Nginx log format without query strings
- [ ] Backups: daily encrypted `pg_dump -Fc` + media archive, copied off-server
- [ ] Restore tested into a scratch database
- [ ] Superuser given the Super Admin role; few Super Admins; Django Admin host IP-restricted
- [ ] `manage.py check --deploy` exits 0 (only W005/W021 if HSTS subdomains/preload are deliberately off)
- [ ] `curl …/healthz/` returns `healthy`

## 8. Things this configuration deliberately does not do

* It does not load `.env` files (systemd supplies the environment).
* It does not validate passwords set through the HMIS **Users** screen's API
  (`/api/users/<id>/set_password/`); production validators apply to
  `createsuperuser` and Django Admin's forms. Extending them to the API would
  change the Users screen's behaviour and is a separate decision.
* It does not authenticate `/media/` downloads; restrict them at Nginx.
* API tokens do not expire (DRF `TokenAuthentication`); they end on sign-out
  and on a password reset.
