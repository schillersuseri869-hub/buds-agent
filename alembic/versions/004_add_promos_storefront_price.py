"""add promos table, storefront_price, v_pricing_report view

Revision ID: 004
Revises: 003
Create Date: 2026-05-04
"""
import sqlalchemy as sa
from alembic import op

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'promos',
        sa.Column('promo_id', sa.String(200), primary_key=True),
        sa.Column('name', sa.String(500), nullable=False),
        sa.Column('type', sa.String(50), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.add_column(
        'market_products',
        sa.Column('storefront_price', sa.Numeric(12, 2), nullable=True),
    )

    op.execute("""
        CREATE OR REPLACE VIEW v_pricing_report AS
        SELECT
            mp.name,
            mp.market_sku,
            mp.catalog_price,
            ROUND(mp.optimal_price * 1.10, 0)  AS min_promo_price,
            mp.storefront_price,
            mp.optimal_price,
            pp.promo_price,
            pp.promo_type,
            CASE
                WHEN pp.promo_price IS NOT NULL AND mp.catalog_price > 0
                THEN ROUND((1 - pp.promo_price / mp.catalog_price) * 100)
                ELSE NULL
            END AS discount_pct,
            pr.name       AS promo_name,
            pr.type       AS promo_type_name,
            pr.starts_at,
            pr.ends_at,
            CASE
                WHEN pp.promo_price IS NULL
                    THEN 'no_promo'
                WHEN pp.promo_price < mp.optimal_price * 1.10
                    THEN 'danger'
                WHEN mp.storefront_price > mp.optimal_price * 1.05
                    THEN 'warning'
                WHEN mp.storefront_price < mp.optimal_price
                    THEN 'info'
                ELSE 'ok'
            END AS status
        FROM market_products mp
        LEFT JOIN promo_participations pp ON pp.product_id = mp.id
        LEFT JOIN promos pr ON pr.promo_id = pp.promo_id
        ORDER BY
            CASE WHEN pp.promo_price < mp.optimal_price * 1.10 THEN 0
                 WHEN mp.storefront_price > mp.optimal_price * 1.05 THEN 1
                 WHEN mp.storefront_price < mp.optimal_price THEN 2
                 WHEN pp.promo_price IS NOT NULL THEN 3
                 ELSE 4 END,
            mp.name
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_pricing_report")
    op.drop_column('market_products', 'storefront_price')
    op.drop_table('promos')
