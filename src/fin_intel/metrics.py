"""Company metrics for screening: valuation, quality, growth, scores and cyclicality.

Computed per issuer's primary security from standard statements (statements.py) and the
latest price, and stored with the date they were computed. Each day's row uses only what
was known that day, so the table accumulates a point-in-time history for backtests.

- TTM (trailing twelve months) flows: the last four reported quarters when they are
  consecutive and span about a year, otherwise the latest fiscal year (annual-only
  filers, e.g. foreign companies filing 20-F).
- Balances: the latest balance sheet; growth and scores compare with a year earlier.
- Currency: foreign filers' financials (e.g. a 20-F in DKK) are converted to US dollars
  at the latest exchange rate (fx.py), so ratios against the US price are like for like.
  Periods reported in another currency (before a switch) are dropped. Without a rate,
  market-cap-based metrics are left empty.
- Point in time: metrics for a past `as_of` use only figures first filed by then
  (`first_filed`; a 10-K's comparative column doesn't make last year's numbers new), the
  close and exchange rates of that day, and the listings trading then, including ones
  delisted since. Restated values replace the original ones, a small look-ahead.
- Splits: share counts and per-share figures are expressed as of their filing; splits
  between that filing and `as_of` are applied so they match the day's price.
- Market cap: price x shares outstanding (cover-page count, else diluted weighted shares),
  kept only if consistent with price / EPS. Multi-class companies whose count, EPS and
  price refer to different classes (BRK-A/BRK-B), or that report per class only (Greif),
  get no market-cap-based metrics rather than wrong ones.
"""

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fin_intel import fx
from fin_intel.db import upsert
from fin_intel.models import CompanyMetrics, CorporateAction, DailyBar, Security, StatementItem

STALE_AFTER = timedelta(days=550)  # newest financials older than this: skip (likely gone)
CURRENT_WINDOW = timedelta(days=400)  # inputs must be this close to the latest financials
PRIMARY_TYPES = ("CS", "ADRC", "OS")
ADR_SHARES_WINDOW = timedelta(days=120)  # depositary share counts older than this: unknown
PRIMARY_MICS = ("XNYS", "XNAS", "XASE", "XTAI", "ROCO")  # US, Taiwan (TWSE, TPEx)


@dataclass(frozen=True)
class Item:
    period_start: date
    period_end: date
    fiscal_period: str | None
    value: float
    filed: date | None = None  # the filing this value is from (latest restatement)


def _ttm(items: list[Item], as_of_end: date | None = None) -> tuple[float, date] | None:
    """Trailing twelve months ending at the latest quarter (or at/before `as_of_end`)."""
    quarters = sorted(
        (i for i in items if i.fiscal_period in ("Q1", "Q2", "Q3", "Q4")),
        key=lambda i: i.period_end,
    )
    annual = sorted((i for i in items if i.fiscal_period == "FY"), key=lambda i: i.period_end)
    if as_of_end:
        quarters = [q for q in quarters if q.period_end <= as_of_end]
        annual = [a for a in annual if a.period_end <= as_of_end]
    if len(quarters) >= 4:
        last4 = quarters[-4:]
        consecutive = all(
            0 < (b.period_start - a.period_end).days <= 7
            for a, b in zip(last4, last4[1:], strict=False)
        )
        span = (last4[-1].period_end - last4[0].period_start).days
        if consecutive and 350 <= span <= 380:
            if not annual or last4[-1].period_end >= annual[-1].period_end:
                return sum(q.value for q in last4), last4[-1].period_end
    if annual:
        return annual[-1].value, annual[-1].period_end
    return None


def _latest_instant(
    items: list[Item], at_or_before: date | None = None
) -> tuple[float, date] | None:
    rows = sorted(items, key=lambda i: i.period_end)
    if at_or_before:
        rows = [i for i in rows if i.period_end <= at_or_before]
    return (rows[-1].value, rows[-1].period_end) if rows else None


FOREIGN_ISSUERS = 10**10  # ids at or above: issuers from other regulators (world.py)
MIN_TRADING_DAYS = 5  # an OTC line must trade this many days a month to price a company


