# Rollback and Incident Runbook

**The rule that governs every procedure below: historical serviceability checks are never deleted.** The `serviceability_checks` table is protected by a database trigger that rejects `UPDATE` and `DELETE`. If a recovery step appears to require removing audit rows, it is the wrong step.

---

## 0. Decide what kind of incident this is

| Symptom | Section |
|---|---|
| Customers wrongly marked NOT_AVAILABLE | §1 — stop deciding, then investigate |
| Everything returns `ROUTE_CALCULATION_ERROR` | §2 — provider failure |
| Application will not start or is erroring broadly | §3 — release rollback |
| A bad migration | §4 |
| Wrong service-location data imported | §5 |
| Threshold changed by mistake | §6 |
| Database corruption or loss | §7 |

---

## 1. Wrong serviceability decisions — highest priority

Wrong decisions are the worst failure this system can have, because they are silent: a customer is rejected and nobody sees an error.

### Step 1 — stop making automated decisions (do this first, before diagnosing)

```bash
TOKEN=$(curl -s -X POST https://<host>/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password>"}' | jq -r .accessToken)

curl -s -X PUT https://<host>/api/v1/admin/config/AUTOMATIC_DECISION_ENABLED \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"value":"false","reason":"INC-<id>: suspected incorrect serviceability decisions"}'
```

This takes effect on the next check, with no restart. Customer creation keeps working; every new customer goes to `PENDING_REVIEW` instead of receiving an automated answer. Operations qualifies manually until the cause is found.

Tell the operations team immediately, in these words: *"Automatic serviceability decisions are paused. New customers will show PENDING_REVIEW. Please qualify manually using Google Maps until further notice."*

### Step 2 — reproduce with one known-bad case

```bash
curl -s -X POST https://<host>/api/v1/serviceability/preview \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"latitude":13.0850,"longitude":80.2101}' | jq
```

Then measure the same origin and destination by hand in Google Maps.

### Step 3 — work through the causes, in order of likelihood

**a) The service-location data is wrong.** By far the most common cause. A location with wrong coordinates, or one that should be ACTIVE but is INACTIVE, changes coverage silently.

```sql
-- Locations outside the Chennai area, i.e. almost certainly wrong
SELECT service_code, location_name, latitude, longitude
FROM service_locations
WHERE status='ACTIVE'
  AND (latitude NOT BETWEEN 12.6 AND 13.6 OR longitude NOT BETWEEN 79.9 AND 80.6);

-- Reversed lat/lng: a Chennai point with latitude ~80 is a swapped pair
SELECT service_code, latitude, longitude FROM service_locations
WHERE status='ACTIVE' AND latitude > 70;

-- Active rows with no usable geometry: invisible to every candidate search
SELECT count(*) FROM service_locations WHERE status='ACTIVE' AND location IS NULL;

-- What changed recently
SELECT service_code, location_name, status, updated_at
FROM service_locations ORDER BY updated_at DESC LIMIT 30;
```

**b) The threshold was changed.**

```sql
SELECT * FROM config_audit
WHERE config_key='SERVICEABILITY_RADIUS_METERS'
ORDER BY created_at DESC LIMIT 10;
```

**c) The routing provider is returning wrong distances.** Compare a stored decision against a manual Google Maps measurement:

```sql
SELECT customer_latitude, customer_longitude, calculated_distance_meters,
       routing_provider, threshold_meters, result, created_at
FROM serviceability_checks
WHERE result IN ('AVAILABLE','NOT_AVAILABLE')
ORDER BY created_at DESC LIMIT 20;
```

A *systematic* offset (every distance roughly double, or absurdly large) points at a coordinate-order bug or the wrong travel mode. A single odd value is usually a genuine road-network quirk.

**d) The candidate radius is too tight.** If the nearest location by road is being missed:

```sql
SELECT value FROM app_config WHERE key IN ('CANDIDATE_RADIUS_FACTOR','CANDIDATE_MAX_COUNT');
```

Raising `CANDIDATE_MAX_COUNT` widens the road-measured set at proportional cost. Raising `CANDIDATE_RADIUS_FACTOR` widens the Stage-1 net.

**e) A stale route cache.** If a location was moved by direct SQL rather than through the API, the cache was not invalidated.

```bash
docker compose exec redis redis-cli --scan --pattern 'route:*' | head
docker compose exec redis redis-cli --scan --pattern 'route:*' | xargs -r docker compose exec -T redis redis-cli del
```

Flushing the route cache is always safe. The only cost is that the next checks pay for fresh routing calls.

### Step 4 — re-run affected checks after the fix

```bash
# Re-check every customer decided in the affected window.
# Previous decisions are NOT deleted -- a new audit row is appended, so the
# record shows both the wrong decision and the correction.
for id in $(curl -s "https://<host>/api/v1/customers?status=NOT_AVAILABLE&page_size=200" \
              -H "Authorization: Bearer $TOKEN" | jq -r '.items[].id'); do
  curl -s -X POST "https://<host>/api/v1/customers/$id/serviceability-check" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"forceRefresh":true}' > /dev/null
  sleep 0.3   # stay inside the provider rate limit
done
```

### Step 5 — re-enable automated decisions

Only after a sample of at least twenty checks has been manually verified against Google Maps.

```bash
curl -s -X PUT https://<host>/api/v1/admin/config/AUTOMATIC_DECISION_ENABLED \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"value":"true","reason":"INC-<id>: cause fixed and 20 decisions manually verified"}'
```

---

## 2. Routing provider failure

**Symptom:** widespread `ROUTE_CALCULATION_ERROR`; the `RoutingProviderFailureRateHigh` alert fires.

