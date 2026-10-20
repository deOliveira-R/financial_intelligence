"""Database schema.

Three layers:
- raw: every provider response, stored as a file and indexed in `raw_responses`. Immutable.
- normalized: tables loaded from raw responses. Reported values are never edited after load,
  so any of them can be rebuilt from raw without network access (`fin-intel rebuild`).
- derived: our own inferences (fiscal calendars, fact labels), recomputed by `derive.py`.
  Columns holding derived values are marked as such.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, deferred, mapped_column

from fin_intel.db import Base

# Full-text search vector of a text column (PostgreSQL caps a tsvector near 1 MB, so very
# long documents are indexed on their first SEARCH_CHARS characters).
SEARCH_CHARS = 500_000


def _search_vector(column: str):
    return deferred(
        mapped_column(
            TSVECTOR,
            Computed(f"to_tsvector('english', left({column}, {SEARCH_CHARS}))", persisted=True),
        )
    )


# --- raw layer and bookkeeping ---------------------------------------------------------


class RawResponse(Base):
    """One HTTP call to a provider. The body lives in a content-addressed file under raw_dir."""

    __tablename__ = "raw_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String)
    dataset: Mapped[str] = mapped_column(String)  # e.g. companyfacts, daily_prices
    key: Mapped[str | None] = mapped_column(String)  # e.g. CIK, ticker, series id
    params: Mapped[str | None] = mapped_column(Text)  # JSON, without credentials
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String)
    size: Mapped[int]

    __table_args__ = (Index("ix_raw_lookup", "provider", "dataset", "key", "fetched_at"),)


class SyncRun(Base):
    """One CLI sync command."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String)  # running, ok, partial, failed
    items_ok: Mapped[int] = mapped_column(default=0)
    items_failed: Mapped[int] = mapped_column(default=0)
    message: Mapped[str | None] = mapped_column(Text)


class JobState(Base):
    """A scheduled step's state, kept until it succeeds (jobs.py): ok, due, running,
    retrying, failed (still retried, less often) or blocked (needs a setting)."""

    __tablename__ = "job_state"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String)
    attempts: Mapped[int] = mapped_column(default=0)  # consecutive failures
    due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_kind: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text)
    hint: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    """A setting saved from the dashboard (settings_store.py), applied over the environment."""

    __tablename__ = "app_settings"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobEvent(Base):
    """One attempt of a scheduled step and how it ended, with the end of its output: the
    history a person, or a model diagnosing a failure, reads."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String)  # the state it left the step in
    attempt: Mapped[int]
    error_kind: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)


class SyncState(Base):
    """Latest sync outcome per item, e.g. (tiingo, daily_prices, AAPL)."""

    __tablename__ = "sync_state"

    provider: Mapped[str] = mapped_column(String, primary_key=True)
    dataset: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    last_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    rows: Mapped[int | None]


# --- entities --------------------------------------------------------------------------


class Issuer(Base):
    """A company or fund registered with the SEC, identified by CIK."""

    __tablename__ = "issuers"

    cik: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String)
    # From SEC submissions: Standard Industrial Classification and filer status.
    sic: Mapped[int | None] = mapped_column(index=True)
    sic_description: Mapped[str | None] = mapped_column(String)
    filer_category: Mapped[str | None] = mapped_column(String)
    # Where the issuer comes from. SEC filers use their CIK; issuers known from other
    # regulators (EDINET, DART, TWSE, ESEF) get an id in a reserved range (world.py).
    source: Mapped[str] = mapped_column(String, server_default="sec")
    source_id: Mapped[str | None] = mapped_column(String, index=True)
    country: Mapped[str | None] = mapped_column(String)
    lei: Mapped[str | None] = mapped_column(String, index=True)
    home_ticker: Mapped[str | None] = mapped_column(String)  # e.g. 005930 (Samsung)
    fiscal_month: Mapped[int | None]  # month the fiscal year ends, when known
    # The home listing's share class (OpenFIGI): the same on every exchange, so it finds
    # the company's shares traded elsewhere (e.g. OTC in the US). See crosslist.py.
    share_class_figi: Mapped[str | None] = mapped_column(String, index=True)
    figi_name: Mapped[str | None] = mapped_column(String)  # OpenFIGI's name for it
    isin: Mapped[str | None] = mapped_column(String)
    # The SEC filer this company is, when it files with both (TSMC, Toyota, ASML): metrics
    # and screens use the SEC filer only, so the company appears once.
    same_as: Mapped[int | None] = mapped_column(index=True)


class Security(Base):
    """A tradable instrument. `id` is our internal identifier, stable across ticker changes.

    `ticker` is the symbol it currently trades under. It is kept after delisting (so history
    stays reachable) and set to NULL only when another security takes the symbol over.
    `origin` is the source whose list created it (sec, massive, tiingo); `active` is False once
    it drops out of that source's current list. Each source only deactivates its own.
    """

    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String, unique=True)
    active: Mapped[bool] = mapped_column(default=True, server_default=true())
    origin: Mapped[str] = mapped_column(String, server_default="sec")
    name: Mapped[str | None] = mapped_column(String)
    exchange: Mapped[str | None] = mapped_column(String)  # as named by SEC, e.g. Nasdaq
    mic: Mapped[str | None] = mapped_column(String)  # ISO 10383 primary exchange
    # Massive's type code: CS, ETF, PFD, WARRANT, UNIT, RIGHT, ADRC, ETN, FUND, ...
    security_type: Mapped[str | None] = mapped_column(String, index=True)
    cik: Mapped[int | None] = mapped_column(ForeignKey("issuers.cik"), index=True)
    figi: Mapped[str | None] = mapped_column(String, index=True)  # composite FIGI
    share_class_figi: Mapped[str | None] = mapped_column(String)
    # Last trading listing per Massive. Delisted securities keep `ticker` NULL (the symbol
    # may be reused); their old symbol lives in ticker_history with last_seen = delisted_on.
    delisted_on: Mapped[date | None] = mapped_column(Date)
    # Shares of this listing (for an ADR, depositary shares) per Massive's ticker details:
    # financials count ordinary shares, which an ADR may bundle (or split).
    shares_outstanding: Mapped[float | None] = mapped_column(Float)
    shares_as_of: Mapped[date | None] = mapped_column(Date)
    # Trading currency of the listing's prices (None: US dollars).
    currency: Mapped[str | None] = mapped_column(String)
    figi_name: Mapped[str | None] = mapped_column(String)  # OpenFIGI's name (crosslist.py)


class TickerHistory(Base):
    """Symbols a security has used, with the span over which our syncs observed each."""

    __tablename__ = "ticker_history"

    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    ticker: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    first_seen: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date] = mapped_column(Date)


# --- prices ----------------------------------------------------------------------------


class DailyBar(Base):
    """Unadjusted end-of-day OHLCV, per source. Adjusted prices are computed on read from
    corporate actions, so new splits and dividends never rewrite stored history."""

    __tablename__ = "daily_bars"

    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)


class CorporateAction(Base):
    """A split (value = new shares per old share) or cash dividend (value = amount per share),
    effective on its ex-date."""

    __tablename__ = "corporate_actions"

    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    ex_date: Mapped[date] = mapped_column(Date, primary_key=True)
    action: Mapped[str] = mapped_column(String, primary_key=True)  # split, dividend
    source: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[float] = mapped_column(Float)


# --- fundamentals ----------------------------------------------------------------------


class Filing(Base):
    """An SEC filing that reported XBRL facts. fy/fp are SEC's labels for the filing."""

    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    accession: Mapped[str] = mapped_column(String, unique=True)
    cik: Mapped[int] = mapped_column(ForeignKey("issuers.cik"), index=True)
    form: Mapped[str | None] = mapped_column(String)
    filed: Mapped[date | None] = mapped_column(Date)
    fiscal_year: Mapped[int | None]
    fiscal_period: Mapped[str | None] = mapped_column(String)
    # Derived: the filing's primary period end (latest duration end on or before filing).
    report_period_end: Mapped[date | None] = mapped_column(Date)