def primary_securities(session: Session, as_of: date | None = None) -> dict[int, Security]:
    """Per issuer, its main listed share: common stock (or ADR) on a major exchange, the
    one with the highest recent dollar volume when there are several. For a past `as_of`,
    the listings trading then (with a bar in the month before), active now or not.

    Companies known from other regulators can also be priced by a linked US listing (an
    OTC ordinary line or an ADR, crosslist.py) that trades at least MIN_TRADING_DAYS a
    month; their home-market listing wins when we have one."""
    historical = as_of is not None and as_of < date.today()
    end = as_of or date.today()
    activity = {
        sid: (dollars, days)
        for sid, dollars, days in session.execute(
            select(
                DailyBar.security_id,
                func.avg(DailyBar.close * DailyBar.volume),
                func.count(),
            )
            .where(DailyBar.date > end - timedelta(days=30), DailyBar.date <= end)
            .group_by(DailyBar.security_id)
        )
    }
    conditions = [
        Security.cik.is_not(None),
        Security.security_type.in_(PRIMARY_TYPES),
        Security.mic.in_(PRIMARY_MICS) | (Security.cik >= FOREIGN_ISSUERS),
    ]
    if not historical:
        conditions.append(Security.active)

    def rank(s: Security) -> tuple[bool, float]:
        return (s.currency is not None, activity.get(s.id, (0.0, 0))[0] or 0.0)

    out: dict[int, Security] = {}
    for s in session.scalars(select(Security).where(*conditions)):
        dollars, days = activity.get(s.id, (0.0, 0))
        if historical and not days:
            continue
        us_line_for_foreign = s.cik >= FOREIGN_ISSUERS and s.currency is None
        if us_line_for_foreign and days < MIN_TRADING_DAYS:
            continue
        current = out.get(s.cik)
        if current is None or rank(s) > rank(current):
            out[s.cik] = s
    return out


def _latest_closes(
    session: Session, ids: list[int], as_of: date | None = None
) -> dict[int, tuple[float, date]]:
    stmt = select(DailyBar.security_id, func.max(DailyBar.date).label("d")).where(
        DailyBar.security_id.in_(ids)
    )
    if as_of is not None:
        stmt = stmt.where(DailyBar.date <= as_of, DailyBar.date > as_of - timedelta(days=10))
    latest = stmt.group_by(DailyBar.security_id).subquery()
    rows = session.execute(
        select(DailyBar.security_id, DailyBar.date, DailyBar.close).join(
            latest, (latest.c.security_id == DailyBar.security_id) & (latest.c.d == DailyBar.date)
        )
    )
    return {sid: (close, d) for sid, d, close in rows if close}


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0 or math.isnan(b):
        return None
    return a / b


def compute(session: Session, as_of: date | None = None) -> int:
    """Compute metrics as of a day (default today) for every issuer with a primary
    security and fresh financials, from what was known that day; returns rows written."""
    historical = as_of is not None and as_of < date.today()
    as_of = as_of or date.today()
    primaries = primary_securities(session, as_of if historical else None)
    prices = _latest_closes(
        session, [s.id for s in primaries.values()], as_of if historical else None
    )
    rates = fx.usd_rates(session, as_of if historical else None)
    splits = _splits(session, [s.id for s in primaries.values()])
    known = func.coalesce(StatementItem.first_filed, StatementItem.filed)
    by_issuer: dict[int, list[tuple[str, str, Item]]] = {}
    for cik, line_item, unit, start, end, fp, value, filed in session.execute(
        select(
            StatementItem.cik,
            StatementItem.line_item,
            StatementItem.unit,
            StatementItem.period_start,
            StatementItem.period_end,
            StatementItem.fiscal_period,
            StatementItem.value,
            StatementItem.filed,
        ).where(StatementItem.cik.in_(list(primaries)), known.is_(None) | (known <= as_of))
    ):
        item = Item(start, end, fp, value, filed)
        by_issuer.setdefault(cik, []).append((line_item, unit, item))

    records = []
    for cik, security in primaries.items():
        rows = by_issuer.get(cik)
        if not rows or security.id not in prices:
            continue
        currency, items = in_dollars(rows, rates)
        items = split_adjusted(items, splits.get(security.id, []), as_of)
        price = prices[security.id][0]
        if security.currency and security.currency != "USD":  # a home listing abroad
            if security.currency not in rates:
                continue
            price *= rates[security.currency]
        row = _issuer_metrics(
            items,
            price,
            as_of,
            valued=currency in rates,
            listing_shares=_adr_shares(security, as_of),
        )
        if row is not None:
            records.append(
                {
                    "security_id": security.id,
                    "cik": cik,
                    "as_of": as_of,
                    "price": price,
                    "currency": currency,
                    **row,
                }
            )
    session.execute(delete(CompanyMetrics).where(CompanyMetrics.as_of == as_of))
    upsert(session, CompanyMetrics, records, key=["security_id", "as_of"])
    session.commit()
    return len(records)


