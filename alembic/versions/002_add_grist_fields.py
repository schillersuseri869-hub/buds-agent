"""add grist_row_id and min_stock to raw_materials

Revision ID: 002
Revises: 001
Create Date: 2026-05-03
"""
import sqlalchemy as sa
from alembic import op

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('raw_materials', sa.Column('grist_row_id', sa.Integer(), nullable=True))
    op.add_column('raw_materials', sa.Column('min_stock', sa.Numeric(12, 3), nullable=True))


def downgrade() -> None:
    op.drop_column('raw_materials', 'min_stock')
    op.drop_column('raw_materials', 'grist_row_id')
