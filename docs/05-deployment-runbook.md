# Production Deployment Runbook

Audience: whoever is on the keyboard during a deployment. Every step is either a command to run or a check with a stated pass condition.

---

## 0. Environments

```
DEVELOPMENT  →  TEST/QA  →  UAT  →  PRODUCTION
```

Each has its own `.env`, its own database, and its own Google Maps API key with its own quota and IP restriction. Nothing is developed against production, and no credential is shared between environments.

---

## 1. First-time production setup

### 1.1 Host

- Linux host, Docker Engine 24+ and the Compose plugin.
- Minimum: 4 vCPU, 8 GB RAM, 60 GB SSD. Postgres and Redis are the memory consumers; add RAM before CPU as the service-location count grows.
- Inbound 443 only. Postgres and Redis are published to `127.0.0.1` in the compose file and must never be exposed publicly.

### 1.2 Google Cloud project

1. Create a dedicated project for production. Do not reuse the development project — a shared quota means a development loop can exhaust production's.
2. Enable exactly four APIs: **Geocoding API**, **Places API**, **Distance Matrix API**, **Directions API**.
3. Create one API key. Under *Application restrictions* choose **IP addresses** and enter the production host's egress IP. Under *API restrictions* select only the four APIs above.
4. Set a **daily quota cap** on each API. This is the only hard stop against a runaway loop turning into an invoice.
5. Set a billing budget alert at your expected monthly spend.

The key is a server credential. It goes in `.env` on the host, is never committed, and never reaches a browser.

### 1.3 Configuration

```bash
git clone <repository> /opt/serviceability
cd /opt/serviceability
cp .env.example .env
chmod 600 .env

python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
```

Edit `.env` and set, at minimum:

| Variable | Production value |
|---|---|
| `ENVIRONMENT` | `production` |
| `DEBUG` | `false` |
| `ENABLE_API_DOCS` | `false` |
| `FORCE_HTTPS` | `true` |
| `SECRET_KEY` | the generated 64-char string |
| `POSTGRES_PASSWORD` | the generated password |
| `GOOGLE_MAPS_API_KEY` | the restricted production key |
| `BOOTSTRAP_ADMIN_PASSWORD` | a strong password, changed after first login |
| `CORS_ORIGINS` / `TRUSTED_HOSTS` | the real hostname |

The application **refuses to start** in production if `DEBUG` is on, docs are enabled, HTTPS is not forced, the secret key is short, the fake provider is selected, or the database password is a known default. That check is in `app/core/config.py`; if startup fails, read the message — it names every problem at once.

### 1.4 TLS

Terminate TLS at `deploy/nginx/nginx.conf` (add the `listen 443 ssl` block and certificate paths) or at a load balancer in front of it. Uncomment the `Strict-Transport-Security` header once certificates are live.

---

## 2. Deployment

```bash
cd /opt/serviceability

# 2.1 BACKUP FIRST. Always, even for a code-only change.
./scripts/backup.sh
# Verify the dump is non-trivial in size and the command exited 0.

# 2.2 Fetch the release
git fetch --tags
git checkout <tag>

# 2.3 Build
docker compose build --pull

# 2.4 Dry-run the migration and READ THE SQL
docker compose run --rm backend alembic upgrade head --sql > /tmp/migration.sql
less /tmp/migration.sql
# Pass condition: no DROP TABLE / DROP COLUMN you did not expect, and nothing
# touching serviceability_checks other than additive changes.

# 2.5 Deploy. Migrations run automatically in the entrypoint before workers start.
docker compose up -d

# 2.6 Watch it come up
docker compose logs -f backend
# Pass condition: "applying database migrations", then "application_starting"
# with the expected routing_provider, then gunicorn worker lines. No traceback.
```

---

## 3. Post-deployment verification

Run all of these. Each has an explicit pass condition.

```bash
# 3.1 Liveness
curl -fsS https://<host>/health
# PASS: {"status":"ok","environment":"production",...}

# 3.2 Readiness — database and Redis
curl -fsS https://<host>/health/ready | jq
# PASS: status "ready", database "ok". Redis "degraded" is survivable
#       (no cache, no live updates) but should be fixed.

# 3.3 Spatial indexes exist
docker compose exec postgres psql -U serviceability -d serviceability -c \
  "SELECT indexname FROM pg_indexes WHERE tablename='service_locations' AND indexdef ILIKE '%gist%';"
# PASS: ix_service_locations_location_gist AND ix_service_locations_active_location.
#       If either is missing, candidate search degrades to a full table scan.

# 3.4 Threshold is what the business expects
docker compose exec postgres psql -U serviceability -d serviceability -c \
  "SELECT key, value FROM app_config WHERE key='SERVICEABILITY_RADIUS_METERS';"
# PASS: 2000

# 3.5 Append-only guard is armed
docker compose exec postgres psql -U serviceability -d serviceability -c \
  "UPDATE serviceability_checks SET result='AVAILABLE' WHERE false;"
# PASS: the statement is allowed only because it matches no rows. To prove the
#       trigger, run it against a real row in QA -- it must raise
#       'serviceability_checks is append-only'.

# 3.6 Provider reachability (authenticated)
TOKEN=$(curl -s -X POST https://<host>/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password>"}' | jq -r .accessToken)

curl -s -X POST https://<host>/api/v1/admin/routing/health \
  -H "Authorization: Bearer $TOKEN" | jq
# PASS: routing.healthy true, geocoding.healthy true.

# 3.7 End-to-end smoke test with a real Chennai address
curl -s -X POST https://<host>/api/v1/serviceability/preview \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"address":"Anna Nagar Tower Park, Chennai"}' | jq
# PASS: a status, thresholdMeters 2000, distanceType "ROAD_DISTANCE",
#       and a nearestServiceLocation with a plausible distance.

# 3.8 THE CRITICAL MANUAL CHECK — validate a road distance by hand
# Take the coordinates from 3.7 and enter the same origin and destination into
# Google Maps in a browser. The driving distance shown there must match
# distanceMeters to within a few percent.
# If it does not: STOP. Do not go live. A systematic mismatch means the
# coordinate order is reversed or the wrong travel mode is configured, and
# every decision the system makes will be wrong.

# 3.9 API docs are off
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/api/v1/docs
# PASS: 404

# 3.10 No API key reachable from the browser bundle
curl -s https://<host>/assets/index-*.js | grep -c 'AIza'
# PASS: 0. A non-zero result means a key leaked into the frontend build --
#       revoke that key immediately.

# 3.11 Metrics not exposed publicly
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/metrics
# PASS: 404
```

