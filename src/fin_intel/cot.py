"""CFTC Commitments of Traders: who holds futures positions, weekly.

Positions are as of Tuesday and published Friday afternoon (3:30 pm ET), so a backtest may
only use them from the Friday on (`available_on`). Three reports, for a curated list of
markets:
- legacy (since 1986): commercial (hedgers) vs non-commercial (speculators), every market.
- disaggregated (since 2006), commodities: producers/merchants, swap dealers, managed money
  (hedge funds and CTAs: the trend-following "speculators"), other reportables.
- traders in financial futures (TFF, since 2006), financials: dealers, asset managers,
  leveraged funds, other reportables.
Non-reportable positions (small traders) appear in every report.

The usual signal is the COT index: a group's net position as a percentile of its range
over the last three years. Extremes in managed money or leveraged funds tend to mark
crowded trades; commercials' extremes tend to lead turns.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import CotPosition

# alias -> (CFTC contract market code, family, description)
MARKETS: dict[str, tuple[str, str, str]] = {
    "crude": ("067651", "commodity", "WTI crude oil (NYMEX)"),
    "natgas": ("023651", "commodity", "Natural gas, Henry Hub (NYMEX)"),
    "gasoline": ("111659", "commodity", "RBOB gasoline (NYMEX)"),
    "heatingoil": ("022651", "commodity", "NY Harbor ULSD (NYMEX)"),
    "gold": ("088691", "commodity", "Gold (COMEX)"),
    "silver": ("084691", "commodity", "Silver (COMEX)"),
    "copper": ("085692", "commodity", "Copper (COMEX)"),
    "platinum": ("076651", "commodity", "Platinum (NYMEX)"),
    "corn": ("002602", "commodity", "Corn (CBOT)"),
    "soybeans": ("005602", "commodity", "Soybeans (CBOT)"),
    "wheat": ("001602", "commodity", "Wheat, SRW (CBOT)"),
    "sp500": ("13874A", "financial", "E-mini S&P 500 (CME)"),
    "nasdaq": ("209742", "financial", "E-mini Nasdaq-100 (CME)"),
    "russell": ("239742", "financial", "E-mini Russell 2000 (CME)"),
    "vix": ("1170E1", "financial", "VIX futures (CFE)"),
    "ust2y": ("042601", "financial", "2-year Treasury note (CBOT)"),
    "ust5y": ("044601", "financial", "5-year Treasury note (CBOT)"),
    "ust10y": ("043602", "financial", "10-year Treasury note (CBOT)"),
    "ustbond": ("020601", "financial", "Treasury bond (CBOT)"),
    "sofr": ("134741", "financial", "3-month SOFR (CME)"),
    "fedfunds": ("045601", "financial", "30-day Fed funds (CBOT)"),
    "dollar": ("098662", "financial", "US dollar index (ICE)"),
    "euro": ("099741", "financial", "Euro FX (CME)"),
    "yen": ("097741", "financial", "Japanese yen (CME)"),
    "pound": ("096742", "financial", "British pound (CME)"),
    "bitcoin": ("133741", "financial", "Bitcoin (CME)"),
}
BY_CODE = {code: alias for alias, (code, _, _) in MARKETS.items()}

REPORTS = {"legacy": "6dca-aqww", "disaggregated": "72hh-3qpy", "tff": "gpe5-46if"}
FAMILY_REPORT = {"commodity": "disaggregated", "financial": "tff"}

# report -> group -> (long, short, spread) field candidates; the API's names are irregular.
FIELDS: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]]] = {
    "legacy": {
        "noncommercial": (
            ("noncomm_positions_long_all",),
            ("noncomm_positions_short_all",),
            ("noncomm_postions_spread_all", "noncomm_positions_spread_all"),
        ),
        "commercial": (("comm_positions_long_all",), ("comm_positions_short_all",), ()),
        "nonreportable": (
            ("nonrept_positions_long_all",),
            ("nonrept_positions_short_all",),
            (),
        ),
    },
    "disaggregated": {
        "producer": (
            ("prod_merc_positions_long", "prod_merc_positions_long_all"),
            ("prod_merc_positions_short", "prod_merc_positions_short_all"),
            (),
        ),
        "swap": (
            ("swap_positions_long_all", "swap__positions_long_all"),
            ("swap__positions_short_all", "swap_positions_short_all"),
            ("swap__positions_spread_all", "swap_positions_spread_all"),
        ),
        "managed_money": (
            ("m_money_positions_long_all",),
            ("m_money_positions_short_all",),
            ("m_money_positions_spread", "m_money_positions_spread_all"),
        ),
        "other": (
            ("other_rept_positions_long", "other_rept_positions_long_all"),
            ("other_rept_positions_short", "other_rept_positions_short_all"),
            ("other_rept_positions_spread", "other_rept_positions_spread_all"),
        ),
        "nonreportable": (
            ("nonrept_positions_long_all",),
            ("nonrept_positions_short_all",),
            (),
        ),
    },
    "tff": {
        "dealer": (
            ("dealer_positions_long_all",),
            ("dealer_positions_short_all",),
            ("dealer_positions_spread_all",),
        ),
        "asset_manager": (
            ("asset_mgr_positions_long", "asset_mgr_positions_long_all"),
            ("asset_mgr_positions_short", "asset_mgr_positions_short_all"),
            ("asset_mgr_positions_spread", "asset_mgr_positions_spread_all"),
        ),
        "leveraged": (
            ("lev_money_positions_long", "lev_money_positions_long_all"),
            ("lev_money_positions_short", "lev_money_positions_short_all"),
            ("lev_money_positions_spread", "lev_money_positions_spread_all"),
        ),
        "other": (
            ("other_rept_positions_long", "other_rept_positions_long_all"),
            ("other_rept_positions_short", "other_rept_positions_short_all"),
            ("other_rept_positions_spread", "other_rept_positions_spread_all"),
        ),
        "nonreportable": (
            ("nonrept_positions_long_all",),
            ("nonrept_positions_short_all",),
            (),
        ),
    },
}
# The group whose positioning best captures speculation, per family.
SPECULATORS = {"commodity": "managed_money", "financial": "leveraged"}
FIELDS_OUT = ("net", "long", "short", "net_pct_oi", "oi", "index")
INDEX_WEEKS = 156  # three years


def available_on(report_date: date) -> date:
    """Tuesday's positions are published that Friday (later in holiday weeks; the lag is
    then a day or so longer than this assumes)."""
    return report_date + timedelta(days=3)


def _num(row: dict[str, Any], names: tuple[str, ...]) -> float | None:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            try:
                return float(value)
            except ValueError:
                return None
    return None


def parse(report: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per (market, date, group) from a page of Socrata rows."""
    out = []
    for row in rows:
        code = (row.get("cftc_contract_market_code") or "").strip()
        day = row.get("report_date_as_yyyy_mm_dd")
        if not code or not day:
            continue
        report_date = datetime.fromisoformat(day[:10]).date()
        for group, (longs, shorts, spreads) in FIELDS[report].items():
            out.append(
                {
                    "report": report,
                    "market_code": code,
                    "report_date": report_date,
                    "group": group,
                    "market_name": row.get("market_and_exchange_names"),
                    "open_interest": _num(row, ("open_interest_all",)),
                    "long": _num(row, longs),
                    "short": _num(row, shorts),
                    "spread": _num(row, spreads) if spreads else None,
                }
            )
    return out