def in_dollars(
    rows: list[tuple[str, str, Item]], rates: dict[str, float]
) -> tuple[str | None, dict[str, list[Item]]]:
    """An issuer's items by line item, in US dollars when a rate exists. The reporting
    currency is that of its latest revenue, net income or assets; monetary items in any
    other currency (periods before a currency switch) are dropped."""
    monetary = [
        (item.period_end, unit)
        for line_item, unit, item in rows
        if line_item in ("revenue", "net_income", "total_assets") and "/" not in unit
    ]
    currency = max(monetary)[1] if monetary else None
    rate = rates.get(currency or "", 1.0)
    out: dict[str, list[Item]] = {}
    for line_item, unit, item in rows:
        base, _, per = unit.partition("/")
        if base == currency:  # amounts and per-share amounts
            item = replace(item, value=item.value * rate)
        elif len(base) == 3 and base.isupper() and per in ("", "shares"):
            continue  # another currency
        out.setdefault(line_item, []).append(item)
    return currency, out


SHARE_ITEMS = ("shares_outstanding", "shares_diluted")
PER_SHARE_ITEMS = ("eps_diluted", "eps_basic")


def _splits(session: Session, ids: list[int]) -> dict[int, list[tuple[date, float]]]:
    """Each security's splits (ex-date, new shares per old), one per date across sources."""
    out: dict[int, dict[date, float]] = {}
    for sid, ex_date, ratio in session.execute(
        select(CorporateAction.security_id, CorporateAction.ex_date, CorporateAction.value).where(
            CorporateAction.security_id.in_(ids), CorporateAction.action == "split"
        )
    ):
        if ratio:
            out.setdefault(sid, {})[ex_date] = ratio
    return {sid: sorted(by_date.items()) for sid, by_date in out.items()}


def split_factor(splits: list[tuple[date, float]], since: date, until: date) -> float:
    """Shares at `until` per share at `since`: the product of splits in between (inverted
    when `until` comes first)."""
    lo, hi = sorted((since, until))
    factor = math.prod(r for d, r in splits if lo < d <= hi)
    return factor if until >= since else 1 / factor


def split_adjusted(
    items: dict[str, list[Item]], splits: list[tuple[date, float]], as_of: date
) -> dict[str, list[Item]]:
    """Share counts and per-share figures restated into the share terms of `as_of`. Each
    value is in the terms of the filing it came from (later filings restate for splits)."""
    if not splits:
        return items
    out = dict(items)
    for name in (*SHARE_ITEMS, *PER_SHARE_ITEMS):
        adjusted = []
        for i in items.get(name, []):
            f = split_factor(splits, i.filed or i.period_end, as_of)
            adjusted.append(replace(i, value=i.value * f if name in SHARE_ITEMS else i.value / f))
        if adjusted:
            out[name] = adjusted
    return out


