# Serviceability Algorithm Specification

This document is the normative definition of the decision. The implementation in `backend/app/services/serviceability.py` follows it exactly, and `backend/tests/test_serviceability_engine.py` asserts each numbered rule.

---

## 1. The rule

```
Let  d = actual road (driving) distance in metres from the new customer
         to the nearest ACTIVE service location, as returned by the
         configured routing provider.
Let  T = SERVICEABILITY_RADIUS_METERS, read from app_config at check time.

d <= T   →  AVAILABLE
d >  T   →  NOT_AVAILABLE
```

`d` and `T` are both **integers in metres**. There is no floating-point comparison anywhere in the decision path. A provider returning `2000.4 m` is rounded to `2000` at the adapter boundary and is `AVAILABLE`; `2000.6 m` rounds to `2001` and is `NOT_AVAILABLE`. Rounding happens once, in the adapter, and is documented in the audit record.

Straight-line distance never enters this comparison. The engine's decision function accepts only a value carrying `distance_type == "ROAD_DISTANCE"` and raises `InvalidDistanceTypeError` otherwise — this is a runtime guard, not a comment.

---

## 2. Full algorithm

```
INPUT:  customer_id, customer_lat, customer_lng, actor
OUTPUT: ServiceabilityResult (persisted as one serviceability_checks row)

 1. request_timestamp := now()
 2. T := config.get_int("SERVICEABILITY_RADIUS_METERS")           # e.g. 2000
    F := config.get_float("CANDIDATE_RADIUS_FACTOR")              # e.g. 4.0
    K := config.get_int("CANDIDATE_MAX_COUNT")                    # e.g. 10
    R := round(T * F)                                             # e.g. 8000 m

 3. IF customer_lat IS NULL OR customer_lng IS NULL:
        persist check(result=LOCATION_VERIFICATION_REQUIRED, threshold=T)
        RETURN LOCATION_VERIFICATION_REQUIRED

 4. active_total := COUNT(service_locations WHERE status='ACTIVE')
    IF active_total = 0:
        persist check(result=NO_SERVICE_LOCATION_IN_RANGE,
                      candidate_count=0, threshold=T)
        RETURN configured empty-table status
               (default NO_SERVICE_LOCATION_CONFIGURED)

 5. # ---- STAGE 1: spatial candidate search (no external calls, no cost) ----
    candidates := SELECT ... FROM service_locations
                  WHERE status='ACTIVE'
                    AND ST_DWithin(location, origin, R)
                  ORDER BY location <-> origin
                  LIMIT K
    # ordered by STRAIGHT-LINE distance — used only to choose whom to route to

 6. IF candidates IS EMPTY:
        persist check(result=NO_SERVICE_LOCATION_IN_RANGE, candidate_count=0,
                      threshold=T,
                      reason="no active service location within R metres")
        RETURN status NOT_AVAILABLE
    # Correct: if nothing is within 4x the threshold in a straight line,
    # nothing can be within the threshold by road, since road >= straight-line.
    #
    # Note the deliberate split between the customer STATUS and the audit
    # RESULT. The status is NOT_AVAILABLE, which is the honest business answer.
    # The audit result is NO_SERVICE_LOCATION_IN_RANGE, because no distance was
    # measured -- and the schema constraint
    # ck_checks_decision_requires_road_distance refuses to record an
    # AVAILABLE/NOT_AVAILABLE *result* with no road distance behind it. The
    # database will not let the audit trail claim a measurement that never
    # happened.

 7. # ---- STAGE 2: actual road distance ----
    cached, uncached := split(candidates by route-cache lookup)
    IF uncached is non-empty:
        legs := routing.get_driving_distance(origin, [c.point for c in uncached])
                # ONE matrix request: 1 origin x N destinations
        store legs in cache with TTL
    all_legs := cached + legs

 8. IF every leg is None/unreachable:
        persist check(result=ROUTE_CALCULATION_ERROR, threshold=T,
                      error_code=..., candidate_count=len(candidates))
        RETURN ROUTE_CALCULATION_ERROR          # NEVER NOT_AVAILABLE
    # Partial failure is tolerated: if at least one leg resolved, the decision
    # proceeds on the resolved legs and the check records degraded=true.

 9. nearest := argmin(leg.distance_meters for leg in all_legs if leg is not None)
    d := nearest.distance_meters                      # integer metres
    # NOTE: argmin is over ROAD distance, not over the Stage-1 ordering.
    # The Stage-1 nearest and the road nearest are frequently different
    # locations — see brief section 25 test 5.

10. IF d <= T:  result := AVAILABLE
    ELSE:       result := NOT_AVAILABLE

11. route := routing.get_driving_route(origin, nearest.point)   # geometry, 1 call
    # Failure here degrades gracefully: the decision from step 10 stands,
    # geometry is stored as NULL, and the UI shows the result without a line.

12. response_timestamp := now()
    persist serviceability_checks row:
        customer_id, customer coordinates snapshot,
        nearest_service_location_id, calculated_distance_meters = d,
        distance_type = 'ROAD_DISTANCE',
        routing_provider, route_duration_seconds, route_geometry,
        threshold_meters = T,            # mandatory
        result, candidate_count, cache_hit,
        request_timestamp, response_timestamp, duration_ms,
        created_by = actor

13. UPDATE customers SET service_status, nearest_service_location_id,
        nearest_service_distance_meters = d, last_check_id

14. publish SSE event check.completed
15. RETURN result
```

