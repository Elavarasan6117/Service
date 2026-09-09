# Chennai Serviceability & Existing Service Location Proximity System

Determines whether a new customer is serviceable, based on the **actual driving distance** by road to the nearest existing service location.

```
ACTUAL ROAD DISTANCE <= 2 KM   →   SERVICE AVAILABLE
ACTUAL ROAD DISTANCE >  2 KM   →   SERVICE NOT AVAILABLE
```

Straight-line distance is used only to pre-select which locations are worth measuring. It never decides. That constraint is enforced in three independent places: a runtime type guard in the engine, a database `CHECK` constraint on the audit table, and a test that asserts a straight-line measurement raises rather than returning an answer.

---

## Quick start

**Windows, no command line:** extract the zip, open the `serviceability` folder and
double-click **`START-HERE-Windows.bat`**. It checks for Python and Node.js, tells you what to
install if either is missing, sets everything up inside the folder and opens the app. See
`READ-ME-FIRST.txt`.

**Everything else:**

```bash
cp .env.example .env
# Set at minimum: SECRET_KEY, POSTGRES_PASSWORD, BOOTSTRAP_ADMIN_PASSWORD,
# and GOOGLE_MAPS_API_KEY.

docker compose up -d --build
docker compose exec backend python scripts/seed.py --demo --count 200
```

Then open **http://localhost:8080** and sign in with `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD`.

**Without a Google key**, set `ROUTING_PROVIDER=fake` and `GEOCODING_PROVIDER=photon` in `.env` for local address lookup against Komoot's public Photon instance (same OpenStreetMap data as Nominatim, but built for application traffic rather than light manual use) and synthetic road distances. `GEOCODING_PROVIDER=nominatim` also works but talks to the raw osm.org endpoint directly, whose usage policy will start rejecting requests under any real usage. The application refuses to start in production with the fake provider selected.

> The seeded service locations are **synthetic** — plausible points in real Chennai localities, not the operational network. Replace them with the real data through the importer before UAT. See §9 of the architecture document for how to digitise the existing map.

---

## Documentation

| Document | What it covers |
|---|---|
| [01 — Architecture](docs/01-architecture.md) | Component and sequence diagrams, technology choices with reasons, **stated assumptions** |
| [02 — Data model](docs/02-data-model.md) | ER diagram, schema, indexes, why each table looks the way it does |
| [03 — API specification](docs/03-api-specification.md) | Every endpoint, the role matrix, response shapes, error envelope |
| [04 — Serviceability algorithm](docs/04-serviceability-algorithm.md) | The normative decision specification, cache policy, failure matrix |
| [05 — Deployment runbook](docs/05-deployment-runbook.md) | Production setup, verification with pass conditions, go-live checklist |
| [06 — Rollback runbook](docs/06-rollback-runbook.md) | Incident procedures, including wrong-decision recovery |
| [07 — User guide](docs/07-user-guide.md) | For the operations team. No technical background assumed |

Interactive API docs are at `/api/v1/docs` in non-production environments.

---

## Stack

| Layer | Choice |
|---|---|
| Database | PostgreSQL 16 + PostGIS 3.4 (GiST spatial indexes, KNN candidate search) |
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.0 async, Alembic |
| Cache / pub-sub | Redis 7 |
| Frontend | React 18, TypeScript, Vite, Leaflet + OpenStreetMap tiles |
| Routing / geocoding | Google Maps Platform (swappable — OSRM adapter included) |
| Real-time | Server-Sent Events over Redis pub/sub |
| Monitoring | Prometheus + Grafana, structured JSON logs |

---

## How a check works

```
New customer coordinates
        │
        ▼
Stage 1  PostGIS KNN search within (threshold × 4), capped at 10 candidates
        │                                   ← no external calls, no cost
        ▼
Stage 2  Route cache lookup, then ONE Distance Matrix request for the misses
        │                                   ← 1 origin × N destinations
        ▼
Nearest by ROAD distance  (frequently not the nearest in a straight line)
        │
        ▼
distance_meters <= threshold_meters   ← integers, exact at the boundary
        │
        ▼
AVAILABLE / NOT_AVAILABLE  →  audit row  →  live map
```

Cost per check is constant whether there are 100 or 100,000 service locations: at most one geocode, one matrix request and one directions request. With a warm cache, often zero routing calls.

---

## Repository layout

