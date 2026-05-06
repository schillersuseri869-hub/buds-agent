"""add returned to order_status enum

Revision ID: 005
Revises: 004
Create Date: 2026-05-06
"""
from alembic import op

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'returned'")


def downgrade() -> None:
    pass