def _adr_shares(security: Security, as_of: date) -> float | None:
    """For an ADR, its depositary shares outstanding if known recently, else 0 (no market
    cap: the ADR ratio is unknown); None for other securities (use the financials' count)."""
    if security.security_type != "ADRC":
        return None
    fresh = security.shares_as_of and as_of - security.shares_as_of <= ADR_SHARES_WINDOW
    return (security.shares_outstanding or 0.0) if fresh else 0.0


def _issuer_metrics(
    items: dict[str, list[Item]],
    price: float,
    as_of: date,
    valued: bool = True,
    listing_shares: float | None = None,
) -> dict | None:
    """Metrics from an issuer's items (in US dollars). Without `valued` (no exchange rate
    for its currency) market-cap-based metrics are left empty. `listing_shares` overrides
    the financials' share count (ADRs, which may bundle several ordinary shares); 0 means
    the listing's count is unknown."""

    # Values must belong to the period being described: a years-old share count or debt
    # balance (e.g. a company that now reports only per share class) is ignored rather
    # than mixed with today's price.
    def ttm(name: str, end: date | None = None) -> float | None:
        r = _ttm(items.get(name, []), end)
        reference = end or period_end
        return r[0] if r and reference - r[1] <= CURRENT_WINDOW else None

    def bal(name: str, at: date | None = None) -> float | None:
        r = _latest_instant(items.get(name, []), at)
        reference = at or period_end
        return r[0] if r and reference - r[1] <= CURRENT_WINDOW else None

    revenue_ttm = _ttm(items.get("revenue", []))
    latest_assets = _latest_instant(items.get("total_assets", []))
    period_end = max(
        (r[1] for r in (revenue_ttm, latest_assets, _ttm(items.get("net_income", []))) if r),
        default=None,
    )
    if period_end is None or as_of - period_end > STALE_AFTER:
        return None
    year_ago = period_end - timedelta(days=365)

    revenue, net_income = ttm("revenue"), ttm("net_income")
    ebit = ttm("operating_income")
    depreciation = ttm("depreciation")
    ocf, capex = ttm("operating_cash_flow"), ttm("capex")
    fcf = None if ocf is None else ocf - (capex or 0.0)
    gross = ttm("gross_profit")
    if gross is None and revenue is not None and (cogs := ttm("cost_of_revenue")) is not None:
        gross = revenue - cogs
    interest, tax, pretax = ttm("interest_expense"), ttm("income_tax"), ttm("pretax_income")
    dividends, buybacks = ttm("dividends_paid"), ttm("buybacks")

    cash = (bal("cash") or 0.0) + (bal("short_term_investments") or 0.0)
    debt = (bal("long_term_debt") or 0.0) + (bal("short_term_debt") or 0.0)
    leases = bal("lease_liabilities") or 0.0
    equity, assets = bal("equity"), bal("total_assets")
    liabilities = bal("total_liabilities")
    if liabilities is None and assets is not None and equity is not None:
        liabilities = assets - equity
    current_assets, current_liabilities = bal("current_assets"), bal("current_liabilities")
    shares = (
        bal("shares_outstanding")
        or ttm_last(items.get("shares_diluted", []))
        or implied_shares(items, period_end)
    )

    if listing_shares is not None:  # an ADR: its own count, in depositary shares
        market_cap = price * listing_shares if valued and listing_shares else None
    else:
        consistent = shares and _consistent(shares, price, items)
        market_cap = price * shares if valued and shares and consistent else None
    ev = None if market_cap is None else market_cap + debt + leases - cash
    ebitda = None if ebit is None else ebit + (depreciation or 0.0)
    tax_rate = min(max(_div(tax, pretax) or 0.21, 0.0), 0.5)
    invested = None if equity is None else equity + debt + leases - cash

    prev_revenue, prev_net = ttm("revenue", year_ago), ttm("net_income", year_ago)
    prev_ebit = ttm("operating_income", year_ago)
    margin_5y = _operating_margins(items)[0]
    operating_margin = _div(ebit, revenue)

    return {
        "period_end": period_end,
        "market_cap": market_cap,
        "enterprise_value": ev,
        "revenue_ttm": revenue,
        "net_income_ttm": net_income,
        "ebit_ttm": ebit,
        "fcf_ttm": fcf,
        "pe": _div(market_cap, net_income) if net_income and net_income > 0 else None,
        "ev_ebit": _div(ev, ebit) if ebit and ebit > 0 else None,
        "ev_ebitda": _div(ev, ebitda) if ebitda and ebitda > 0 else None,
        "ev_sales": _div(ev, revenue),
        "p_fcf": _div(market_cap, fcf) if fcf and fcf > 0 else None,
        "p_b": _div(market_cap, equity) if equity and equity > 0 else None,
        "earnings_yield": _div(ebit, ev) if ev and ev > 0 else None,
        "fcf_yield": _div(fcf, market_cap),
        "dividend_yield": _div(abs(dividends), market_cap) if dividends else 0.0,
        "shareholder_yield": _div(abs(dividends or 0.0) + abs(buybacks or 0.0), market_cap),
        "gross_margin": _div(gross, revenue),
        "operating_margin": operating_margin,
        "net_margin": _div(net_income, revenue),
        "roe": _div(net_income, equity) if equity and equity > 0 else None,
        "roic": _div(None if ebit is None else ebit * (1 - tax_rate), invested)
        if invested and invested > 0
        else None,
        "debt_to_equity": _div(debt + leases, equity) if equity and equity > 0 else None,
        "net_debt_to_ebitda": _div(debt + leases - cash, ebitda) if ebitda and ebitda > 0 else None,
        "current_ratio": _div(current_assets, current_liabilities),
        "interest_coverage": _div(ebit, abs(interest)) if interest else None,
        "revenue_growth": _growth(revenue, prev_revenue),
        "earnings_growth": _growth(net_income, prev_net),
        "ebit_growth": _growth(ebit, prev_ebit),
        "operating_margin_5y": margin_5y,
        "operating_margin_vs_5y": None
        if margin_5y is None or operating_margin is None
        else operating_margin - margin_5y,
        "piotroski_f": _piotroski(items, period_end, year_ago),
        "altman_z": _altman(items, market_cap, liabilities, revenue, ebit),
    }