class Concept(Base):
    """An XBRL concept, e.g. us-gaap:Revenues."""

    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    taxonomy: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    label: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("taxonomy", "name"),)


class Fact(Base):
    """One XBRL value as reported in one filing. Instants store period_start == period_end.

    The same value reappears in later filings as comparatives; keeping each filing's copy
    preserves point-in-time history.
    """

    __tablename__ = "facts"

    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"), primary_key=True)
    unit: Mapped[str] = mapped_column(String, primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    instant: Mapped[bool]
    value: Mapped[float]
    frame: Mapped[str | None] = mapped_column(String)
    # Denormalized from filings for the per-company queries every read path makes.
    cik: Mapped[int] = mapped_column(ForeignKey("issuers.cik"))
    # Derived (see derive.py): the fact's own period, from its dates and the fiscal calendar.
    period_type: Mapped[str | None] = mapped_column(String)
    fiscal_year: Mapped[int | None]
    fiscal_period: Mapped[str | None] = mapped_column(String)

    __table_args__ = (Index("ix_facts_cik_concept", "cik", "concept_id"),)


class FiscalCalendar(Base):
    """Derived: an issuer's fiscal year end over time, one row per regime (see periods.py)."""

    __tablename__ = "fiscal_calendars"

    cik: Mapped[int] = mapped_column(ForeignKey("issuers.cik"), primary_key=True)
    segment: Mapped[int] = mapped_column(primary_key=True)  # 0 = oldest regime
    year_end_month: Mapped[int]
    year_end_day: Mapped[int]
    year_offset: Mapped[int]  # fiscal year label minus the calendar year it ends in
    first_year_end: Mapped[date] = mapped_column(Date)
    last_year_end: Mapped[date] = mapped_column(Date)


# --- economic data ---------------------------------------------------------------------


class EconomicSeries(Base):
    __tablename__ = "economic_series"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
    units: Mapped[str | None] = mapped_column(String)
    frequency: Mapped[str | None] = mapped_column(String)
    seasonal_adjustment: Mapped[str | None] = mapped_column(String)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_id: Mapped[int | None] = mapped_column(ForeignKey("economic_releases.id"))


class EconomicRelease(Base):
    """A FRED release (e.g. Consumer Price Index): the publication a series comes out in."""

    __tablename__ = "economic_releases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String)
    link: Mapped[str | None] = mapped_column(String)


class EconomicReleaseDate(Base):
    """A release's publication date, past or scheduled."""

    __tablename__ = "economic_release_dates"

    release_id: Mapped[int] = mapped_column(ForeignKey("economic_releases.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)


class EconomicObservation(Base):
    __tablename__ = "economic_observations"

    series_id: Mapped[str] = mapped_column(ForeignKey("economic_series.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float | None] = mapped_column(Float)


# --- portfolio -------------------------------------------------------------------------


class Account(Base):
    """A brokerage or retirement account. Only taxable accounts are harvesting candidates,
    but purchases in every account count for the wash-sale rule."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)  # e.g. "Fidelity Individual"
    broker: Mapped[str] = mapped_column(String)  # fidelity, vanguard, manual, ...
    account_type: Mapped[str] = mapped_column(String)  # taxable, ira, roth_ira, 401k, hsa
    taxable: Mapped[bool]
    number_last4: Mapped[str | None] = mapped_column(String)


class PortfolioTransaction(Base):
    """One account event as the broker reported it. Lots are derived from these (portfolio.py).

    `amount` is the cash effect on the account (negative for purchases); `source_ref` is a
    hash of the source row so re-importing the same export is idempotent.
    """

    __tablename__ = "portfolio_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    # buy, sell, reinvest, dividend, interest, split, transfer_in, transfer_out, fee, other
    action: Mapped[str] = mapped_column(String)
    symbol: Mapped[str | None] = mapped_column(String, index=True)
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id"))
    quantity: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String)  # generic, fidelity, vanguard, manual
    source_ref: Mapped[str] = mapped_column(String, unique=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionSnapshot(Base):
    """Holdings as a broker reported them on a date: used to reconcile derived lots and to
    price holdings with no market data (e.g. 401(k) collective trusts without tickers)."""

    __tablename__ = "position_snapshots"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    market_value: Mapped[float | None] = mapped_column(Float)
    cost_basis: Mapped[float | None] = mapped_column(Float)


class EconomicVintage(Base):
    """A value of an observation as published from `realtime_start` on (ALFRED vintages).

    What was known about `date` on day D is the row with the latest realtime_start <= D.
    A version spanning two fetch windows appears once per window with the same value,
    which this lookup makes harmless.
    """

    __tablename__ = "economic_vintages"

    series_id: Mapped[str] = mapped_column(ForeignKey("economic_series.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    realtime_start: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float | None] = mapped_column(Float)


class MarketBreadth(Base):
    """Derived: daily market internals for a universe of stocks (see breadth.py)."""

    __tablename__ = "market_breadth"

    universe: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int]  # stocks with a bar today and the previous trading day
    advancers: Mapped[int]
    decliners: Mapped[int]
    unchanged: Mapped[int]
    ad_line: Mapped[int]  # cumulative advancers minus decliners since the data begins
    up_volume: Mapped[float]
    down_volume: Mapped[float]
    new_highs: Mapped[int]  # close at its 252-day high
    new_lows: Mapped[int]
    eligible_252: Mapped[int]  # stocks with 252 days of history (denominator for highs/lows)
    above_50d: Mapped[int]
    eligible_50d: Mapped[int]
    above_200d: Mapped[int]
    eligible_200d: Mapped[int]


class StatementItem(Base):
    """Derived: a standard financial statement line for an issuer and period (statements.py).
    `concept_id` is the XBRL concept the value came from."""

    __tablename__ = "statement_items"

    cik: Mapped[int] = mapped_column(ForeignKey("issuers.cik"), primary_key=True)
    line_item: Mapped[str] = mapped_column(String, primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    period_type: Mapped[str] = mapped_column(String)
    fiscal_year: Mapped[int | None]
    fiscal_period: Mapped[str | None] = mapped_column(String)
    unit: Mapped[str] = mapped_column(String)
    value: Mapped[float]
    filed: Mapped[date | None] = mapped_column(Date)
    # When the figure was first public (later filings repeat it as a comparative).
    first_filed: Mapped[date | None] = mapped_column(Date)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.id"))
    derived: Mapped[bool] = mapped_column(default=False, server_default=false())


class CompanyMetrics(Base):
    """Derived: screening metrics for an issuer's primary security, as computed on `as_of`
    from what was known that day (metrics.py). One row per security per day."""

    __tablename__ = "company_metrics"

    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    cik: Mapped[int] = mapped_column(ForeignKey("issuers.cik"))
    price: Mapped[float] = mapped_column(Float)
    period_end: Mapped[date] = mapped_column(Date)  # latest financials used
    # The financials' reporting currency; amounts are converted to US dollars at the day's
    # rate (valuation needs a rate: without one, market-cap metrics are left empty).
    currency: Mapped[str | None] = mapped_column(String)
    market_cap: Mapped[float | None] = mapped_column(Float)
    enterprise_value: Mapped[float | None] = mapped_column(Float)
    revenue_ttm: Mapped[float | None] = mapped_column(Float)
    net_income_ttm: Mapped[float | None] = mapped_column(Float)
    ebit_ttm: Mapped[float | None] = mapped_column(Float)
    fcf_ttm: Mapped[float | None] = mapped_column(Float)
    pe: Mapped[float | None] = mapped_column(Float)
    ev_ebit: Mapped[float | None] = mapped_column(Float)
    ev_ebitda: Mapped[float | None] = mapped_column(Float)
    ev_sales: Mapped[float | None] = mapped_column(Float)
    p_fcf: Mapped[float | None] = mapped_column(Float)
    p_b: Mapped[float | None] = mapped_column(Float)
    earnings_yield: Mapped[float | None] = mapped_column(Float)
    fcf_yield: Mapped[float | None] = mapped_column(Float)
    dividend_yield: Mapped[float | None] = mapped_column(Float)
    shareholder_yield: Mapped[float | None] = mapped_column(Float)
    gross_margin: Mapped[float | None] = mapped_column(Float)
    operating_margin: Mapped[float | None] = mapped_column(Float)
    net_margin: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)
    roic: Mapped[float | None] = mapped_column(Float)
    debt_to_equity: Mapped[float | None] = mapped_column(Float)
    net_debt_to_ebitda: Mapped[float | None] = mapped_column(Float)
    current_ratio: Mapped[float | None] = mapped_column(Float)
    interest_coverage: Mapped[float | None] = mapped_column(Float)
    revenue_growth: Mapped[float | None] = mapped_column(Float)
    earnings_growth: Mapped[float | None] = mapped_column(Float)
    ebit_growth: Mapped[float | None] = mapped_column(Float)
    operating_margin_5y: Mapped[float | None] = mapped_column(Float)
    operating_margin_vs_5y: Mapped[float | None] = mapped_column(Float)
    altman_z: Mapped[float | None] = mapped_column(Float)
    piotroski_f: Mapped[int | None]


class InsiderTransaction(Base):
    """A non-derivative transaction reported on Form 3, 4 or 5 (insiders.py). `key` is a
    content hash within the filing, identical whichever source the row came from."""

    __tablename__ = "insider_transactions"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    accession: Mapped[str] = mapped_column(String, index=True)
    form_type: Mapped[str | None] = mapped_column(String)
    filing_date: Mapped[date | None] = mapped_column(Date, index=True)
    issuer_cik: Mapped[int] = mapped_column(index=True)
    issuer_symbol: Mapped[str | None] = mapped_column(String)
    owner_cik: Mapped[int | None]
    owner_name: Mapped[str | None] = mapped_column(String)
    relationship: Mapped[str | None] = mapped_column(String)
    owner_title: Mapped[str | None] = mapped_column(String)
    security_title: Mapped[str | None] = mapped_column(String)
    trans_date: Mapped[date] = mapped_column(Date, index=True)
    trans_code: Mapped[str | None] = mapped_column(String)
    acquired_disposed: Mapped[str | None] = mapped_column(String)
    shares: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    shares_after: Mapped[float | None] = mapped_column(Float)
    direct_indirect: Mapped[str | None] = mapped_column(String)
    plan_10b5_1: Mapped[bool] = mapped_column(default=False)


class InstitutionalFiler(Base):
    """A 13F filer: an institutional investment manager with $100M+ under management."""

    __tablename__ = "institutional_filers"

    cik: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String, index=True)


class InstitutionalPosition(Base):
    """A filer's holding of one security at a quarter end (13F), summed across the rows
    it reports it in (e.g. per subsidiary). Options are separate positions (put_call)."""

    __tablename__ = "institutional_positions"

    filer_cik: Mapped[int] = mapped_column(ForeignKey("institutional_filers.cik"), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    cusip: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    put_call: Mapped[str] = mapped_column(String, primary_key=True)  # "" for shares
    issuer_name: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
    shares: Mapped[float | None] = mapped_column(Float)  # or principal amount, see share_type
    share_type: Mapped[str | None] = mapped_column(String)  # SH or PRN
    value: Mapped[float | None] = mapped_column(Float)  # US dollars
    accession: Mapped[str] = mapped_column(String)
    filed: Mapped[date | None] = mapped_column(Date)


class CusipMapping(Base):
    """Derived: which security a CUSIP is (via a FIGI in the filing or OpenFIGI)."""

    __tablename__ = "cusip_mappings"

    cusip: Mapped[str] = mapped_column(String, primary_key=True)
    figi: Mapped[str | None] = mapped_column(String)
    composite_figi: Mapped[str | None] = mapped_column(String)
    ticker: Mapped[str | None] = mapped_column(String)
    security_type: Mapped[str | None] = mapped_column(String)
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id"))


class CongressReport(Base):
    """A member of Congress's Periodic Transaction Report (congress.py). `transactions` is
    None until the report itself is parsed (paper filings never are)."""

    __tablename__ = "congress_reports"

    doc_id: Mapped[str] = mapped_column(String, primary_key=True)
    chamber: Mapped[str] = mapped_column(String)  # house or senate
    name: Mapped[str | None] = mapped_column(String, index=True)
    state: Mapped[str | None] = mapped_column(String)  # House state and district
    filed: Mapped[date | None] = mapped_column(Date, index=True)
    year: Mapped[int | None]
    electronic: Mapped[bool] = mapped_column(default=True)
    transactions: Mapped[int | None]


class LegislatorTerm(Base):
    """One term of a member of Congress (unitedstates/congress-legislators)."""

    __tablename__ = "legislator_terms"

    bioguide: Mapped[str] = mapped_column(String, primary_key=True)
    start: Mapped[date] = mapped_column(Date, primary_key=True)
    end: Mapped[date] = mapped_column(Date)
    chamber: Mapped[str] = mapped_column(String)  # house or senate
    state: Mapped[str] = mapped_column(String)
    district: Mapped[int | None]  # House; 0 for at-large
    party: Mapped[str | None] = mapped_column(String)
    first: Mapped[str | None] = mapped_column(String)
    last: Mapped[str | None] = mapped_column(String)
    nickname: Mapped[str | None] = mapped_column(String)
    full_name: Mapped[str | None] = mapped_column(String)


class Committee(Base):
    """A current congressional committee or subcommittee."""

    __tablename__ = "committees"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # thomas id, e.g. SSAS
    name: Mapped[str] = mapped_column(String)
    chamber: Mapped[str | None] = mapped_column(String)
    parent: Mapped[str | None] = mapped_column(String)
    jurisdiction: Mapped[str | None] = mapped_column(Text)


class CommitteeMembership(Base):
    """Current committee assignments (a snapshot: historical rosters aren't in the source)."""

    __tablename__ = "committee_memberships"

    committee: Mapped[str] = mapped_column(String, primary_key=True)
    bioguide: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    title: Mapped[str | None] = mapped_column(String)
    rank: Mapped[int | None]
    side: Mapped[str | None] = mapped_column(String)  # majority or minority


class CongressReportMember(Base):
    """Derived: the member of Congress who filed a report (congress_members.py), matched
    by House district and date or, for senators, by name among those serving then."""

    __tablename__ = "congress_report_members"

    doc_id: Mapped[str] = mapped_column(String, primary_key=True)
    bioguide: Mapped[str] = mapped_column(String, index=True)
    party: Mapped[str | None] = mapped_column(String)
    state: Mapped[str | None] = mapped_column(String)
    method: Mapped[str] = mapped_column(String)  # district or name


class LobbyingReport(Base):
    """A quarterly lobbying report (LD-2) from the Senate LDA database (lobbying.py).
    `amount` is the registrant's income from the client (lobbying firms) or the client's
    own expenses (in-house lobbying). Amendments are separate rows; the latest posted for a
    registrant, client and quarter counts."""

    __tablename__ = "lobbying_reports"

    filing_uuid: Mapped[str] = mapped_column(String, primary_key=True)
    year: Mapped[int] = mapped_column(index=True)
    quarter: Mapped[int]
    filing_type: Mapped[str] = mapped_column(String)
    registrant_id: Mapped[int | None]
    registrant: Mapped[str | None] = mapped_column(String)
    client_id: Mapped[int | None] = mapped_column(index=True)
    client: Mapped[str | None] = mapped_column(String, index=True)
    client_description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float | None] = mapped_column(Float)
    posted: Mapped[date | None] = mapped_column(Date)


class LobbyingIssue(Base):
    """An issue area a lobbying report covers (general issue code: DEF, ENG, HCR, TAX...)."""

    __tablename__ = "lobbying_issues"

    filing_uuid: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    entities: Mapped[str | None] = mapped_column(Text)  # government bodies lobbied


class Bill(Base):
    """A bill or joint resolution (GovInfo Bill Status, bills.py)."""

    __tablename__ = "bills"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # 118-hr-1234
    congress: Mapped[int] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String)
    number: Mapped[int]
    introduced: Mapped[date | None] = mapped_column(Date, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    policy_area: Mapped[str | None] = mapped_column(String, index=True)
    sponsor: Mapped[str | None] = mapped_column(String, index=True)  # bioguide id
    sponsor_party: Mapped[str | None] = mapped_column(String)
    cosponsors: Mapped[int | None]
    committees: Mapped[str | None] = mapped_column(Text)  # system codes, comma-separated
    latest_action: Mapped[date | None] = mapped_column(Date)
    latest_action_text: Mapped[str | None] = mapped_column(Text)
    law: Mapped[str | None] = mapped_column(String)  # public law number (118-31), if enacted
    enacted: Mapped[date | None] = mapped_column(Date)


class BillSubject(Base):
    """A legislative subject CRS assigned to a bill (e.g. "Military procurement")."""

    __tablename__ = "bill_subjects"

    bill_id: Mapped[str] = mapped_column(String, primary_key=True)
    subject: Mapped[str] = mapped_column(String, primary_key=True, index=True)


class PresidentialDocument(Base):
    """An executive order, proclamation, memorandum, determination or notice published in
    the Federal Register (presidential.py)."""

    __tablename__ = "presidential_documents"

    document_number: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    number: Mapped[str | None] = mapped_column(String)  # EO / proclamation number
    title: Mapped[str | None] = mapped_column(Text)
    president: Mapped[str | None] = mapped_column(String)
    signed: Mapped[date | None] = mapped_column(Date, index=True)
    published: Mapped[date | None] = mapped_column(Date)
    disposition: Mapped[str | None] = mapped_column(Text)  # amends / revokes / see also


class PresidentialText(Base):
    """A presidential document's full text, with a full-text search vector."""

    __tablename__ = "presidential_texts"
    __table_args__ = (Index("ix_presidential_texts_search", "search", postgresql_using="gin"),)

    document_number: Mapped[str] = mapped_column(String, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    search: Mapped[str] = _search_vector("text")


class CongressTrade(Base):
    """One transaction in a PTR. Amounts are the reported range's bounds (dollars);
    amount_max is None for the open-ended top range."""

    __tablename__ = "congress_trades"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String, index=True)
    chamber: Mapped[str] = mapped_column(String)
    owner: Mapped[str | None] = mapped_column(String)  # self, spouse, joint, child
    ticker: Mapped[str | None] = mapped_column(String, index=True)
    asset_name: Mapped[str | None] = mapped_column(String)
    asset_type: Mapped[str | None] = mapped_column(String)
    # purchase, sale, sale_partial or exchange
    trans_type: Mapped[str | None] = mapped_column(String)
    trans_date: Mapped[date] = mapped_column(Date, index=True)
    notified: Mapped[date | None] = mapped_column(Date)
    amount_min: Mapped[float | None] = mapped_column(Float)
    amount_max: Mapped[float | None] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text)


class CotPosition(Base):
    """One trader group's futures positions in a market on a CFTC report date (cot.py)."""

    __tablename__ = "cot_positions"

    report: Mapped[str] = mapped_column(String, primary_key=True)
    market_code: Mapped[str] = mapped_column(String, primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    group: Mapped[str] = mapped_column(String, primary_key=True)
    market_name: Mapped[str | None] = mapped_column(String)
    open_interest: Mapped[float | None] = mapped_column(Float)
    long: Mapped[float | None] = mapped_column(Float)
    short: Mapped[float | None] = mapped_column(Float)
    spread: Mapped[float | None] = mapped_column(Float)


class FilingText(Base):
    """A narrative section of a filing as plain text (filing_text.py): 10-K/10-Q risk
    factors and MD&A, 8-K earnings press releases, with a full-text search vector."""

    __tablename__ = "filing_texts"
    __table_args__ = (Index("ix_filing_texts_search", "search", postgresql_using="gin"),)

    accession: Mapped[str] = mapped_column(String, primary_key=True)
    section: Mapped[str] = mapped_column(String, primary_key=True)  # mdna, EX-99.1...
    cik: Mapped[int] = mapped_column(index=True)
    form: Mapped[str] = mapped_column(String)
    filed: Mapped[date] = mapped_column(Date, index=True)
    period: Mapped[date | None] = mapped_column(Date)
    text: Mapped[str] = mapped_column(Text)
    search: Mapped[str] = _search_vector("text")


class FundHolding(Base):
    """One holding of a tracked fund (an index ETF) in an N-PORT report (funds.py).
    `identifier` is the CUSIP, else the ISIN, else the holding's name."""

    __tablename__ = "fund_holdings"

    fund: Mapped[str] = mapped_column(String, primary_key=True)  # the fund's ticker
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    identifier: Mapped[str] = mapped_column(String, primary_key=True)
    series_id: Mapped[str] = mapped_column(String)
    accession: Mapped[str] = mapped_column(String)
    filed: Mapped[date] = mapped_column(Date)
    cusip: Mapped[str | None] = mapped_column(String, index=True)
    isin: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)
    shares: Mapped[float | None] = mapped_column(Float)
    value_usd: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float | None] = mapped_column(Float)  # percent of net assets
    asset_category: Mapped[str | None] = mapped_column(String)


class ShortInterest(Base):
    """A stock's short position on a FINRA settlement date (shortinterest.py)."""

    __tablename__ = "short_interest"

    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    settlement_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    short_position: Mapped[float] = mapped_column(Float)
    average_daily_volume: Mapped[float | None] = mapped_column(Float)
    days_to_cover: Mapped[float | None] = mapped_column(Float)
    market: Mapped[str | None] = mapped_column(String)


class FederalObligation(Base):
    """Federal contract obligations in a month for one industry (NAICS code), from
    USAspending (contracts.py). Revised as agencies report; DoD reports 90 days late."""

    __tablename__ = "federal_obligations"

    month: Mapped[date] = mapped_column(Date, primary_key=True)  # first day of the month
    naics: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    amount: Mapped[float] = mapped_column(Float)


class ConflictEvent(Base):
    """An ACLED event (conflict.py): a battle, explosion or remote violence (drone and air
    strikes, shelling), violence against civilians, a protest or riot, or a strategic
    development, with its actors, place and reported fatalities. ACLED revises events;
    `updated` is its last modification."""

    __tablename__ = "conflict_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # event_id_cnty
    event_date: Mapped[date] = mapped_column(Date, index=True)
    disorder_type: Mapped[str | None] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String, index=True)
    sub_event_type: Mapped[str | None] = mapped_column(String, index=True)
    actor1: Mapped[str | None] = mapped_column(String)
    assoc_actor_1: Mapped[str | None] = mapped_column(String)
    actor2: Mapped[str | None] = mapped_column(String)
    assoc_actor_2: Mapped[str | None] = mapped_column(String)
    interaction: Mapped[str | None] = mapped_column(String)
    civilian_targeting: Mapped[str | None] = mapped_column(String)
    country: Mapped[str] = mapped_column(String, index=True)
    iso: Mapped[int | None]
    region: Mapped[str | None] = mapped_column(String)
    admin1: Mapped[str | None] = mapped_column(String)
    location: Mapped[str | None] = mapped_column(String)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    fatalities: Mapped[int] = mapped_column(default=0)
    source: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)
    tags: Mapped[str | None] = mapped_column(String)
    updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UcdpEvent(Base):
    """A UCDP georeferenced event of organized violence (conflict.py): state-based (1),
    non-state (2) or one-sided violence against civilians (3), with best, low and high
    fatality estimates. `dataset` is "ged" (the yearly, final release) or "candidate"
    (monthly releases of the current year, replaced when the yearly release covers them)."""

    __tablename__ = "ucdp_events"

    dataset: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    relid: Mapped[str | None] = mapped_column(String)
    year: Mapped[int] = mapped_column(index=True)
    type_of_violence: Mapped[int]
    conflict_name: Mapped[str | None] = mapped_column(String)
    dyad_name: Mapped[str | None] = mapped_column(String)
    side_a: Mapped[str | None] = mapped_column(String)
    side_b: Mapped[str | None] = mapped_column(String)
    country: Mapped[str] = mapped_column(String, index=True)
    country_id: Mapped[int | None]
    region: Mapped[str | None] = mapped_column(String)
    adm_1: Mapped[str | None] = mapped_column(String)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    date_start: Mapped[date] = mapped_column(Date, index=True)
    date_end: Mapped[date] = mapped_column(Date)
    deaths_a: Mapped[int] = mapped_column(default=0)
    deaths_b: Mapped[int] = mapped_column(default=0)
    deaths_civilians: Mapped[int] = mapped_column(default=0)
    deaths_unknown: Mapped[int] = mapped_column(default=0)
    best: Mapped[int] = mapped_column(default=0)
    low: Mapped[int] = mapped_column(default=0)
    high: Mapped[int] = mapped_column(default=0)
    source_headline: Mapped[str | None] = mapped_column(String)


class NewsVolume(Base):
    """Daily count of worldwide online news articles matching one of news.py's queries
    (GDELT), with the day's total of monitored articles to normalize by."""

    __tablename__ = "news_volume"

    query: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    articles: Mapped[int]
    total: Mapped[int | None]


class BisSeries(Base):
    """A BIS statistical series (credit.py): `key` is its dimension values joined by dots
    in the dataset's order (WS_TC Q.US.P.A.M.770.A = US credit to the private
    non-financial sector from all lenders, market value, % of GDP, adjusted for breaks)."""

    __tablename__ = "bis_series"

    flow: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    country: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str | None] = mapped_column(String)


