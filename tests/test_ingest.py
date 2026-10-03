from datetime import UTC, date, datetime

import httpx
import pytest
import respx
from conftest import COMPANY_FACTS, FRED_OBS, TICKERS, mock_fred, tiingo_bar
from sqlalchemy import func, select

from fin_intel import ingest
from fin_intel.db import upsert
from fin_intel.models import (
    Concept,
    CorporateAction,
    DailyBar,
    EconomicObservation,
    EconomicSeries,
    Fact,
    Filing,
    FiscalCalendar,
    RawResponse,
    SyncState,
)
from fin_intel.providers import (
    FredProvider,
    NotConfiguredError,
    ProviderError,
    QuotaExceededError,
    SecProvider,
    TiingoProvider,
)

SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
TIINGO = "https://api.tiingo.com/tiingo/daily"


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


@respx.mock
def test_sec_sync_normalizes_filings_concepts_and_facts(session, raw_store):
    tickers_route = respx.get(SEC_TICKERS).respond(json=TICKERS)
    respx.get(SEC_FACTS).respond(json=COMPANY_FACTS)
    sec = SecProvider(raw_store=raw_store)

    assert ingest.sync_tickers(session, sec) == 2
    assert tickers_route.calls[0].request.headers["User-Agent"] == "Test test@example.com"
    ingest.sync_fundamentals(session, sec, "AAPL")
    ingest.sync_fundamentals(session, sec, "AAPL")  # idempotent

    assert count(session, Filing) == 3
    assert count(session, Concept) == 3
    assert count(session, Fact) == 6
    assert session.scalar(select(Concept.label).where(Concept.name == "EarningsPerShareDiluted"))
    # Derived layer ran: calendar inferred, facts labelled, filing period ends found.
    (calendar,) = session.scalars(select(FiscalCalendar)).all()
    assert (calendar.year_end_month, calendar.year_end_day) == (9, 27)
    assert count(session, Fact) == session.scalar(
        select(func.count()).select_from(Fact).where(Fact.fiscal_year.is_not(None))
    )
    q10 = session.scalar(select(Filing).where(Filing.accession == "Q-2025"))
    assert q10.report_period_end == date(2025, 6, 28)


@respx.mock
def test_every_response_is_recorded_without_credentials(session, raw_store):
    mock_fred()
    ingest.sync_economic(session, FredProvider(raw_store=raw_store), "UNRATE")

    rows = session.scalars(select(RawResponse).order_by(RawResponse.id)).all()
    assert [(r.provider, r.dataset, r.key, r.status) for r in rows] == [
        ("fred", "series", "UNRATE", 200),
        ("fred", "observations", "UNRATE", 200),
        ("fred", "vintage_dates", "UNRATE", 200),
        ("fred", "vintages", "UNRATE", 200),
    ]
    assert all("fred-key" not in (r.params or "") for r in rows)
    (latest,) = [r for r in raw_store.records() if r.dataset == "observations"]
    assert latest.json() == FRED_OBS
    assert session.scalars(select(EconomicObservation.value).order_by("date")).all() == [4.2, None]
    assert session.get(EconomicSeries, "UNRATE").title == "Unemployment Rate"


def test_sec_requires_user_agent(monkeypatch):
    monkeypatch.delenv("FI_SEC_USER_AGENT")
    from fin_intel.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(NotConfiguredError):
        SecProvider().fetch_company_tickers()


@respx.mock
def test_prices_store_unadjusted_bars_and_actions_incrementally(session, raw_store):
    respx.get(f"{TIINGO}/MSFT").respond(
        json={"ticker": "msft", "name": "Microsoft", "exchangeCode": "NASDAQ"}
    )
    prices = respx.get(f"{TIINGO}/MSFT/prices")
    prices.side_effect = [
        httpx.Response(200, json=[tiingo_bar("2026-09-01", 10), tiingo_bar("2026-09-02", 11)]),
        httpx.Response(
            200, json=[tiingo_bar("2026-09-02", 11), tiingo_bar("2026-09-03", 6, split=2.0)]
        ),
    ]
    tiingo = TiingoProvider(raw_store=raw_store)
    ingest.sync_prices(session, tiingo, "MSFT")
    ingest.sync_prices(session, tiingo, "MSFT")

    # A split no longer forces a full refetch: stored bars are unadjusted.
    assert prices.call_count == 2
    assert prices.calls[1].request.url.params["startDate"] == "2026-09-02"
    assert prices.calls[0].request.headers["Authorization"] == "Token tiingo-key"
    assert session.scalars(select(DailyBar.close).order_by(DailyBar.date)).all() == [10, 11, 6]
    (split,) = session.scalars(select(CorporateAction)).all()
    assert (split.ex_date, split.action, split.value) == (date(2026, 9, 3), "split", 2.0)