def _consistent(shares: float | None, price: float, items: dict[str, list[Item]]) -> bool:
    """Whether the share count, EPS and price are in the same share class's terms, judged
    on the latest fiscal year reporting both net income and EPS (same period on purpose:
    trailing EPS can lag trailing net income when a company only reports EPS quarterly).

    - The share count implied by that year (net income / EPS) must be within 1.5x of the
      reported count: catches a count on another basis than EPS.
    - Price / EPS must be at least 1: catches a price from another class than the EPS.
      Berkshire reports per Class A share (~$62,000 EPS) while BRK-B trades near $480, a
      "P/E" of 0.008. No real company earns its whole share price in a year.
    """
    eps_by_end = {
        i.period_end: i.value
        for name in ("eps_diluted", "eps_basic")  # Berkshire reports basic only
        for i in items.get(name, [])
        if i.fiscal_period == "FY"
    }
    years = [
        (i.period_end, i.value, eps_by_end[i.period_end])
        for i in items.get("net_income", [])
        if i.fiscal_period == "FY" and i.period_end in eps_by_end
    ]
    if not shares or not years:
        return True  # nothing to compare
    _, net_income, eps = max(years)
    if net_income <= 0 or eps <= 0:
        return True
    if price / eps < 1:
        return False
    return 1 / 1.5 <= shares / (net_income / eps) <= 1.5


def _growth(now: float | None, before: float | None) -> float | None:
    """Change relative to the earlier value's magnitude (works across sign changes)."""
    if now is None or before is None or before == 0:
        return None
    return (now - before) / abs(before)