def load(session: Session, report: str, rows: list[dict[str, Any]]) -> int:
    return upsert(
        session,
        CotPosition,
        parse(report, rows),
        key=["report", "market_code", "report_date", "group"],
    )


# --- reading ---------------------------------------------------------------------------


def resolve(market: str, group: str) -> tuple[str, str]:
    """(market code, report) for a market alias (or CFTC code) and group name."""
    market = market.lower()
    if market in MARKETS:
        code, family, _ = MARKETS[market]
    elif market.upper() in BY_CODE:
        code = market.upper()
        family = MARKETS[BY_CODE[code]][1]
    else:
        raise ValueError(f"unknown COT market {market!r}; one of {', '.join(MARKETS)}")
    group = group.lower()
    if group in FIELDS["legacy"] and group != "nonreportable":
        return code, "legacy"
    report = FAMILY_REPORT[family]
    if group not in FIELDS[report]:
        groups = sorted(set(FIELDS[report]) | {"noncommercial", "commercial"})
        raise ValueError(f"{market}: unknown group {group!r}; one of {', '.join(groups)}")
    return code, report


def history(session: Session, market: str, group: str) -> list[CotPosition]:
    code, report = resolve(market, group)
    return list(
        session.scalars(
            select(CotPosition)
            .where(
                CotPosition.market_code == code,
                CotPosition.report == report,
                CotPosition.group == group.lower(),
            )
            .order_by(CotPosition.report_date)
        )
    )


