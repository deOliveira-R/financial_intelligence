from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FI_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg:///fin_intel"  # local socket, OS user
    raw_dir: str = "data/raw"
    sec_user_agent: str | None = None
    fred_api_key: str | None = None
    tiingo_api_key: str | None = None
    massive_api_key: str | None = None
    openfigi_api_key: str | None = None  # optional: raises OpenFIGI's limits
    lda_api_key: str | None = None  # Senate LDA lobbying API (free); anonymous is very slow
    acled_email: str | None = None  # ACLED account (acleddata.com), for conflict events
    acled_password: str | None = None
    congress_api_key: str | None = None  # api.data.gov key for api.congress.gov (optional)
    eia_api_key: str = "DEMO_KEY"  # shared, rate-limited key; a free personal key lifts it
    edinet_api_key: str | None = None  # Japan's EDINET (free registration)
    opendart_api_key: str | None = None  # Korea's OpenDART (free registration)
    http_timeout: float = 30.0
    # When set, every API endpoint except /health requires the header `X-API-Key: <value>`,
    # or a tailnet user named by Tailscale Serve (the dashboard): `*` for any, a
    # comma-separated list of logins, or empty for none.
    api_key: str | None = None
    tailnet_users: str = "*"
    # Scheduled syncs (sync-daily / sync-weekly). Comma-separated lists.
    watchlist: str = ""  # tickers for Tiingo history and SEC fundamentals
    fred_series: str = ""  # empty: the curated macro pack (macro.py)
    market_otc: bool = True  # include OTC securities in Massive's daily bars

    @property
    def watchlist_tickers(self) -> list[str]:
        return _split(self.watchlist)

    @property
    def fred_series_ids(self) -> list[str]:
        """The configured list (default: the macro pack), plus the exchange rates that
        foreign filers' metrics need."""
        from fin_intel import fx
        from fin_intel.macro import MACRO_SERIES

        ids = _split(self.fred_series) or list(MACRO_SERIES)
        return ids + [s for s, _ in fx.SERIES.values() if s not in ids]


def _split(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """The environment (and .env), with the settings saved from the dashboard over it."""
    settings = Settings()
    saved = _saved(settings.database_url)
    return settings.model_copy(update=saved) if saved else settings


def _saved(database_url: str) -> dict[str, str]:
    """Settings saved from the dashboard (settings_store.py), or none when the database
    isn't reachable or not migrated yet: commands that don't need it still run."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    from fin_intel.settings_store import EDITABLE

    try:
        engine = create_engine(
            database_url, poolclass=NullPool, connect_args={"connect_timeout": 3}
        )
        try:
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT name, value FROM app_settings")).all()
        finally:
            engine.dispose()
    except Exception:
        return {}
    return {name: value for name, value in rows if name in EDITABLE}
