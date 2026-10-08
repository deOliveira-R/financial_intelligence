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
    monkeypatch.setenv("FI_MASSIVE_API_KEY", "massive-key")
    monkeypatch.setenv("FI_OPENDART_API_KEY", "dart-key")
    monkeypatch.setenv("FI_EDINET_API_KEY", "edinet-key")
    get_settings.cache_clear()
    base._limiters.clear()
    # Mocked providers needn't wait out real rate limits (e.g. Massive's 5 calls/minute).
    from fin_intel.providers import (
        BojProvider,
        CboeProvider,
        DartProvider,
        EiaProvider,
        FederalRegisterProvider,
        FedProvider,
        FinraProvider,
        GovinfoProvider,
        HouseProvider,
        LdaProvider,
        LegislatorsProvider,
        MassiveProvider,
        MofProvider,
        SenateProvider,
        UsaspendingProvider,
    )
    from fin_intel.providers.ratelimit import MINUTE, Limit

    for provider in (
        MassiveProvider,
        HouseProvider,
        SenateProvider,
        EiaProvider,
        FedProvider,
        DartProvider,
        BojProvider,
        MofProvider,
        UsaspendingProvider,
        FinraProvider,
        CboeProvider,
        LegislatorsProvider,
        LdaProvider,
        GovinfoProvider,
        FederalRegisterProvider,
    ):
        monkeypatch.setattr(provider, "limits", (Limit(10_000, MINUTE),))
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


MASSIVE_GROUPED = {
    "status": "OK",
    "resultsCount": 3,
    "results": [
        {"T": "AAPL", "o": 254.0, "h": 256.0, "l": 253.0, "c": 255.5, "v": 41e6, "t": 0},
        {"T": "BRK.B", "o": 480.0, "h": 482.0, "l": 478.0, "c": 481.0, "v": 3e6, "t": 0},
        {"T": "ZZZZW", "o": 0.1, "h": 0.1, "l": 0.1, "c": 0.1, "v": 100, "t": 0},
    ],
}
MASSIVE_SPLITS_PAGE1 = {
    "status": "OK",
    "results": [
        {
            "ticker": "AAPL",
            "execution_date": "2020-08-31",
            "split_from": 1,
            "split_to": 4,
            "adjustment_type": "forward_split",
        },
    ],
    "next_url": "https://api.massive.com/stocks/v1/splits?cursor=abc",
}
MASSIVE_SPLITS_PAGE2 = {
    "status": "OK",
    "results": [
        {
            "ticker": "BRK.B",
            "execution_date": "2010-01-21",
            "split_from": 1,
            "split_to": 50,
            "adjustment_type": "forward_split",
        },
        {
            "ticker": "ZZZZ",
            "execution_date": "2025-01-02",
            "split_from": 10,
            "split_to": 1,
            "adjustment_type": "reverse_split",
        },
    ],
}
MASSIVE_DIVIDENDS = {
    "status": "OK",
    "results": [
        {
            "ticker": "AAPL",
            "ex_dividend_date": "2026-08-11",
            "cash_amount": 0.26,
            "currency": "USD",
            "distribution_type": "recurring",
        },
    ],
}

# UNRATE's July value was first published as 4.1 on Aug 1, revised to 4.2 on Sep 5, when
# August was first published.
FRED_VINTAGE_DATES = {"count": 2, "vintage_dates": ["2026-08-01", "2026-09-05"]}
FRED_VINTAGES = {
    "count": 3,
    "observations": [
        {
            "date": "2026-07-01",
            "realtime_start": "2026-08-01",
            "realtime_end": "2026-09-04",
            "value": "4.1",
        },
        {
            "date": "2026-07-01",
            "realtime_start": "2026-09-05",
            "realtime_end": "9999-12-31",
            "value": "4.2",
        },
        {
            "date": "2026-08-01",
            "realtime_start": "2026-09-05",
            "realtime_end": "9999-12-31",
            "value": "4.3",
        },
    ],
}


def mock_fred(respx_router=None):
    """Register FRED routes for UNRATE: series, latest observations, and vintages."""
    import respx

    router = respx_router or respx
    base = "https://api.stlouisfed.org/fred"
    router.get(f"{base}/series").respond(json=FRED_SERIES)
    router.get(f"{base}/series/vintagedates").respond(json=FRED_VINTAGE_DATES)
    # Vintage requests carry a realtime window; the plain one is the latest values.
    router.get(f"{base}/series/observations", params={"realtime_start": "2026-08-01"}).respond(
        json=FRED_VINTAGES
    )
    router.get(f"{base}/series/observations").respond(json=FRED_OBS)