class BisObservation(Base):
    __tablename__ = "bis_observations"

    flow: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)  # last day of the period
    value: Mapped[float] = mapped_column(Float)


class PanelObservation(Base):
    """A country-year value from a historical macro panel (macrohistory.py: the
    Jordà-Schularick-Taylor database): credit, GDP, crises, asset returns since 1870."""

    __tablename__ = "panel_observations"

    source: Mapped[str] = mapped_column(String, primary_key=True)  # jst
    country: Mapped[str] = mapped_column(String, primary_key=True)  # ISO 3166 alpha-3
    variable: Mapped[str] = mapped_column(String, primary_key=True)
    year: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[float] = mapped_column(Float)


class ResearchRun(Base):
    """One evaluation of a hypothesis variant (research.py): every run is kept, so a result
    is judged against how many variants were tried, and the holdout's single use shows."""

    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis: Mapped[str] = mapped_column(String, index=True)  # H001...
    variant: Mapped[str] = mapped_column(String)  # canonical JSON of the parameters
    period: Mapped[str] = mapped_column(String)  # design, validation, holdout
    frozen: Mapped[bool] = mapped_column(default=False)  # the design chosen for the holdout
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    code_version: Mapped[str | None] = mapped_column(String)
    metrics: Mapped[str] = mapped_column(Text)  # JSON


