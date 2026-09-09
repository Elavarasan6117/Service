"""Initial schema: PostGIS, core tables, spatial indexes, config seed.

Revision ID: 0001
Revises:
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

GEOG = Geography(geometry_type="POINT", srid=4326, spatial_index=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---- users ---------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("failed_login_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"])

    # ---- warehouses ----------------------------------------------------
    op.create_table(
        "warehouses",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("warehouse_code", sa.String(64), nullable=False, unique=True),
        sa.Column("warehouse_name", sa.String(255), nullable=False),
        sa.Column("address", sa.Text),
        sa.Column("city", sa.String(128)),
        sa.Column("pincode", sa.String(16)),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=False),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=False),
        sa.Column("location", GEOG),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_warehouses_lat"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_warehouses_lng"),
    )
    op.create_index("ix_warehouses_warehouse_code", "warehouses", ["warehouse_code"])

    # ---- routes --------------------------------------------------------
    op.create_table(
        "routes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("route_code", sa.String(64), nullable=False, unique=True),
        sa.Column("route_name", sa.String(255), nullable=False),
        sa.Column("service_area", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_routes_route_code", "routes", ["route_code"])
    op.create_index("ix_routes_service_area", "routes", ["service_area"])

    # ---- import batches (before service_locations, which references it) --
    op.create_table(
        "import_batches",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("total_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("valid_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("invalid_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("geocoded_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duplicate_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("geocode_failed_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("committed_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("report", sa.dialects.postgresql.JSONB),
        sa.Column("created_by", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_label", sa.String(255), nullable=False, server_default="SYSTEM"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_import_batches_created_at", "import_batches", ["created_at"])

    # ---- customers -----------------------------------------------------
    op.create_table(
        "customers",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_code", sa.String(64), nullable=False, unique=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(32)),
        sa.Column("address", sa.Text, nullable=False),
        sa.Column("formatted_address", sa.Text),
        sa.Column("area", sa.String(128)),
        sa.Column("city", sa.String(128), nullable=False, server_default="Chennai"),
        sa.Column("pincode", sa.String(16)),
        sa.Column("service_type", sa.String(64)),
        sa.Column("latitude", sa.Numeric(10, 7)),
        sa.Column("longitude", sa.Numeric(10, 7)),
        sa.Column("location", GEOG),
        sa.Column("coordinate_source", sa.String(32)),
        sa.Column("service_status", sa.String(48), nullable=False, server_default="PENDING"),
        sa.Column("nearest_service_location_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("nearest_service_distance_meters", sa.Integer),
        sa.Column("last_check_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_customers_lat"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_customers_lng"),
        sa.CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_customers_latlng_pair"),
        sa.CheckConstraint(
            "nearest_service_distance_meters IS NULL OR nearest_service_distance_meters >= 0",
            name="ck_customers_distance_non_negative",
        ),
    )
    op.create_index("ix_customers_customer_code", "customers", ["customer_code"])
    op.create_index("ix_customers_service_status", "customers", ["service_status"])
    op.create_index("ix_customers_area", "customers", ["area"])
    op.create_index("ix_customers_created_at", "customers", ["created_at"])

    # ---- service_locations ---------------------------------------------
    op.create_table(
        "service_locations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("service_code", sa.String(64), nullable=False, unique=True),
        sa.Column("customer_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("warehouse_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("warehouses.id", ondelete="SET NULL")),
        sa.Column("route_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("routes.id", ondelete="SET NULL")),
        sa.Column("import_batch_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("import_batches.id", ondelete="SET NULL")),
        sa.Column("location_name", sa.String(255), nullable=False),
        sa.Column("address", sa.Text),
        sa.Column("area", sa.String(128)),
        sa.Column("city", sa.String(128), server_default="Chennai"),
        sa.Column("pincode", sa.String(16)),
        sa.Column("latitude", sa.Numeric(10, 7)),
        sa.Column("longitude", sa.Numeric(10, 7)),
        sa.Column("location", GEOG),
        sa.Column("service_area", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("coordinate_source", sa.String(32), nullable=False, server_default="PROVIDED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_service_locations_lat"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_service_locations_lng"),
        sa.CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_service_locations_latlng_pair"),
    )
    op.create_index("ix_service_locations_service_code", "service_locations", ["service_code"])
    op.create_index("ix_service_locations_status", "service_locations", ["status"])
    op.create_index("ix_service_locations_area", "service_locations", ["service_area"])
    op.create_index("ix_service_locations_route", "service_locations", ["route_id"])

    op.create_foreign_key(
        "fk_customers_nearest_service_location",
        "customers",
        "service_locations",
        ["nearest_service_location_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ---- serviceability_checks (append-only audit) ----------------------
    op.create_table(
        "serviceability_checks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("nearest_service_location_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("service_locations.id", ondelete="SET NULL")),
        sa.Column("customer_latitude", sa.Numeric(10, 7)),
        sa.Column("customer_longitude", sa.Numeric(10, 7)),
        sa.Column("calculated_distance_meters", sa.Integer),
        sa.Column("distance_type", sa.String(32), nullable=False, server_default="ROAD_DISTANCE"),
        sa.Column("routing_provider", sa.String(64)),
        sa.Column("route_duration_seconds", sa.Integer),
        sa.Column("route_geometry", sa.Text),
        sa.Column("route_geometry_format", sa.String(32)),
        sa.Column("threshold_meters", sa.Integer, nullable=False),
        sa.Column("result", sa.String(48), nullable=False),
        sa.Column("candidate_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("candidate_radius_meters", sa.Integer),
        sa.Column("cache_hit", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("degraded", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_detail", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("request_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_timestamp", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("created_by", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_label", sa.String(255), nullable=False, server_default="SYSTEM"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("threshold_meters > 0", name="ck_checks_threshold_positive"),
        sa.CheckConstraint(
            "calculated_distance_meters IS NULL OR calculated_distance_meters >= 0",
            name="ck_checks_distance_non_negative",
        ),
        # The system's core invariant, enforced by the database rather than
        # only by application code: an AVAILABLE/NOT_AVAILABLE decision can
        # only ever be recorded against a road-distance measurement.
        sa.CheckConstraint(
            "result NOT IN ('AVAILABLE', 'NOT_AVAILABLE') "
            "OR (distance_type = 'ROAD_DISTANCE' AND calculated_distance_meters IS NOT NULL)",
            name="ck_checks_decision_requires_road_distance",
        ),
    )
    op.create_index("ix_checks_created_at", "serviceability_checks", ["created_at"])
    op.create_index("ix_checks_customer_id", "serviceability_checks", ["customer_id"])
    op.create_index("ix_checks_result", "serviceability_checks", ["result"])
    op.create_index("ix_checks_customer_created", "serviceability_checks", ["customer_id", "created_at"])
    op.create_index("ix_checks_result_created", "serviceability_checks", ["result", "created_at"])

    # ---- import rows ---------------------------------------------------
    op.create_table(
        "import_rows",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("row_number", sa.Integer, nullable=False),
        sa.Column("raw_data", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("errors", sa.dialects.postgresql.JSONB),
        sa.Column("warnings", sa.dialects.postgresql.JSONB),
        sa.Column("resolved_latitude", sa.Numeric(10, 7)),
        sa.Column("resolved_longitude", sa.Numeric(10, 7)),
        sa.Column("coordinate_source", sa.String(32)),
        sa.Column("created_service_location_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("service_locations.id", ondelete="SET NULL")),
    )
    op.create_index("ix_import_rows_batch_id", "import_rows", ["batch_id"])

    # ---- configuration -------------------------------------------------
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("requires_admin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("min_value", sa.String(64)),
        sa.Column("max_value", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "config_audit",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("config_key", sa.String(128), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text, nullable=False),
        sa.Column("changed_by", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("changed_by_label", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_config_audit_config_key", "config_audit", ["config_key"])
    op.create_index("ix_config_audit_created_at", "config_audit", ["created_at"])

    # ---- spatial indexes ------------------------------------------------
    # These are what make Stage-1 candidate search O(log n) instead of O(n).
    op.execute("CREATE INDEX ix_service_locations_location_gist ON service_locations USING GIST (location)")
    op.execute(
        "CREATE INDEX ix_service_locations_active_location ON service_locations "
        "USING GIST (location) WHERE status = 'ACTIVE'"
    )
    op.execute("CREATE INDEX ix_customers_location_gist ON customers USING GIST (location)")
    op.execute("CREATE INDEX ix_warehouses_location_gist ON warehouses USING GIST (location)")
    op.execute(
        "CREATE INDEX ix_customers_name_trgm ON customers USING GIN (customer_name gin_trgm_ops)"
    )

    # ---- trigger keeping `location` in step with lat/lng ------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_location_from_latlng() RETURNS trigger AS $$
        BEGIN
            IF NEW.latitude IS NULL OR NEW.longitude IS NULL THEN
                NEW.location := NULL;
            ELSE
                -- ST_MakePoint takes (x, y) = (longitude, latitude).
                -- Reversing these silently relocates every Chennai point.
                NEW.location := ST_SetSRID(
                    ST_MakePoint(NEW.longitude::float8, NEW.latitude::float8), 4326
                )::geography;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("customers", "service_locations", "warehouses"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_sync_location
            BEFORE INSERT OR UPDATE OF latitude, longitude ON {table}
            FOR EACH ROW EXECUTE FUNCTION sync_location_from_latlng();
            """
        )

    # ---- guard: serviceability_checks is append-only ---------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_check_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'serviceability_checks is append-only; % is not permitted. '
                'Historic serviceability decisions must never be altered or removed.',
                TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_checks_append_only
        BEFORE UPDATE OR DELETE ON serviceability_checks
        FOR EACH ROW EXECUTE FUNCTION forbid_check_mutation();
        """
    )

    # ---- seed configuration ---------------------------------------------
    op.execute(
        """
        INSERT INTO app_config (key, value, value_type, description, requires_admin, min_value, max_value)
        VALUES
        ('SERVICEABILITY_RADIUS_METERS', '2000', 'INT',
         'Maximum ACTUAL ROAD DISTANCE in metres for a new customer to be serviceable. '
         'A distance exactly equal to this value is AVAILABLE.', true, '100', '100000'),

        ('CANDIDATE_RADIUS_FACTOR', '4.0', 'FLOAT',
         'Stage-1 search radius as a multiple of the threshold. Must be >= 1.0; '
         'road distance is always >= straight-line distance, so a factor below 1 '
         'could discard a location that is actually within range.', true, '1.0', '20.0'),

        ('CANDIDATE_MAX_COUNT', '10', 'INT',
         'Maximum candidates sent to the routing provider per check. Caps cost per check '
         'regardless of how many service locations exist.', false, '1', '25'),

        ('ROUTE_CACHE_TTL_SECONDS', '86400', 'INT',
         'How long a measured road distance between a pair of points stays cached. '
         'Moving a location invalidates its entries immediately regardless of this value.',
         false, '0', '604800'),

        ('SERVICEABILITY_EMPTY_TABLE_RESULT', 'NO_SERVICE_LOCATION_CONFIGURED', 'STRING',
         'Status returned when no ACTIVE service locations exist at all. '
         'Set to NOT_AVAILABLE if the business prefers that reading.', true, NULL, NULL),

        ('AUTO_CHECK_ON_CUSTOMER_CREATE', 'true', 'BOOL',
         'Run the serviceability check automatically when a customer is created.',
         false, NULL, NULL),

        ('AUTOMATIC_DECISION_ENABLED', 'true', 'BOOL',
         'Master switch for automated decisions. Set to false during an incident to '
         'route all new customers to manual review (rollback runbook step 1) without '
         'taking the application down.', true, NULL, NULL)
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_checks_append_only ON serviceability_checks")
    op.execute("DROP FUNCTION IF EXISTS forbid_check_mutation()")
    for table in ("customers", "service_locations", "warehouses"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_sync_location ON {table}")
    op.execute("DROP FUNCTION IF EXISTS sync_location_from_latlng()")

    op.drop_table("config_audit")
    op.drop_table("app_config")
    op.drop_table("import_rows")
    op.drop_table("serviceability_checks")
    op.drop_constraint("fk_customers_nearest_service_location", "customers", type_="foreignkey")
    op.drop_table("service_locations")
    op.drop_table("customers")
    op.drop_table("import_batches")
    op.drop_table("routes")
    op.drop_table("warehouses")
    op.drop_table("users")
