from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

PROMO_FLOOR_MULT = Decimal("1.10")
PROMO_STEP_MULT = Decimal("0.12")
STOREFRONT_WATCH_MULT = Decimal("1.05")
QUARANTINE_THRESHOLD = Decimal("0.20")


def compute_promo_floor(optimal: Decimal) -> Decimal:
    return optimal * PROMO_FLOOR_MULT


def compute_promo_step(optimal: Decimal) -> Decimal:
    return optimal * PROMO_STEP_MULT


def compute_new_promo_price(current_promo: Decimal, optimal: Decimal) -> Decimal:
    floor = compute_promo_floor(optimal)
    step = compute_promo_step(optimal)
    return max(current_promo - step, floor)


def should_lower_promo_price(storefront: Decimal, optimal: Decimal) -> bool:
    return storefront > optimal * STOREFRONT_WATCH_MULT


def should_alert_below_optimal(storefront: Decimal, optimal: Decimal) -> bool:
    return storefront < optimal


def is_quarantine_risk(current_catalog: Decimal, new_catalog: Decimal) -> bool:
    if current_catalog <= 0:
        return False
    drop_ratio = (current_catalog - new_catalog) / current_catalog
    return drop_ratio > QUARANTINE_THRESHOLD


@dataclass
class CatalogUpdate:
    sku: str
    new_value: Decimal
    new_discount_base: Decimal
    minimum_for_bestseller: Decimal
    quarantine_risk: bool


def compute_catalog_update(
    sku: str,
    db_catalog: Decimal,
    db_crossed: Decimal,
    db_optimal: Decimal,
    market_catalog: Decimal,
) -> Optional[CatalogUpdate]:
    if db_catalog == market_catalog:
        return None
    return CatalogUpdate(
        sku=sku,
        new_value=db_catalog,
        new_discount_base=db_crossed,
        minimum_for_bestseller=db_optimal,
        quarantine_risk=is_quarantine_risk(market_catalog, db_catalog),
    )


@dataclass
class StorefrontDecision:
    sku: str
    action: str  # "lower" | "alert_below_optimal" | "alert_floor_breach" | "skip" | "ok"
    new_promo_price: Optional[Decimal] = None
    storefront: Optional[Decimal] = None
    optimal: Optional[Decimal] = None


def evaluate_storefront(
    sku: str,
    storefront: Decimal,
    optimal: Decimal,
    current_promo: Decimal,
    is_pr: bool,
) -> StorefrontDecision:
    if is_pr:
        return StorefrontDecision(sku=sku, action="skip")

    floor = compute_promo_floor(optimal)

    if current_promo < floor:
        return StorefrontDecision(
            sku=sku, action="alert_floor_breach",
            storefront=storefront, optimal=optimal,
        )

    if should_alert_below_optimal(storefront, optimal):
        return StorefrontDecision(
            sku=sku, action="alert_below_optimal",
            storefront=storefront, optimal=optimal,
        )

    if should_lower_promo_price(storefront, optimal):
        new_promo = compute_new_promo_price(current_promo, optimal)
        return StorefrontDecision(
            sku=sku, action="lower",
            new_promo_price=new_promo,
            storefront=storefront, optimal=optimal,
        )

    return StorefrontDecision(sku=sku, action="ok")