def implied_shares(items: dict[str, list[Item]], period_end: date) -> float | None:
    """Shares implied by the latest fiscal year's net income and basic EPS, for filers that
    tag no share count outside dimensional tables (most European ESEF reports)."""
    eps = {i.period_end: i.value for i in items.get("eps_basic", []) if i.fiscal_period == "FY"}
    years = sorted(
        (i.period_end, i.value, eps[i.period_end])
        for i in items.get("net_income", [])
        if i.fiscal_period == "FY" and i.period_end in eps
    )
    if not years:
        return None
    end, net_income, per_share = years[-1]
    if period_end - end > CURRENT_WINDOW or not per_share or net_income * per_share <= 0:
        return None
    return net_income / per_share


def ttm_last(items: list[Item]) -> float | None:
    """The most recent value of a per-period quantity (e.g. weighted diluted shares)."""
    rows = sorted(items, key=lambda i: i.period_end)
    return rows[-1].value if rows else None


def _operating_margins(items: dict[str, list[Item]]) -> tuple[float | None, int]:
    """Average annual operating margin over up to the last five fiscal years."""
    revenue = {i.period_end: i.value for i in items.get("revenue", []) if i.fiscal_period == "FY"}
    ebit = {
        i.period_end: i.value for i in items.get("operating_income", []) if i.fiscal_period == "FY"
    }
    years = sorted(set(revenue) & set(ebit))[-5:]
    margins = [ebit[y] / revenue[y] for y in years if revenue[y]]
    return (
        (sum(margins) / len(margins), len(margins)) if len(margins) >= 3 else (None, len(margins))
    )


def _piotroski(items: dict[str, list[Item]], end: date, year_ago: date) -> int | None:
    """Piotroski F-score (0-9) from TTM vs a year earlier; None if inputs are missing."""

    def ttm(name, at):
        r = _ttm(items.get(name, []), at)
        return r[0] if r else None

    def bal(name, at):
        r = _latest_instant(items.get(name, []), at)
        return r[0] if r else None

    ni, ni0 = ttm("net_income", end), ttm("net_income", year_ago)
    ocf = ttm("operating_cash_flow", end)
    assets, assets0 = bal("total_assets", end), bal("total_assets", year_ago)
    if ni is None or ni0 is None or ocf is None or not assets or not assets0:
        return None
    roa, roa0 = ni / assets, ni0 / assets0
    debt = (bal("long_term_debt", end) or 0.0) / assets
    debt0 = (bal("long_term_debt", year_ago) or 0.0) / assets0
    cr = _div(bal("current_assets", end), bal("current_liabilities", end))
    cr0 = _div(bal("current_assets", year_ago), bal("current_liabilities", year_ago))
    shares, shares0 = bal("shares_outstanding", end), bal("shares_outstanding", year_ago)
    rev, rev0 = ttm("revenue", end), ttm("revenue", year_ago)
    gross, gross0 = ttm("gross_profit", end), ttm("gross_profit", year_ago)
    gross_margin, gross_margin0 = _div(gross, rev), _div(gross0, rev0)
    turnover, turnover0 = _div(rev, assets), _div(rev0, assets0)
    signals = [
        ni > 0,
        ocf > 0,
        roa > roa0,
        ocf > ni,
        debt <= debt0,
        cr is not None and cr0 is not None and cr > cr0,
        shares is not None and shares0 is not None and shares <= shares0,
        gross_margin is not None and gross_margin0 is not None and gross_margin > gross_margin0,
        turnover is not None and turnover0 is not None and turnover > turnover0,
    ]
    return sum(signals)


def _altman(items, market_cap, liabilities, revenue, ebit) -> float | None:
    """Altman Z-score (original public-manufacturer weights); None if inputs are missing."""

    def bal(name):
        r = _latest_instant(items.get(name, []))
        return r[0] if r else None

    assets = bal("total_assets")
    ca, cl, re = bal("current_assets"), bal("current_liabilities"), bal("retained_earnings")
    if not assets or not liabilities or ca is None or cl is None or re is None:
        return None
    if market_cap is None or revenue is None or ebit is None:
        return None
    return (
        1.2 * (ca - cl) / assets
        + 1.4 * re / assets
        + 3.3 * ebit / assets
        + 0.6 * market_cap / liabilities
        + 1.0 * revenue / assets
    )
