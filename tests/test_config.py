import os
import pytest
from unittest.mock import patch


def test_settings_loads_from_env():
    env = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "buds_test",
        "POSTGRES_USER": "buds",
        "POSTGRES_PASSWORD": "secret",
        "REDIS_URL": "redis://localhost:6379/0",
        "OWNER_BOT_TOKEN": "123:abc",
        "OWNER_TELEGRAM_ID": "111222333",
        "FLORIST_BOT_TOKEN": "456:def",
        "FLORIST_TELEGRAM_ID": "444555666",
        "MARKET_API_TOKEN": "mtoken",
        "MARKET_CAMPAIGN_ID": "12345",
        "MARKET_CLIENT_ID": "67890",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "/tmp/sa.json",
        "GOOGLE_SPREADSHEET_ID": "sheet123",
    }
    with patch.dict(os.environ, env, clear=True):
        from app.config import Settings
        s = Settings()
        assert s.postgres_db == "buds_test"
        assert s.redis_url == "redis://localhost:6379/0"
        assert s.owner_telegram_id == 111222333
        assert s.market_campaign_id == 12345


def test_settings_has_market_warehouse_id():
    from app.config import Settings
    s = Settings(
        owner_bot_token="x",
        owner_telegram_id=1,
        market_api_token="t",
        market_campaign_id=1,
        market_client_id="c",
        market_warehouse_id=999,
    )
    assert s.market_warehouse_id == 999


def test_database_url_format():
    env = {
        "POSTGRES_HOST": "myhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "mydb",
        "POSTGRES_USER": "myuser",
        "POSTGRES_PASSWORD": "mypass",
        "REDIS_URL": "redis://localhost:6379/0",
        "OWNER_BOT_TOKEN": "123:abc",
        "OWNER_TELEGRAM_ID": "1",
        "FLORIST_BOT_TOKEN": "456:def",
        "FLORIST_TELEGRAM_ID": "2",
        "MARKET_API_TOKEN": "t",
        "MARKET_CAMPAIGN_ID": "1",
        "MARKET_CLIENT_ID": "c",
    }
    with patch.dict(os.environ, env, clear=True):
        from app.config import Settings
        s = Settings()
        assert s.database_url == "postgresql+asyncpg://myuser:mypass@myhost:5432/mydb"