---

## 3. Why Stage 1 is safe

The concern with any two-stage design is whether the pre-filter can discard the true winner.

**Claim.** Road distance is always greater than or equal to straight-line distance between the same two points.

**Consequence.** If a service location's straight-line distance exceeds `R = T × F`, its road distance also exceeds `R`. With `F >= 1`, `R >= T`, so that location cannot be within the threshold by road, and excluding it cannot change the AVAILABLE/NOT_AVAILABLE decision.

`F = 4.0` is set well above 1 for a second reason: reporting. The engine should name the genuinely road-nearest location even when it is out of range, so operations can see "the nearest we have is 3.2 km". A larger `F` widens the set of locations whose road distance is measured, making that reported nearest more accurate. The trade-off is cost, bounded by `K`.

`K` caps cost. With `K = 10`, one Distance Matrix request per check contains at most 10 elements. If Stage 1 finds more than `K` candidates, the ones kept are the `K` closest in a straight line — the most likely to be closest by road. Raising `K` improves the odds of naming the exact road-nearest location in dense areas; it does not affect correctness of the AVAILABLE/NOT_AVAILABLE decision as long as at least one candidate inside the threshold survives, and the straight-line-nearest `K` will always include any location within `T` straight-line metres when `K` is not exhausted by closer ones.

**Documented residual risk.** In a pathological case — more than `K` locations closer in a straight line than a location that is much closer by road — the reported nearest could be a slightly farther road distance than the true minimum. This cannot flip AVAILABLE to NOT_AVAILABLE incorrectly in the direction that matters: if any of the `K` is within `T`, the answer is AVAILABLE. It could in principle report NOT_AVAILABLE when a discarded location beyond the `K` closest straight-line candidates was within `T` by road — which requires 10 locations all closer in a straight line yet all farther by road. Raise `K` for dense service areas; the default of 10 with `F = 4` is comfortable for Chennai's density.

---

## 4. Status model

| Status | When | Terminal? | UI |
|---|---|---|---|
| `PENDING` | Customer created, check not yet started. | No | Grey · "Pending" |
| `CALCULATING` | Check in flight. | No | Spinner · "Calculating…" |
| `AVAILABLE` | Road distance ≤ threshold. | Yes | Green · "SERVICE AVAILABLE" |
| `NOT_AVAILABLE` | Road distance > threshold. | Yes | Red · "SERVICE NOT AVAILABLE" |
| `LOCATION_VERIFICATION_REQUIRED` | Geocoding failed, was ambiguous, or coordinates absent. | No | Amber · "Confirm location on map" |
| `ROUTE_CALCULATION_ERROR` | Routing provider failed after retries. | No | Orange · "Unable to calculate driving distance. Retry." |
| `NO_SERVICE_LOCATION_CONFIGURED` | No active service locations exist at all. | No | Amber · "No service locations configured" |
| `PENDING_REVIEW` | Supervisor override pending. | No | Blue · "Under review" |

