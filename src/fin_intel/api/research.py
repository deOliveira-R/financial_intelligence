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


@router.get("/themes")
def theme_list():
    """The themes: one taxonomy over every source's own codes."""
    from fin_intel import themes

    return [{"name": t.name, "label": t.label} for t in themes.THEMES]


@router.get("/themes/{name}")
def theme_detail(session: SessionDep, name: str):
    """A theme's codes in every vocabulary and its direct graph edges (BEA industries,
    suppliers, buyers)."""
    from fin_intel import graph, themes

    try:
        theme = themes.get(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    return {**asdict(theme), "graph": graph.neighbors(session, f"theme:{name}")}


@router.get("/graph/{node}")
def graph_node(
    session: SessionDep,
    node: str,
    direction: str = Query("edges", pattern="^(edges|upstream|downstream|companies)$"),
    hops: int = Query(2, ge=1, le=4),
    min_share: float = Query(0.02, ge=0, le=1),
):
    """The supply-chain graph around a node (`theme:defense`, `industry:334413`,
    `summary:334`, or a theme name or BEA code): its direct edges, or its suppliers
    (`upstream`, by share of its inputs) or buyers (`downstream`, by share of its output)
    within `hops` edges, or the `companies` in it (industries: by SIC mapping weight)."""
    from fin_intel import graph

    try:
        key = graph.resolve(session, node)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    if direction == "edges":
        return {"node": key, "edges": graph.neighbors(session, key)}
    if direction == "companies":
        return {"node": key, "companies": graph.companies(session, key)}
    return {"node": key, direction: graph.walk(session, key, direction, hops, min_share)}


@router.get("/timeline")
def timeline_endpoint(
    session: SessionDep,
    ticker: Annotated[list[str] | None, Query(description="Companies")] = None,
    theme: str | None = None,
    start: date | None = None,
    end: date | None = None,
    kind: Annotated[list[str] | None, Query(description="Only these kinds")] = None,
    limit: int = Query(100, ge=1, le=1000, description="Most events per kind"),
):
    """What became public, when, newest first: insider and congressional trades, 8-K
    items and event filings, periodic reports (companies); bills, presidential documents,
    lobbying reports, federal contracts (themes, which also cover their companies). Dates
    are public dates, so a window ending on a date shows only what was known then
    (default: the last 90 days)."""
    from datetime import timedelta

    from fin_intel import symbols, timeline

    end = end or date.today()
    start = start or end - timedelta(days=90)
    ciks = None
    if ticker:
        ciks = set()
        for t in ticker:
            security = symbols.get_security(session, t)
            if security is None or security.cik is None:
                raise HTTPException(404, f"unknown company {t}")
            ciks.add(security.cik)
    try:
        events = timeline.query(
            session, start, end, ciks=ciks, theme=theme, kinds=kind, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return [asdict(e) for e in events]


@router.get("/dossier/company/{ticker}")
def company_dossier(session: SessionDep, ticker: str, as_of: date | None = None):
    """Everything known about a company on a date (default today), point in time: identity
    and industries, metrics, 13F ownership, short interest, index funds holding it, filing
    text, its timeline of the last 90 days, and its themes' suppliers and buyers."""
    from fin_intel import dossier
    from fin_intel.api.app import issuer_cik_or_404

    return dossier.company(session, issuer_cik_or_404(session, ticker), as_of or date.today())


@router.get("/dossier/theme/{name}")
def theme_dossier(session: SessionDep, name: str, as_of: date | None = None):
    """Everything known about a theme on a date (default today): its codes, companies and
    the largest of them, supply chain, research series (ETF returns, breadth, lobbying,
    contracts, macro) as known then and a year earlier, and its policy timeline."""
    from fin_intel import dossier

    try:
        return dossier.theme(session, name, as_of or date.today())
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get("/distress")
def distress_alerts(session: SessionDep) -> list[dict]:
    """Distress scores of the watchlist and holdings under H001's frozen model (the latest
    daily scoring): `flagged` when in the top 2% of the research population. A flag means
    "read the filings now", not "sell": about one flagged company in nine failed within a
    year in the holdout, ten months ahead (docs/research/H001-distress-alerts.md)."""
    from fin_intel import distress

    return distress.latest(session)
