from decimal import Decimal
import pytest

from app.agents.pricing_agent.price_engine import (
    PROMO_FLOOR_MULT,
    PROMO_STEP_MULT,
    STOREFRONT_WATCH_MULT,
    compute_promo_floor,
    compute_new_promo_price,
    should_lower_promo_price,
    should_alert_below_optimal,
    compute_catalog_update,
    is_quarantine_risk,
    CatalogUpdate,
    StorefrontDecision,
    evaluate_storefront,
)


def test_compute_promo_floor():
    assert compute_promo_floor(Decimal("1000")) == Decimal("1100")


def test_compute_new_promo_price_normal():
    # current=1500, step=120, floor=1100 → 1380
    result = compute_new_promo_price(Decimal("1500"), Decimal("1000"))
    assert result == Decimal("1380")


def test_compute_new_promo_price_respects_floor():
    # current=1150, step=120, floor=1100 → clamped to 1100
    result = compute_new_promo_price(Decimal("1150"), Decimal("1000"))
    assert result == Decimal("1100")


def test_should_lower_promo_price_true_when_above_watch():
    # storefront=1200 > optimal*1.05=1050 → True
    assert should_lower_promo_price(Decimal("1200"), Decimal("1000")) is True


def test_should_lower_promo_price_false_when_at_watch():
    # storefront=1050 == optimal*1.05 → False
    assert should_lower_promo_price(Decimal("1050"), Decimal("1000")) is False


def test_should_alert_below_optimal():
    assert should_alert_below_optimal(Decimal("999"), Decimal("1000")) is True
    assert should_alert_below_optimal(Decimal("1000"), Decimal("1000")) is False


def test_compute_catalog_update_detects_mismatch():
    update = compute_catalog_update(
        sku="SKU-001",
        db_catalog=Decimal("1500"),
        db_crossed=Decimal("2100"),
        db_optimal=Decimal("1000"),
        market_catalog=Decimal("1400"),
    )
    assert update is not None
    assert update.sku == "SKU-001"
    assert update.new_value == Decimal("1500")


def test_compute_catalog_update_no_change_when_equal():
    result = compute_catalog_update(
        sku="SKU-001",
        db_catalog=Decimal("1500"),
        db_crossed=Decimal("2100"),
        db_optimal=Decimal("1000"),
        market_catalog=Decimal("1500"),
    )
    assert result is None


def test_is_quarantine_risk_large_drop():
    assert is_quarantine_risk(Decimal("2500"), Decimal("1800")) is True


def test_is_quarantine_risk_small_drop():
    assert is_quarantine_risk(Decimal("2500"), Decimal("2200")) is False


def test_evaluate_storefront_lower_action():
    decision = evaluate_storefront(
        sku="SKU-001",
        storefront=Decimal("1300"),
        optimal=Decimal("1000"),
        current_promo=Decimal("1200"),
        is_pr=False,
    )
    assert decision.action == "lower"
    assert decision.new_promo_price == Decimal("1100")  # max(1200-120, 1100) = max(1080, 1100) = 1100


def test_evaluate_storefront_below_min_action():
    decision = evaluate_storefront(
        sku="SKU-001",
        storefront=Decimal("900"),
        optimal=Decimal("1000"),
        current_promo=Decimal("1200"),
        is_pr=False,
    )
    assert decision.action == "alert_below_optimal"


def test_evaluate_storefront_pr_sku_no_lower():
    decision = evaluate_storefront(
        sku="PR-001",
        storefront=Decimal("1500"),
        optimal=Decimal("1000"),
        current_promo=Decimal("1800"),
        is_pr=True,
    )
    assert decision.action == "skip"
