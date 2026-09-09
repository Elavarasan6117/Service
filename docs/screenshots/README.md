# Screenshots

Captured from the running application by `backend/scripts/screenshots.py`
(Playwright + Chromium). To regenerate:

```bash
# terminal 1 — backend
cd backend
python scripts/demo_local.py
DATABASE_URL_OVERRIDE="sqlite+aiosqlite:///$PWD/demo.db" \
  ROUTING_PROVIDER=fake GEOCODING_PROVIDER=fake \
  SECRET_KEY=local-demo-secret-key-not-for-real-use-1234 \
  uvicorn app.main:app --port 8000

# terminal 2 — frontend
cd frontend && npm install && npm run dev

# terminal 3 — capture
cd backend && python scripts/screenshots.py ../docs/screenshots
```

| File | Shows |
|---|---|
| `01-login.png` | Sign-in |
| `02-dashboard.png` | Operations dashboard before any check |
| `03-form-filled.png` | New-customer form completed, marker placed |
| `04-available.png` | SERVICE AVAILABLE decision with the route drawn |
| `05-available-closeup.png` | The decision panel on its own |
| `06-not-available.png` | SERVICE NOT AVAILABLE, 2.34 km against a 2.00 km limit |
| `07-not-available-closeup.png` | The rejection panel on its own |
| `08-recent-checks.png` | Audit history table |
| `09-admin.png` | Administration: threshold, configuration, provider health |
| `10-dashboard-dark.png` | Dark theme |

## Two things these images do not show

**Map tiles.** They were captured in a sandbox with no route to
`tile.openstreetmap.org`, so the map background is blank. Markers, the route
polyline, popups and the legend all render normally; on a machine with
internet access the Chennai street map appears behind them.

**Real road distances.** These runs use the fake routing provider, which
derives road distance from straight-line distance times a fixed detour ratio.
The decision logic, thresholds, audit trail and UI are all real; the distances
are not. Only a Docker run with a live Google Maps key produces distances you
should act on.
