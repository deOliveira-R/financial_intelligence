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
    false,
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
    # From SEC submissions: Standard Industrial Classification and filer status.
    sic: Mapped[int | None] = mapped_column(index=True)
    sic_description: Mapped[str | None] = mapped_column(String(256))
    filer_category: Mapped[str | None] = mapped_column(String(64))


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
    # Shares of this listing (for an ADR, depositary shares) per Massive's ticker details:
    # financials count ordinary shares, which an ADR may bundle (or split).
    shares_outstanding: Mapped[float | None] = mapped_column(Float)
    shares_as_of: Mapped[date | None] = mapped_column(Date)


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
    release_id: Mapped[int | None] = mapped_column(ForeignKey("economic_releases.id"))


class EconomicRelease(Base):
    """A FRED release (e.g. Consumer Price Index): the publication a series comes out in."""

    __tablename__ = "economic_releases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String(256))
    link: Mapped[str | None] = mapped_column(String(512))


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


class StatementItem(Base):
    """Derived: a standard financial statement line for an issuer and period (statements.py).
    `concept_id` is the XBRL concept the value came from."""

    __tablename__ = "statement_items"

    cik: Mapped[int] = mapped_column(ForeignKey("issuers.cik"), primary_key=True)
    line_item: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    period_type: Mapped[str] = mapped_column(String(16))
    fiscal_year: Mapped[int | None]
    fiscal_period: Mapped[str | None] = mapped_column(String(8))
    unit: Mapped[str] = mapped_column(String(64))
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
    currency: Mapped[str | None] = mapped_column(String(3))
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

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    accession: Mapped[str] = mapped_column(String(32), index=True)
    form_type: Mapped[str | None] = mapped_column(String(8))
    filing_date: Mapped[date | None] = mapped_column(Date, index=True)
    issuer_cik: Mapped[int] = mapped_column(index=True)
    issuer_symbol: Mapped[str | None] = mapped_column(String(32))
    owner_cik: Mapped[int | None]
    owner_name: Mapped[str | None] = mapped_column(String(256))
    relationship: Mapped[str | None] = mapped_column(String(64))
    owner_title: Mapped[str | None] = mapped_column(String(256))
    security_title: Mapped[str | None] = mapped_column(String(256))
    trans_date: Mapped[date] = mapped_column(Date, index=True)
    trans_code: Mapped[str | None] = mapped_column(String(4))
    acquired_disposed: Mapped[str | None] = mapped_column(String(2))
    shares: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    shares_after: Mapped[float | None] = mapped_column(Float)
    direct_indirect: Mapped[str | None] = mapped_column(String(2))
    plan_10b5_1: Mapped[bool] = mapped_column(default=False)


class InstitutionalFiler(Base):
    """A 13F filer: an institutional investment manager with $100M+ under management."""

    __tablename__ = "institutional_filers"

    cik: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String(256), index=True)


class InstitutionalPosition(Base):
    """A filer's holding of one security at a quarter end (13F), summed across the rows
    it reports it in (e.g. per subsidiary). Options are separate positions (put_call)."""

    __tablename__ = "institutional_positions"

    filer_cik: Mapped[int] = mapped_column(ForeignKey("institutional_filers.cik"), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    cusip: Mapped[str] = mapped_column(String(9), primary_key=True, index=True)
    put_call: Mapped[str] = mapped_column(String(4), primary_key=True)  # "" for shares
    issuer_name: Mapped[str | None] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(String(64))
    shares: Mapped[float | None] = mapped_column(Float)  # or principal amount, see share_type
    share_type: Mapped[str | None] = mapped_column(String(4))  # SH or PRN
    value: Mapped[float | None] = mapped_column(Float)  # US dollars
    accession: Mapped[str] = mapped_column(String(32))
    filed: Mapped[date | None] = mapped_column(Date)


class CusipMapping(Base):
    """Derived: which security a CUSIP is (via a FIGI in the filing or OpenFIGI)."""

    __tablename__ = "cusip_mappings"

    cusip: Mapped[str] = mapped_column(String(9), primary_key=True)
    figi: Mapped[str | None] = mapped_column(String(12))
    composite_figi: Mapped[str | None] = mapped_column(String(12))
    ticker: Mapped[str | None] = mapped_column(String(32))
    security_type: Mapped[str | None] = mapped_column(String(64))
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id"))


class CongressReport(Base):
    """A member of Congress's Periodic Transaction Report (congress.py). `transactions` is
    None until the report itself is parsed (paper filings never are)."""

    __tablename__ = "congress_reports"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chamber: Mapped[str] = mapped_column(String(8))  # house or senate
    name: Mapped[str | None] = mapped_column(String(128), index=True)
    state: Mapped[str | None] = mapped_column(String(8))  # House state and district
    filed: Mapped[date | None] = mapped_column(Date, index=True)
    year: Mapped[int | None]
    electronic: Mapped[bool] = mapped_column(default=True)
    transactions: Mapped[int | None]


class CongressTrade(Base):
    """One transaction in a PTR. Amounts are the reported range's bounds (dollars);
    amount_max is None for the open-ended top range."""

    __tablename__ = "congress_trades"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    chamber: Mapped[str] = mapped_column(String(8))
    owner: Mapped[str | None] = mapped_column(String(16))  # self, spouse, joint, child
    ticker: Mapped[str | None] = mapped_column(String(16), index=True)
    asset_name: Mapped[str | None] = mapped_column(String(256))
    asset_type: Mapped[str | None] = mapped_column(String(64))
    # purchase, sale, sale_partial or exchange
    trans_type: Mapped[str | None] = mapped_column(String(16))
    trans_date: Mapped[date] = mapped_column(Date, index=True)
    notified: Mapped[date | None] = mapped_column(Date)
    amount_min: Mapped[float | None] = mapped_column(Float)
    amount_max: Mapped[float | None] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text)


class CotPosition(Base):
    """One trader group's futures positions in a market on a CFTC report date (cot.py)."""

    __tablename__ = "cot_positions"

    report: Mapped[str] = mapped_column(String(16), primary_key=True)
    market_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    group: Mapped[str] = mapped_column(String(16), primary_key=True)
    market_name: Mapped[str | None] = mapped_column(String(128))
    open_interest: Mapped[float | None] = mapped_column(Float)
    long: Mapped[float | None] = mapped_column(Float)
    short: Mapped[float | None] = mapped_column(Float)
    spread: Mapped[float | None] = mapped_column(Float)
