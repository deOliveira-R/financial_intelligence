import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from fin_intel.config import Settings, get_settings
from fin_intel.db import init_db, session_factory
from fin_intel.providers import base
from fin_intel.raw import RawStore


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    # Tests must never pick up real credentials from the developer's .env.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("FI_SEC_USER_AGENT", "Test test@example.com")
    monkeypatch.setenv("FI_FRED_API_KEY", "fred-key")
    monkeypatch.setenv("FI_TIINGO_API_KEY", "tiingo-key")
    get_settings.cache_clear()
    base._limiters.clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    return engine


@pytest.fixture
def session(engine):
    with session_factory(engine)() as session:
        yield session


@pytest.fixture
def raw_store(engine, tmp_path):
    return RawStore(engine, tmp_path / "raw")


# --- sample payloads -------------------------------------------------------------------

TICKERS = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], [1067983, "Berkshire", "BRK-B", "NYSE"]],
}


def fact(start, end, val, accn, fy, fp, form, filed):
    out = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start:
        out["start"] = start
    return out


COMPANY_FACTS = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "description": "Amount of revenue recognized.",
                "units": {
                    "USD": [
                        # FY2024 as first reported, then restated in the FY2025 10-K, which
                        # also carries the FY2025 value and 9M year-to-date from the 10-Q.
                        fact(
                            "2023-10-01",
                            "2024-09-28",
                            100.0,
                            "A-2024",
                            2024,
                            "FY",
                            "10-K",
                            "2024-11-01",
                        ),
                        fact(
                            "2023-10-01",
                            "2024-09-28",
                            101.0,
                            "A-2025",
                            2025,
                            "FY",
                            "10-K",
                            "2025-10-31",
                        ),
                        fact(
                            "2024-09-29",
                            "2025-09-27",
                            120.0,
                            "A-2025",
                            2025,
                            "FY",
                            "10-K",
                            "2025-10-31",
                        ),
                        fact(
                            "2024-09-29",
                            "2025-06-28",
                            90.0,
                            "Q-2025",
                            2025,
                            "Q3",
                            "10-Q",
                            "2025-08-01",
                        ),
                    ]
                },
            },
            "EarningsPerShareDiluted": {
                "label": "EPS diluted",
                "units": {
                    "USD/shares": [
                        fact(
                            "2023-10-01",
                            "2024-09-28",
                            8.0,
                            "A-2024",
                            2024,
                            "FY",
                            "10-K",
                            "2024-11-01",
                        ),
                    ]
                },
            },
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        fact(None, "2024-09-28", 365.0, "A-2024", 2024, "FY", "10-K", "2024-11-01")
                    ]
                },
            },
        }
    },
}


def tiingo_bar(day, close, div=0.0, split=1.0):
    return {
        "date": f"{day}T00:00:00.000Z",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "adjOpen": close,
        "adjHigh": close,
        "adjLow": close,
        "adjClose": close,
        "adjVolume": 1000,
        "divCash": div,
        "splitFactor": split,
    }


FRED_SERIES = {
    "seriess": [
        {
            "id": "UNRATE",
            "title": "Unemployment Rate",
            "units_short": "%",
            "frequency_short": "M",
            "seasonal_adjustment_short": "SA",
            "last_updated": "2026-09-05 07:44:02-05",
        }
    ]
}
FRED_OBS = {
    "observations": [{"date": "2026-07-01", "value": "4.2"}, {"date": "2026-08-01", "value": "."}]
}
