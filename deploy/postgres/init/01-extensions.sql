-- Runs once, on first initialisation of an empty data directory.
--
-- Alembic also creates these extensions, so this file is belt-and-braces: it
-- means a psql session against a fresh database has PostGIS available before
-- migrations have run, which makes early troubleshooting easier.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Log any statement slower than a second. The candidate search should be
-- well under a millisecond; anything appearing here is worth investigating.
ALTER SYSTEM SET log_min_duration_statement = '1000ms';
