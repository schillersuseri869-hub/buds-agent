from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "buds"
    postgres_user: str = "buds"
    postgres_password: str = "buds"
    redis_url: str = "redis://redis:6379/0"
    owner_bot_token: str
    owner_telegram_id: int
    florist_bot_token: Optional[str] = None
    florist_telegram_id: Optional[int] = None
    market_api_token: str
    market_campaign_id: int
    market_client_id: str
    google_service_account_file: str = "/app/secrets/service_account.json"
    google_spreadsheet_id: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
