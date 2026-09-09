#!/usr/bin/env bash
# Prepare a self-hosted OSRM routing engine for South India.
#
# Only needed if you set ROUTING_PROVIDER=osrm or configure OSRM as the
# fallback. Requires roughly 8 GB of free RAM during preprocessing and about
# 6 GB of disk. Takes 20-60 minutes depending on the machine.
set -euo pipefail

cd "$(dirname "$0")/.."
DATA_DIR="deploy/osrm-data"
EXTRACT="southern-zone-latest.osm.pbf"
URL="https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf"

mkdir -p "$DATA_DIR"

if [ ! -f "${DATA_DIR}/${EXTRACT}" ]; then
  echo "[osrm] downloading South India extract (~400 MB)"
  curl -fL --progress-bar -o "${DATA_DIR}/${EXTRACT}" "$URL"
else
  echo "[osrm] extract already present, skipping download"
fi

IMAGE=ghcr.io/project-osrm/osrm-backend:latest
run() { docker run --rm -t -v "$(pwd)/${DATA_DIR}:/data" "$IMAGE" "$@"; }

echo "[osrm] extracting with the car profile (slowest step)"
run osrm-extract -p /opt/car.lua "/data/${EXTRACT}"

echo "[osrm] partitioning"
run osrm-partition "/data/southern-zone-latest.osrm"

echo "[osrm] customising"
run osrm-customize "/data/southern-zone-latest.osrm"

cat <<'EOF'

[osrm] done.

Start it and point the application at it:

    docker compose --profile osrm up -d osrm

    # in .env
    ROUTING_PROVIDER=osrm
    # or, to keep Google primary and OSRM as a fallback:
    ROUTING_FALLBACK_PROVIDER=osrm

    docker compose up -d backend

Then verify:

    curl -s -X POST https://<host>/api/v1/admin/routing/health \
      -H "Authorization: Bearer $TOKEN" | jq

Note: OSRM does not geocode. Keep GEOCODING_PROVIDER=google_maps, or add a
Nominatim adapter.

Before switching production to OSRM, re-run the manual road-distance
validation in the deployment runbook (§3.8). OSM road data for Chennai is
good but not identical to Google's, and distances will differ slightly.
EOF
