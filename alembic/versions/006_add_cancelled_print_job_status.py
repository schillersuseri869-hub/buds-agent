"""add cancelled to print_job_status enum

Revision ID: 006
Revises: 005
Create Date: 2026-05-06
"""
from alembic import op

revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE print_job_status ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    pass