class XbrlFiling(Base):
    """A filing with XBRL financial data, from EDGAR's quarterly xbrl.idx (population.py):
    every company that filed one since 2009, alive or dead, which is the research
    population free of survivorship bias."""

    __tablename__ = "xbrl_filings"

    accession: Mapped[str] = mapped_column(String, primary_key=True)
    cik: Mapped[int] = mapped_column(index=True)
    form: Mapped[str] = mapped_column(String, index=True)
    filed: Mapped[date] = mapped_column(Date, index=True)
    company: Mapped[str | None] = mapped_column(String)  # name when filed


class CorporateEvent(Base):
    """A dated corporate event from SEC filings (filing_events.py): one row per 8-K item, or
    per filing for other event forms (13D/13G stakes, late filings, delistings, tender
    offers...). `accepted` is when the filing became public."""

    __tablename__ = "corporate_events"

    accession: Mapped[str] = mapped_column(String, primary_key=True)
    item: Mapped[str] = mapped_column(String, primary_key=True)  # "" for non-8-K forms
    cik: Mapped[int] = mapped_column(index=True)
    form: Mapped[str] = mapped_column(String, index=True)
    filed: Mapped[date] = mapped_column(Date, index=True)
    accepted: Mapped[datetime | None] = mapped_column(DateTime)
    report_date: Mapped[date | None] = mapped_column(Date)


