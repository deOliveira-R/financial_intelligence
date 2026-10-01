from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FI_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/fin_intel.db"
    raw_dir: str = "data/raw"
    sec_user_agent: str | None = None
    fred_api_key: str | None = None
    tiingo_api_key: str | None = None
    massive_api_key: str | None = None
    http_timeout: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