@respx.mock
def test_sync_state_records_success_and_failure(session, raw_store):
    respx.get(SEC_TICKERS).respond(json=TICKERS)
    respx.get(SEC_FACTS).respond(500)
    sec = SecProvider(raw_store=raw_store)
    sec.max_retries = 0
    ingest.sync_tickers(session, sec)
    with pytest.raises(ProviderError):
        ingest.sync_fundamentals(session, sec, "AAPL")

    states = {s.dataset: s for s in session.scalars(select(SyncState))}
    assert states["company_tickers"].last_error is None and states["company_tickers"].rows == 2
    assert "HTTP 500" in states["companyfacts"].last_error
    assert states["companyfacts"].last_success is None
    # The failed response is in the raw layer too.
    assert session.scalar(select(RawResponse.status).where(RawResponse.dataset == "companyfacts"))


@respx.mock
def test_retries_on_429(session, monkeypatch):
    monkeypatch.setattr("fin_intel.providers.base.time.sleep", lambda _: None)
    route = respx.get("https://api.stlouisfed.org/fred/series/observations")
    route.side_effect = [httpx.Response(429), httpx.Response(200, json={"observations": []})]
    assert FredProvider().fetch_observations("GDP") == {"observations": []}
    assert route.call_count == 2


@respx.mock
def test_persistent_429_raises_quota_error(monkeypatch):
    monkeypatch.setattr("fin_intel.providers.base.time.sleep", lambda _: None)
    respx.get("https://api.stlouisfed.org/fred/series/observations").respond(429)
    with pytest.raises(QuotaExceededError):
        FredProvider().fetch_observations("GDP")


@respx.mock
def test_tiingo_plain_text_quota_message_raises_quota_error():
    respx.get(f"{TIINGO}/AAPL/prices").respond(
        200,
        text="You have run over your 500 symbol look up for this month. "
        "Please upgrade at https://api.tiingo.com/pricing to have your limits increased.",
    )
    with pytest.raises(QuotaExceededError):
        TiingoProvider().fetch_daily("AAPL")


def test_tiingo_monthly_symbol_cap_checked_before_calling(raw_store):
    for i in range(500):
        raw_store.save("tiingo", "daily_prices", f"T{i}", None, 200, b"[]")
    tiingo = TiingoProvider(raw_store=raw_store)
    tiingo.limiter.acquire = lambda: None  # the 500 recorded calls also fill the hourly window
    with pytest.raises(QuotaExceededError, match="distinct symbols"):
        tiingo.fetch_daily("NEW")


def test_rate_limits_hold_across_processes(raw_store):
    # 50 Tiingo calls recorded in the last hour (by an earlier process): the hourly limit
    # is used up, so the next call must not go out.
    for _ in range(50):
        raw_store.save("tiingo", "daily_prices", "AAPL", None, 200, b"[]")
    with pytest.raises(QuotaExceededError, match="rate limit"):
        TiingoProvider(raw_store=raw_store).limiter.acquire()


def test_raw_bodies_are_stored_once_per_content(raw_store, tmp_path):
    for _ in range(3):
        raw_store.save("sec", "companyfacts", "1", None, 200, b'{"same": true}')
    assert len(list((tmp_path / "raw").rglob("*.gz"))) == 1
    assert len(list(raw_store.records())) == 3


def test_upsert_collapses_duplicate_keys_in_batch(session):
    rows = [{"id": "X", "source": "a", "title": "old"}, {"id": "X", "source": "a", "title": "new"}]
    assert upsert(session, EconomicSeries, rows, key=["id"]) == 1
    assert session.get(EconomicSeries, "X").title == "new"


def test_raw_fetched_at_is_utc(raw_store):
    raw_store.save("sec", "x", None, None, 200, b"{}")
    (record,) = raw_store.records()
    assert record.fetched_at.tzinfo is not None
    assert abs((datetime.now(UTC) - record.fetched_at).total_seconds()) < 60


@respx.mock
def test_pagination_links_keep_their_query_alongside_auth_params():
    # FRED sends its key as a query parameter; a link with its own query must keep both.
    route = respx.get("https://api.stlouisfed.org/fred/series/observations").respond(json={})
    FredProvider().get(
        "https://api.stlouisfed.org/fred/series/observations?offset=1000", dataset="x"
    )
    params = route.calls[0].request.url.params
    assert (params["offset"], params["api_key"]) == ("1000", "fred-key")


@respx.mock
def test_fred_vintages_are_stored_and_fetched_incrementally(session, raw_store):
    from fin_intel.models import EconomicVintage

    mock_fred()
    fred = FredProvider(raw_store=raw_store)
    ingest.sync_economic(session, fred, "UNRATE")
    versions = session.execute(
        select(
            EconomicVintage.date, EconomicVintage.realtime_start, EconomicVintage.value
        ).order_by(EconomicVintage.date, EconomicVintage.realtime_start)
    ).all()
    assert versions == [
        (date(2026, 7, 1), date(2026, 8, 1), 4.1),
        (date(2026, 7, 1), date(2026, 9, 5), 4.2),
        (date(2026, 8, 1), date(2026, 9, 5), 4.3),
    ]
    # The next sync asks only for vintages after the last one stored.
    route = respx.get("https://api.stlouisfed.org/fred/series/vintagedates").respond(
        json={"count": 0, "vintage_dates": []}
    )
    ingest.sync_economic(session, fred, "UNRATE")
    assert route.calls[-1].request.url.params["realtime_start"] == "2026-09-06"
