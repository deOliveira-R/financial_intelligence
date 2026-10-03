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
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from fin_intel.db import Base

# --- raw layer and bookkeeping ---------------------------------------------------------


class RawResponse(Base):
    """One HTTP call to a provider. The body lives in a content-addressed file under raw_dir."""

    __tablename__ = "raw_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    dataset: Mapped[str] = mapped_column(String(64))  # e.g. companyfacts, daily_prices
    key: Mapped[str | None] = mapped_column(String(64))  # e.g. CIK, ticker, series id
    params: Mapped[str | None] = mapped_column(Text)  # JSON, without credentials
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String(64))
    size: Mapped[int]

    __table_args__ = (Index("ix_raw_lookup", "provider", "dataset", "key", "fetched_at"),)


class SyncRun(Base):
    """One CLI sync command."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # running, ok, partial, failed
    items_ok: Mapped[int] = mapped_column(default=0)
    items_failed: Mapped[int] = mapped_column(default=0)
    message: Mapped[str | None] = mapped_column(Text)


class SyncState(Base):
    """Latest sync outcome per item, e.g. (tiingo, daily_prices, AAPL)."""

    __tablename__ = "sync_state"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    rows: Mapped[int | None]


# --- entities --------------------------------------------------------------------------


class Issuer(Base):
    """A company or fund registered with the SEC, identified by CIK."""

    __tablename__ = "issuers"

    cik: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String(256))


class Security(Base):
    """A tradable instrument. `id` is our internal identifier, stable across ticker changes.

    `ticker` is the symbol it currently trades under. It is kept after delisting (so history
    stays reachable) and set to NULL only when another security takes the symbol over.
    `origin` is the source whose list created it (sec, massive, tiingo); `active` is False once
    it drops out of that source's current list. Each source only deactivates its own.
    """

    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(32), unique=True)
    active: Mapped[bool] = mapped_column(default=True, server_default=true())
    origin: Mapped[str] = mapped_column(String(16), server_default="sec")
    name: Mapped[str | None] = mapped_column(String(256))
    exchange: Mapped[str | None] = mapped_column(String(32))  # as named by SEC, e.g. Nasdaq
    mic: Mapped[str | None] = mapped_column(String(8))  # ISO 10383 primary exchange
    # Massive's type code: CS, ETF, PFD, WARRANT, UNIT, RIGHT, ADRC, ETN, FUND, ...
    security_type: Mapped[str | None] = mapped_column(String(16), index=True)
    cik: Mapped[int | None] = mapped_column(ForeignKey("issuers.cik"), index=True)
    figi: Mapped[str | None] = mapped_column(String(12), index=True)  # composite FIGI
    share_class_figi: Mapped[str | None] = mapped_column(String(12))
    # Last trading listing per Massive. Delisted securities keep `ticker` NULL (the symbol
    # may be reused); their old symbol lives in ticker_history with last_seen = delisted_on.
    delisted_on: Mapped[date | None] = mapped_column(Date)


class TickerHistory(Base):
    """Symbols a security has used, with the span over which our syncs observed each."""

    __tablename__ = "ticker_history"

    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    first_seen: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date] = mapped_column(Date)


# --- prices ----------------------------------------------------------------------------


class DailyBar(Base):
    """Unadjusted end-of-day OHLCV, per source. Adjusted prices are computed on read from
    corporate actions, so new splits and dividends never rewrite stored history."""

    __tablename__ = "daily_bars"

    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
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
    action: Mapped[str] = mapped_column(String(16), primary_key=True)  # split, dividend
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[float] = mapped_column(Float)


# --- fundamentals ----------------------------------------------------------------------


class Filing(Base):
    """An SEC filing that reported XBRL facts. fy/fp are SEC's labels for the filing."""

    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    accession: Mapped[str] = mapped_column(String(32), unique=True)
    cik: Mapped[int] = mapped_column(ForeignKey("issuers.cik"), index=True)
    form: Mapped[str | None] = mapped_column(String(16))
    filed: Mapped[date | None] = mapped_column(Date)
    fiscal_year: Mapped[int | None]
    fiscal_period: Mapped[str | None] = mapped_column(String(8))
    # Derived: the filing's primary period end (latest duration end on or before filing).
    report_period_end: Mapped[date | None] = mapped_column(Date)


class Concept(Base):
    """An XBRL concept, e.g. us-gaap:Revenues."""

    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    taxonomy: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(256))
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
    unit: Mapped[str] = mapped_column(String(64), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    instant: Mapped[bool]
    value: Mapped[float]
    frame: Mapped[str | None] = mapped_column(String(16))
    # Denormalized from filings for the per-company queries every read path makes.
    cik: Mapped[int] = mapped_column(ForeignKey("issuers.cik"))
    # Derived (see derive.py): the fact's own period, from its dates and the fiscal calendar.
    period_type: Mapped[str | None] = mapped_column(String(16))
    fiscal_year: Mapped[int | None]
    fiscal_period: Mapped[str | None] = mapped_column(String(8))

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

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(512))
    units: Mapped[str | None] = mapped_column(String(128))
    frequency: Mapped[str | None] = mapped_column(String(16))
    seasonal_adjustment: Mapped[str | None] = mapped_column(String(16))
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    name: Mapped[str] = mapped_column(String(64), unique=True)  # e.g. "Fidelity Individual"
    broker: Mapped[str] = mapped_column(String(32))  # fidelity, vanguard, manual, ...
    account_type: Mapped[str] = mapped_column(String(32))  # taxable, ira, roth_ira, 401k, hsa
    taxable: Mapped[bool]
    number_last4: Mapped[str | None] = mapped_column(String(4))


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
    action: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id"))
    quantity: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))  # generic, fidelity, vanguard, manual
    source_ref: Mapped[str] = mapped_column(String(64), unique=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionSnapshot(Base):
    """Holdings as a broker reported them on a date: used to reconcile derived lots and to
    price holdings with no market data (e.g. 401(k) collective trusts without tickers)."""

    __tablename__ = "position_snapshots"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
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

    universe: Mapped[str] = mapped_column(String(32), primary_key=True)
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