class ForwardOutcome(Base):
    """Derived: what happened after a company's metrics snapshot (outcomes.py), the label
    signals are scored against. Returns are total returns from the next close; excess
    returns are against SPY, the snapshot's universe median and its sector median.
    Columns stay empty until their horizon has passed."""

    __tablename__ = "forward_outcomes"

    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    cik: Mapped[int] = mapped_column(index=True)
    sector: Mapped[str | None] = mapped_column(String)
    ret_1m: Mapped[float | None] = mapped_column(Float)
    ret_3m: Mapped[float | None] = mapped_column(Float)
    ret_6m: Mapped[float | None] = mapped_column(Float)
    ret_12m: Mapped[float | None] = mapped_column(Float)
    excess_spy_3m: Mapped[float | None] = mapped_column(Float)
    excess_spy_12m: Mapped[float | None] = mapped_column(Float)
    excess_universe_3m: Mapped[float | None] = mapped_column(Float)
    excess_universe_12m: Mapped[float | None] = mapped_column(Float)
    excess_sector_3m: Mapped[float | None] = mapped_column(Float)
    excess_sector_12m: Mapped[float | None] = mapped_column(Float)
    max_drawdown_12m: Mapped[float | None] = mapped_column(Float)
    pe_change_12m: Mapped[float | None] = mapped_column(Float)  # log(P/E later / P/E now)
    ev_ebit_change_12m: Mapped[float | None] = mapped_column(Float)