Colour is never the only signal. Every status renders an icon, a text label and, in tables, a text column. The palette is checked for deuteranopia/protanopia distinguishability.

---

## 5. Cache policy (brief §19)

Cache key: `route:{provider}:{origin_lat},{origin_lng}:{dest_lat},{dest_lng}` with coordinates rounded to 6 decimal places (~11 cm — finer than any GPS input, so rounding never merges genuinely different points).

| Rule | Value |
|---|---|
| TTL | `ROUTE_CACHE_TTL_SECONDS`, default 86400 (24 h) |
| Invalidation on service location move | Immediate — all keys containing that location's point are deleted |
| Invalidation on customer location move | Immediate for that customer's pairs |
| Bypass | `force_refresh=true` on the check endpoint (ADMIN/SUPERVISOR/OPERATIONS) |
| Never cached | Failed lookups; a transient provider error must not be sticky |

Road networks change slowly; 24 h is a deliberate balance between cost and freshness. Because a moved location invalidates eagerly, the stale-data risk is limited to genuine road-network changes, which do not shift distances materially within a day.

---

## 6. Failure handling matrix (brief §18)

| Failure | Retry | Result status | Customer record |
|---|---|---|---|
| Geocoding returns zero results | No | `LOCATION_VERIFICATION_REQUIRED` | Created, no coordinates |
| Geocoding partial match / low confidence | No | `LOCATION_VERIFICATION_REQUIRED` | Created, coordinates set, flagged for confirmation |
| Geocoding provider 5xx / timeout | 3× exponential backoff | `LOCATION_VERIFICATION_REQUIRED` | Created, no coordinates |
| Routing timeout | 3× exponential backoff + jitter | `ROUTE_CALCULATION_ERROR` | Preserved, retryable |
| Routing 429 | Honour `Retry-After`, then backoff | `ROUTE_CALCULATION_ERROR` | Preserved, retryable |
| Routing 5xx | 3× backoff, then fallback provider if configured | `ROUTE_CALCULATION_ERROR` | Preserved, retryable |
| Circuit breaker open | No call attempted | `ROUTE_CALCULATION_ERROR` | Preserved, retryable |
| Some destinations unreachable | No | Decision on reachable legs, `degraded=true` | Normal |
| All destinations unreachable | No | `ROUTE_CALCULATION_ERROR` | Preserved, retryable |
| Directions call fails after distance succeeded | 1× | Decision stands, geometry NULL | Normal |
| Database write fails | Transaction rollback | HTTP 500 | No partial state |

The single most important row in this table: **no provider failure ever produces `NOT_AVAILABLE`.** A failure to measure is not a measurement of distance. `ROUTE_CALCULATION_ERROR` is asserted in test 8.

---

## 7. Threshold configurability (brief §3, §21)

`SERVICEABILITY_RADIUS_METERS` lives in the `app_config` table, seeded to `2000`. It is read fresh from the database at the start of every check — no process-level caching that would let two API replicas disagree after a change. Changing it requires the ADMIN role and a written reason, and writes a `config_audit` row.

The constant `2000` appears in exactly two places in the codebase: the seed migration, and the test that asserts the seeded default. Nowhere in the decision path is it literal.

The architecture anticipates §32's future rules — per-service-type and per-area thresholds — by resolving the threshold through `ConfigService.get_threshold_meters(service_type=None, area=None)`. Today both arguments are ignored and the global value is returned; adding an override table later changes one method, not the engine.
