"""add defect and inventory_correction movement types

Revision ID: 001
Revises:
Create Date: 2026-05-03
"""
from alembic import op

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE movement_type ADD VALUE IF NOT EXISTS 'defect'")
    op.execute("ALTER TYPE movement_type ADD VALUE IF NOT EXISTS 'inventory_correction'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    pass
