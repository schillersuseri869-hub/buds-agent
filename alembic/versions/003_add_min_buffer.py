"""add min_buffer to raw_materials

Revision ID: 003
Revises: 002
Create Date: 2026-05-04
"""
import sqlalchemy as sa
from alembic import op

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'raw_materials',
        sa.Column('min_buffer', sa.Numeric(12, 3), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('raw_materials', 'min_buffer')