class IoCode(Base):
    """A row or column code of BEA's input-output tables (industries.py): an industry or
    commodity, a final-demand column (F...: consumption, investment, exports, government),
    a value-added row (V...) or a total (T...). `level` is the table's level of detail; detail
    and summary industries carry their parents in BEA's hierarchy."""

    __tablename__ = "io_codes"

    level: Mapped[str] = mapped_column(String, primary_key=True)  # sector, summary, detail
    code: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(
        String
    )  # industry, commodity, final_demand, value_added, total
    summary: Mapped[str | None] = mapped_column(String)  # summary parent (detail industries)
    sector: Mapped[str | None] = mapped_column(String)  # sector parent (summary and detail)


class IoNaics(Base):
    """A NAICS code (any length: a prefix covers its subcodes) in a BEA detail industry, from
    BEA's concordance. Government industries have none."""

    __tablename__ = "io_naics"

    code: Mapped[str] = mapped_column(String, primary_key=True)  # BEA detail industry
    naics: Mapped[str] = mapped_column(String, primary_key=True, index=True)


class IoUse(Base):
    """One cell of a BEA use table, as published: the value (millions of dollars) of a
    commodity used by an industry or a final-demand column in a year. Includes the total and
    value-added rows and columns, so shares need no other table."""

    __tablename__ = "io_uses"

    level: Mapped[str] = mapped_column(String, primary_key=True)  # summary, detail
    year: Mapped[int] = mapped_column(primary_key=True)
    commodity: Mapped[str] = mapped_column(String, primary_key=True)
    buyer: Mapped[str] = mapped_column(String, primary_key=True, index=True)  # industry or F...
    value: Mapped[float] = mapped_column(Float)


