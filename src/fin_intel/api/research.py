"""Research: time series, rule backtests, event studies."""

from dataclasses import asdict
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from fin_intel import (
    backtest,
    events,
    timeseries,
)
from fin_intel.api.app import SessionDep

router = APIRouter()


@router.get("/timeseries", response_model=None)
def timeseries_endpoint(
    session: SessionDep,
    s: Annotated[list[str], Query(description="Series specs, e.g. px:SPY|sma:200, fred:T10Y2Y")],
    start: date | None = None,
    end: date | None = None,
    pit: bool = Query(True, description="FRED values as known on each date (no look-ahead)"),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """Prices, macro series and indicators aligned on the trading calendar.

    Spec syntax: `px:TICKER` (adjusted close), `close:`, `volume:`, `fred:ID`; one ratio
    (`/`) or difference (`-`) of two terms; then transforms with `|`: sma:N ema:N rsi:N
    macd ret:N diff:N vol:N z:N high:N low:N dd:N yoy.
    """
    from fastapi.responses import PlainTextResponse

    try:
        dates, series = timeseries.build(session, s, start, end, pit)
    except timeseries.SpecError as exc:
        raise HTTPException(400, str(exc)) from None
    if format == "csv":
        lines = [",".join(["date", *(f'"{k}"' for k in series)])]
        for i, d in enumerate(dates):
            cells = ["" if v[i] is None else f"{v[i]:.6g}" for v in series.values()]
            lines.append(",".join([d.isoformat(), *cells]))
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv")
    return {"dates": dates, "series": series}


@router.get("/backtest")
def backtest_rule(
    session: SessionDep,
    asset: str,
    rule: str = Query(..., description="e.g. px:SPY > px:SPY|sma:200"),
    start: date | None = None,
    end: date | None = None,
    cost_bps: float = 5.0,
    short: bool = False,
    curve: bool = Query(False, description="Include the daily equity curves"),
) -> dict:
    """Backtest a rule on point-in-time series: the position decided at each close is held
    over the next day. Returns strategy vs buy-and-hold stats, per-year returns, trades."""
    try:
        r = backtest.run(session, asset, rule, start, end, cost_bps, short)
    except backtest.RuleError as exc:
        raise HTTPException(400, str(exc)) from None
    out = {
        k: v
        for k, v in asdict(r).items()
        if k not in ("dates", "equity", "benchmark_equity", "trade_list")
    }
    out["trade_list"] = [asdict(t) for t in r.trade_list[-50:]]
    if curve:
        out["curve"] = [
            {"date": d, "strategy": e, "benchmark": b}
            for d, e, b in zip(r.dates, r.equity, r.benchmark_equity, strict=True)
        ]
    return out


@router.get("/events/{source}")
def event_study(
    session: SessionDep,
    source: str,
    member: str | None = None,
    min_amount: float = 0,
    cik: int | None = None,
    min_insiders: int = 3,
    item: str | None = Query(None, description="8k: item, e.g. 2.02 (earnings releases)"),
    form: str | None = Query(None, description="8k: form, e.g. SC 13D"),
) -> events.Study:
    """Average returns vs SPY 5, 21, 63 and 126 trading days after disclosed trades:
    source = insiders (cluster buys), congress (purchases), 13f (new positions) or 8k
    (corporate events from SEC filings, filtered by `item` or `form`)."""
    try:
        found = events.from_source(
            session, source, member, min_amount, cik, min_insiders, item, form
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    result = events.study(session, found)
    result.unpriced = result.unpriced[:100]
    return result