def values(rows: list[CotPosition], field: str) -> list[float | None]:
    """A field per row; `index` is the net position's percentile (0-100) within the
    trailing three years, using only data up to each row."""
    nets = [None if r.long is None or r.short is None else r.long - r.short for r in rows]
    if field == "net":
        return nets
    if field == "long":
        return [r.long for r in rows]
    if field == "short":
        return [r.short for r in rows]
    if field == "oi":
        return [r.open_interest for r in rows]
    if field == "net_pct_oi":
        return [
            None if n is None or not r.open_interest else 100 * n / r.open_interest
            for n, r in zip(nets, rows, strict=True)
        ]
    if field == "index":
        out: list[float | None] = []
        for i, n in enumerate(nets):
            window = [v for v in nets[max(0, i - INDEX_WEEKS + 1) : i + 1] if v is not None]
            if n is None or len(window) < 52:
                out.append(None)
                continue
            lo, hi = min(window), max(window)
            out.append(100 * (n - lo) / (hi - lo) if hi > lo else 50.0)
        return out
    raise ValueError(f"unknown COT field {field!r}; one of {', '.join(FIELDS_OUT)}")


@dataclass(frozen=True)
class Summary:
    market: str
    description: str
    report_date: date
    group: str
    net: float | None
    change: float | None  # week over week
    net_pct_oi: float | None
    index: float | None  # 3-year percentile of net
    commercial_index: float | None


def summary(session: Session) -> list[Summary]:
    """Speculative positioning per market at the latest report, with its COT index;
    markets near 0 or 100 are crowded."""
    out = []
    for alias, (_code, family, description) in MARKETS.items():
        group = SPECULATORS[family]
        rows = history(session, alias, group)
        if not rows:
            continue
        nets, pct, idx = values(rows, "net"), values(rows, "net_pct_oi"), values(rows, "index")
        commercial = history(session, alias, "commercial")
        commercial_idx = values(commercial, "index") if commercial else [None]
        out.append(
            Summary(
                market=alias,
                description=description,
                report_date=rows[-1].report_date,
                group=group,
                net=nets[-1],
                change=nets[-1] - nets[-2]
                if len(nets) > 1 and nets[-1] is not None and nets[-2] is not None
                else None,
                net_pct_oi=pct[-1],
                index=idx[-1],
                commercial_index=commercial_idx[-1],
            )
        )
    return out


def series_by_day(
    session: Session, market: str, group: str, field: str, days: list[date], pit: bool
) -> list[float | None]:
    """A COT field on each trading day: the latest report published by then (pit) or the
    latest report dated by then (not pit, which looks ahead by three days)."""
    rows = history(session, market, group)
    if not rows:
        raise ValueError(f"no COT data for {market}; run fin-intel sync-cot")
    by_date: dict[date, float | None] = defaultdict(lambda: None)
    for r, v in zip(rows, values(rows, field), strict=True):
        by_date[available_on(r.report_date) if pit else r.report_date] = v
    dates = sorted(by_date)
    out, i, current = [], 0, None
    for d in days:
        while i < len(dates) and dates[i] <= d:
            current = by_date[dates[i]]
            i += 1
        out.append(current)
    return out