**Reassurance first:** no customer is being wrongly rejected. The engine cannot turn a provider failure into `NOT_AVAILABLE`. This is a work stoppage, not a data-integrity incident.

```bash
# 1. What is failing
curl -s -X POST https://<host>/api/v1/admin/routing/health \
  -H "Authorization: Bearer $TOKEN" | jq

docker compose logs --tail=200 backend | grep -E 'provider_retry|circuit_opened|routing_failed'
```

Common causes:

| Log signature | Cause | Action |
|---|---|---|
| `PROVIDER_QUOTA_EXCEEDED` | Daily quota hit | Raise the quota in Google Cloud Console, or wait for the reset |
| `PROVIDER_REQUEST_DENIED` | Key restriction or disabled API | Check IP restriction — the host's egress IP may have changed |
| `ROUTING_TIMEOUT` | Network or provider slowness | Check connectivity; consider raising `PROVIDER_TIMEOUT_SECONDS` |
| `ROUTING_CIRCUIT_OPEN` | Breaker tripped after repeated failures | Fix the underlying cause; it half-opens automatically |

**Switching provider mid-incident.** If Google is down and OSRM is prepared:

```bash
# Edit .env:  ROUTING_PROVIDER=osrm
docker compose --profile osrm up -d osrm
docker compose up -d backend
curl -s -X POST https://<host>/api/v1/admin/routing/health -H "Authorization: Bearer $TOKEN" | jq
```

Note in the incident record that decisions during this window used OSRM — the provider name is stored on every audit row, so the switch is already traceable.

---

## 3. Release rollback

```bash
cd /opt/serviceability
./scripts/backup.sh                    # always, before touching anything

git log --oneline -10                  # identify the last good tag
git checkout <previous-good-tag>
docker compose build --pull
docker compose up -d
docker compose logs -f backend
```

**If the new release included a migration**, check whether it is backwards-compatible before rolling the code back. Additive changes (new nullable columns, new tables, new indexes) are safe to leave in place — the older code ignores them. Destructive changes are not; see §4.

Verify with §3 of the deployment runbook before declaring the rollback complete.

---

## 4. Migration rollback

```bash
docker compose exec backend alembic current
docker compose exec backend alembic downgrade -1 --sql > /tmp/down.sql
less /tmp/down.sql
# READ IT. If it drops a column that holds data you need, do not run it --
# restore from backup instead (§7).

docker compose exec backend alembic downgrade -1
```

Alembic downgrades that drop columns destroy data. For any migration touching `serviceability_checks`, prefer restoring from backup over downgrading.

---

## 5. Bad service-location import

A committed import cannot be "un-imported" by a single command, but the batch is fully traced.

```sql
-- What did this batch create?
SELECT count(*) FROM service_locations WHERE import_batch_id = '<batch-uuid>';

-- Deactivate them all: removes them from candidate searches while keeping
-- every historical decision explainable.
UPDATE service_locations
SET status='INACTIVE', updated_at=now()
WHERE import_batch_id = '<batch-uuid>';
```

Deactivate rather than delete. Deleting would break the foreign key on past checks (`ON DELETE SET NULL` would blank the nearest-location reference) and destroy the ability to explain why a decision was made.

Then flush the route cache (§1e) and re-run checks for affected customers (§1 step 4).

---

## 6. Threshold changed by mistake

```sql
SELECT old_value, new_value, changed_by_label, reason, created_at
FROM config_audit WHERE config_key='SERVICEABILITY_RADIUS_METERS'
ORDER BY created_at DESC LIMIT 5;
```

Restore it through the API so the correction is itself audited:

```bash
curl -s -X PUT https://<host>/api/v1/admin/config/SERVICEABILITY_RADIUS_METERS \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"value":"2000","reason":"INC-<id>: reverting unintended change from <old>"}'
```

Decisions made while the wrong threshold was in force are still explainable — each check stored the threshold that produced it. Find them:

```sql
SELECT id, customer_id, calculated_distance_meters, threshold_meters, result, created_at
FROM serviceability_checks
WHERE threshold_meters <> 2000
ORDER BY created_at DESC;
```

Re-run those customers per §1 step 4.

---

## 7. Database restore

Last resort. It loses everything written since the backup, including audit rows.

```bash
docker compose stop backend           # stop writes first
./scripts/backup.sh                   # snapshot the current broken state too --
                                      # you may need it for the post-mortem

gunzip -c backups/serviceability_<timestamp>.sql.gz | \
  docker compose exec -T postgres psql -U serviceability -d postgres

docker compose exec backend alembic current   # confirm schema version
docker compose up -d backend
```

Then work through §3 of the deployment runbook in full, and tell operations exactly which time window of records was lost.

---

## 8. Communication template

> **Serviceability system — <status>**
>
> **What is happening:** <one sentence>
> **Impact on decisions:** <e.g. "No incorrect decisions have been made. Checks are returning an error and must be retried." / "Decisions made between 09:00 and 11:30 IST may be incorrect and are being re-run.">
> **What operations should do now:** <e.g. "Retry the check." / "Qualify manually against Google Maps; the system will show PENDING_REVIEW.">
> **Next update:** <time>

Be explicit about whether decisions are *wrong* or merely *unavailable*. Those need very different responses from the business, and conflating them is how a routing outage turns into lost customers.

---

## 9. After every incident

- [ ] Root cause written down, not just the symptom.
- [ ] A test added that would have caught it. The suite in `backend/tests/` is where this belongs.
- [ ] An alert added or tuned, if it was noticed by a human rather than by monitoring.
- [ ] This runbook updated with anything that was missing.
- [ ] Affected customers re-checked and the corrected decisions communicated to sales.
