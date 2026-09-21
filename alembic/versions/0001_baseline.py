"""baseline — empty schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-20

Baseline before any domain tables. First real migration lands with the
wallet schema (Phase 3) and follows expand → migrate → verify → contract
(§73): never edit an already-applied migration.
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Intentionally empty — establishes the revision chain.
    pass


def downgrade() -> None:
    pass