class Concordance(Base):
    """One step of a published classification concordance (Census, industries.py): a code
    of one system (`sic1987`, `naics2002`...) and a code of the next that covers part or
    all of it. Many-to-many; Census doesn't give shares."""

    __tablename__ = "concordances"

    system: Mapped[str] = mapped_column(String, primary_key=True)  # sic1987
    code: Mapped[str] = mapped_column(String, primary_key=True)
    target_system: Mapped[str] = mapped_column(String, primary_key=True)  # naics2002
    target_code: Mapped[str] = mapped_column(String, primary_key=True)


class GraphNode(Base):
    """Derived (graph.py): a node of the supply-chain graph. Keys are typed:
    `industry:334413` (BEA detail), `summary:334` (BEA summary), `theme:defense`."""

    __tablename__ = "graph_nodes"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    label: Mapped[str] = mapped_column(Text)


class GraphEdge(Base):
    """Derived (graph.py): a typed edge. `supplies` runs from supplier to buyer, with the
    dollars (millions) and the share of the buyer's intermediate inputs (`buyer_share`) and
    of the supplier's output (`supplier_share`); `part_of` runs from a node to its parent
    (industry to summary, company to theme); `maps_to` from a theme or a company to an
    industry, with `value` the company's weight in it (its SIC code's share of NAICS codes
    there). `source` says where the edge comes from (bea,
    themes, curated) and `year` the data year."""

    __tablename__ = "graph_edges"

    src: Mapped[str] = mapped_column(String, primary_key=True)
    dst: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String)
    year: Mapped[int | None]
    value: Mapped[float | None] = mapped_column(Float)
    buyer_share: Mapped[float | None] = mapped_column(Float)
    supplier_share: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)


class DistressScore(Base):
    """A watched company's score under H001's frozen distress model on one day (distress.py):
    derived, recomputed by the daily sync. `flagged` when the score is in the top 2% of the
    whole research population that day; a company *entering* the top 2% is the alert."""

    __tablename__ = "distress_scores"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    cik: Mapped[int] = mapped_column(primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    cutoff: Mapped[float] = mapped_column(Float)  # the population's top-2% score that day
    flagged: Mapped[bool]
    signals: Mapped[str] = mapped_column(Text)  # JSON list of the signals present
    z: Mapped[float | None] = mapped_column(Float)  # Altman Z''