---

## 4. Loading the real service locations

The system is useless until the real Chennai service network is loaded. Coverage is defined entirely by the contents of `service_locations`.

1. Sign in as ADMIN.
2. `POST /api/v1/imports` with the CSV or XLSX. This **validates only** and writes nothing to `service_locations`.
3. Download the report: `GET /api/v1/imports/{id}/report?fmt=xlsx`.
4. Review it with the operations team. Every invalid, duplicate and failed-geocode row must be understood — a missing location silently shrinks coverage and produces wrong NOT_AVAILABLE answers.
5. Fix the source file and re-upload as many times as needed.
6. When the report is clean, `POST /api/v1/imports/{id}/commit`.
7. Spot-check on the map: pick ten known locations and confirm the markers sit where the operations team expects.

```bash
# Sanity counts after commit
docker compose exec postgres psql -U serviceability -d serviceability -c \
  "SELECT status, count(*), count(*) FILTER (WHERE location IS NULL) AS missing_geometry
   FROM service_locations GROUP BY status;"
# PASS: the ACTIVE count matches the business's expectation, and
#       missing_geometry is 0 for ACTIVE rows.
```

---

## 5. Go-live checklist

Do not tick a box you have not personally verified.

- [ ] Production database backed up, and a restore rehearsed at least once.
- [ ] `.env` complete, `chmod 600`, not in git.
- [ ] Google API key restricted by IP and by API, with daily quota caps.
- [ ] Billing budget alert configured.
- [ ] Migrations applied; spatial indexes confirmed present (3.3).
- [ ] Warehouse record created with verified coordinates.
- [ ] Real service locations imported; validation report reviewed and signed off.
- [ ] Threshold confirmed at 2000 m with the business (3.4).
- [ ] Ten sample Chennai locations tested end to end.
- [ ] **Road distances manually validated against Google Maps** (3.8).
- [ ] Bootstrap admin password changed; real user accounts created with correct roles.
- [ ] TLS live; HSTS enabled; API docs off (3.9).
- [ ] No API key in the frontend bundle (3.10).
- [ ] Prometheus scraping; alert rules loaded; alert routing tested with a deliberate failure.
- [ ] Log aggregation receiving structured JSON.
- [ ] Operations users trained — in particular on `ROUTE_CALCULATION_ERROR` (retry, it is not a rejection) and on `LOCATION_VERIFICATION_REQUIRED` (drag the marker and confirm).
- [ ] Rollback runbook read by whoever is on call.
- [ ] Pilot scope agreed: **one service area or one warehouse first.**

---

## 6. Pilot

Do not switch the whole Chennai network on at once.

1. Import one service area's locations only.
2. Run one week of live traffic through it.
3. Each day, export the report and have operations manually verify a sample of decisions against Google Maps.
4. Track: what proportion of checks return `AVAILABLE`; how many need location verification; how many routing errors occur; and the p95 latency.
5. Expand only when the sample review shows no disagreement between the system's road distance and a human check.

---

## 7. Backups

`scripts/backup.sh` takes a compressed `pg_dump`. Schedule it hourly, retain 7 days locally and 30 days offsite.

```bash
0 * * * * cd /opt/serviceability && ./scripts/backup.sh >> /var/log/svc-backup.log 2>&1
```

A backup you have never restored is not a backup. Restore into QA once a quarter and run the smoke test against it.

---

## 8. Routine operations

```bash
docker compose ps                          # what is running
docker compose logs -f --tail=200 backend  # follow logs
docker compose restart backend             # restart the API only
docker compose exec backend alembic current  # applied migration
docker compose exec backend alembic history  # available migrations

# Find every decision for one customer
docker compose exec postgres psql -U serviceability -d serviceability -c \
  "SELECT created_at, result, calculated_distance_meters, threshold_meters,
          routing_provider, created_by_label
   FROM serviceability_checks
   WHERE customer_id = (SELECT id FROM customers WHERE customer_code='CUST-1001')
   ORDER BY created_at DESC;"
```
