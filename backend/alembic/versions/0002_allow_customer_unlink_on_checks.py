"""Allow one narrow exception to the checks-append-only guard.

serviceability_checks.customer_id has ON DELETE SET NULL, so deleting a
customer issues an UPDATE against serviceability_checks to null out that
column -- and the append-only trigger from 0001 blocked that UPDATE
unconditionally, which meant a customer with any check history could never
be deleted at all (see delete_check in app/api/v1/serviceability.py).

This relaxes the guard to allow exactly that one case -- customer_id
transitioning from set to NULL, with every other column byte-for-byte
unchanged -- while still rejecting any DELETE and any other UPDATE. The
decision data itself (result, distance, route, etc.) remains immutable.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-10
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_check_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.customer_id IS NOT NULL
               AND NEW.customer_id IS NULL
               AND (to_jsonb(NEW) - 'customer_id') = (to_jsonb(OLD) - 'customer_id')
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'serviceability_checks is append-only; % is not permitted. '
                'Historic serviceability decisions must never be altered or removed.',
                TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
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
