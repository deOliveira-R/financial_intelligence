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
    openfigi_api_key: str | None = None  # optional: raises OpenFIGI's limits
    http_timeout: float = 30.0
    # When set, every API endpoint except /health requires the header `X-API-Key: <value>`.
    api_key: str | None = None
    # Scheduled syncs (sync-daily / sync-weekly). Comma-separated lists.
    watchlist: str = ""  # tickers for Tiingo history and SEC fundamentals
    fred_series: str = ""  # empty: the curated macro pack (macro.py)
    market_otc: bool = True  # include OTC securities in Massive's daily bars

    @property
    def watchlist_tickers(self) -> list[str]:
        return _split(self.watchlist)

    @property
    def fred_series_ids(self) -> list[str]:
        from fin_intel.macro import MACRO_SERIES

        return _split(self.fred_series) or list(MACRO_SERIES)


def _split(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
