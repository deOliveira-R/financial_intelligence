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
    `active` is False once the security drops out of SEC's current ticker list.
    """

    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(32), unique=True)
    active: Mapped[bool] = mapped_column(default=True, server_default=true())
    name: Mapped[str | None] = mapped_column(String(256))
    exchange: Mapped[str | None] = mapped_column(String(32))
    cik: Mapped[int | None] = mapped_column(ForeignKey("issuers.cik"), index=True)
    figi: Mapped[str | None] = mapped_column(String(12))


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