```
backend/
  app/
    api/v1/       HTTP layer — no business logic
    core/         config, enums, security, logging, metrics, errors
    db/           session, base, spatial types
    models/       SQLAlchemy models
    providers/    ports + Google / OSRM / fake adapters + resilience wrapper
    schemas/      Pydantic request/response contracts
    services/     serviceability engine, spatial search, customers, import,
                  config, cache, events
  alembic/        migrations (schema, indexes, triggers, config seed)
  tests/          57 tests including all 10 acceptance scenarios
  scripts/        entrypoint, seed, backup
frontend/
  src/            React + TypeScript SPA, Leaflet map
deploy/
  nginx/          edge proxy, CSP, SSE-aware config
  prometheus/     scrape config and alert rules
docs/             architecture, data model, API, algorithm, runbooks, user guide
sample_data/      example import file
```

---

## Tests

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest              # 57 tests, ~14 s, no external calls
python -m pytest --cov=app
```

The suite runs against SQLite with fake providers, so the boundary assertions are exact and free. The ten scenarios from the brief map one-to-one onto tests in `tests/test_acceptance_scenarios.py`:

| Scenario | Test |
|---|---|
| 500 m → AVAILABLE | `test_1_five_hundred_metres_is_available` |
| 1.5 km → AVAILABLE | `test_2_one_point_five_km_is_available` |
| **Exactly 2000 m → AVAILABLE** | `test_3_exactly_two_thousand_metres_is_available` |
| 2.1 km → NOT_AVAILABLE | `test_4_two_point_one_km_is_not_available` |
| **Road-nearest ≠ geo-nearest** | `test_5_nearest_is_chosen_by_road_distance_not_straight_line` |
| No service locations | `test_6_no_service_locations_configured` |
| Invalid address | `test_7_invalid_address_requires_verification` |
| **Routing failure ≠ NOT_AVAILABLE** | `test_8_routing_failure_is_not_not_available` |
| Marker moved | `test_9_manual_marker_move_recalculates` |
| Service location moved | `test_10_moved_service_location_is_used_in_new_checks` |

`tests/test_spatial_postgis.py` covers the real PostGIS path and is skipped unless `TEST_POSTGRES_URL` is set:

```bash
TEST_POSTGRES_URL=postgresql+asyncpg://user:pass@localhost:5432/test python -m pytest -m postgis
```

---

## Security posture

- **No mapping API key reaches the browser.** The map uses Leaflet over OSM tiles, which needs no key; geocoding, autocomplete, distance and directions are all proxied by the backend. Verified in the deployment runbook (§3.10).
- JWT authentication, five roles, permission checks as FastAPI dependencies.
- The 2 km threshold is ADMIN-only and every change is audited with a mandatory reason.
- Redis token-bucket rate limiting, shared across API replicas.
- Pydantic validation on every request; SQLAlchemy parameter binding throughout.
- Secrets from the environment only; `.env` is gitignored; API keys are redacted from logs by a structlog processor.
- Production startup refuses debug mode, enabled docs, non-HTTPS, short secrets, default passwords, or the fake provider.

---

## Cost control

Routing APIs are billed per element. The controls are:

| Control | Setting |
|---|---|
| Spatial pre-filter before any paid call | Stage 1, always on |
| Candidate cap per check | `CANDIDATE_MAX_COUNT` (10) |
| One matrix request per check, not one per candidate | Provider adapters |
| Result cache | `ROUTE_CACHE_TTL_SECONDS` (24 h) |
| Eager cache invalidation when a location moves | Automatic |
| Per-user rate limits | `RATE_LIMIT_*` |
| Circuit breaker on repeated failure | `PROVIDER_CIRCUIT_*` |
| Spend visibility | `provider_elements_total` in Prometheus |

Set daily quota caps in Google Cloud Console as well. Application-level controls reduce spend; only the provider's own cap makes an upper bound enforceable.

---

## Extending it

The architecture anticipates the enhancements in §32 of the brief:

- **Different thresholds by service type or area** — `ConfigService.get_threshold_meters()` already takes both arguments and ignores them. Add an override table; the engine does not change.
- **Another routing provider** — implement the `RoutingProvider` protocol, register it in `providers/registry.py`, change one environment variable.
- **Multiple warehouses** — `warehouses` is already a table with a foreign key from `service_locations`.
- **Vehicle tracking, capacity rules, notifications** — separate modules behind the existing service boundaries. The engine's decision function stays a pure comparison of two integers.
