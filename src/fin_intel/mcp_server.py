"""The research tools for agents, over MCP: dossiers, the timeline, the supply-chain graph,
filing text, time series and the data catalog. Read-only, like the API, and backed by the
same functions.

Runs where the database is: `fin-intel mcp` speaks MCP over stdio, so a client on another
machine runs it through SSH (deploy/README.md), with nothing exposed to the network.
"""

import functools
import json
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from fin_intel.db import session_factory

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
MAX_TEXT = 60_000  # characters of filing text per call

server = MCPServer(
    "fin-intel",
    instructions=(
        "Financial intelligence over free official data (SEC, FRED, Congress, lobbying, "
        "federal contracts, BEA input-output tables...). Everything is point in time: pass "
        "as_of (YYYY-MM-DD) to see only what was public then. Start with list_themes, "
        "company_dossier or theme_dossier; drill down with timeline, supply_chain, "
        "search_filings and filing_text; series syntax is in timeseries' description."
    ),
)


def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register a read-only tool whose expected failures (unknown ticker, theme or node, bad
    series spec) reach the agent as their message rather than a generic error."""
    from fin_intel.timeseries import SpecError

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ValueError, SpecError) as exc:
            raise ToolError(str(exc)) from exc

    return server.tool(annotations=READ_ONLY)(run)


def _plain(value: Any) -> Any:
    """Dates and other non-JSON values as strings."""
    return json.loads(json.dumps(value, default=str))


def _day(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _cik(session: Any, ticker: str) -> int:
    from fin_intel import symbols

    security = symbols.get_security(session, ticker)
    if security is None or security.cik is None:
        raise ValueError(f"unknown company {ticker}")
    return security.cik


@tool
def list_themes() -> list[dict[str, str]]:
    """The themes (industries and policy areas with their codes in every source): use a
    name with theme_dossier, timeline or supply_chain (as theme:<name>)."""
    from fin_intel import themes

    return [{"name": t.name, "label": t.label} for t in themes.THEMES]


@tool
def company_dossier(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Everything known about a company on a date (default today): identity and BEA
    industries, valuation metrics, 13F ownership, short interest, index funds holding it,
    filing text sections, its last 90 days of public events, its themes' suppliers and
    buyers."""
    from fin_intel import dossier

    with session_factory()() as session:
        return _plain(dossier.company(session, _cik(session, ticker), _day(as_of) or date.today()))


@tool
def theme_dossier(theme: str, as_of: str | None = None) -> dict[str, Any]:
    """Everything known about a theme on a date: codes, companies (largest by market
    value), supply chain, research series as known then and a year earlier, and its policy
    events (bills, presidential documents, lobbying, contracts) of the last 90 days."""
    from fin_intel import dossier

    with session_factory()() as session:
        return _plain(dossier.theme(session, theme, _day(as_of) or date.today()))


@tool
def timeline(
    tickers: list[str] | None = None,
    theme: str | None = None,
    start: str | None = None,
    end: str | None = None,
    kinds: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Public events newest first, dated when they became public. Kinds: insider,
    congress, filing_event (8-K items, 13D/G, late filings), report (10-K/10-Q), bill,
    presidential, lobbying, contracts. Scope by tickers, a theme (its policy events and
    companies), or neither. Default window: the 90 days to `end` (default today); `limit`
    caps each kind."""
    from dataclasses import asdict

    from fin_intel import timeline as tl

    last = _day(end) or date.today()
    first = _day(start) or last - timedelta(days=90)
    with session_factory()() as session:
        ciks = {_cik(session, t) for t in tickers} if tickers else None
        events = tl.query(session, first, last, ciks=ciks, theme=theme, kinds=kinds, limit=limit)
        return _plain([asdict(e) for e in events])


@tool
def supply_chain(
    node: str, direction: str = "upstream", hops: int = 2, min_share: float = 0.02
) -> dict[str, Any]:
    """The supply-chain graph (BEA input-output tables, 2017 detail): `upstream` are
    suppliers by share of the node's inputs, `downstream` buyers (industries and final
    demand: exports, investment, government) by share of its output, `edges` the direct
    links, `companies` the listed companies in it. Nodes: theme:<name>, industry:<BEA code>
    (e.g. 334413 semiconductors), summary:<code>, company:<cik>; a theme name or BEA code
    also works. Capital equipment counts as investment (final demand), not as an input."""
    from fin_intel import graph

    with session_factory()() as session:
        key = graph.resolve(session, node)
        if direction == "edges":
            return _plain({"node": key, "edges": graph.neighbors(session, key)})
        if direction == "companies":
            return _plain({"node": key, "companies": graph.companies(session, key)})
        return _plain(
            {"node": key, direction: graph.walk(session, key, direction, hops, min_share)}
        )


@tool
def search_filings(
    query: str, ticker: str | None = None, since: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Full-text search of 10-K/10-Q risk factors and MD&A and earnings releases (web
    search syntax: words, "phrases", OR, -exclude), with snippets; read a hit with
    filing_text."""
    from dataclasses import asdict

    from fin_intel import filing_text

    with session_factory()() as session:
        ciks = [_cik(session, ticker)] if ticker else None
        hits = filing_text.search(session, query, ciks=ciks, since=_day(since), limit=limit)
        return _plain([asdict(h) for h in hits])


@tool
def filing_text(accession: str, section: str | None = None) -> list[dict[str, Any]]:
    """The text of a filing's sections (risk_factors, mdna, press_release...), each cut to
    MAX_TEXT characters."""
    from fin_intel import filing_text as ft

    with session_factory()() as session:
        rows = ft.get(session, accession, section)
        return _plain(
            [
                {
                    "section": r.section,
                    "form": r.form,
                    "filed": r.filed,
                    "characters": len(r.text),
                    "text": r.text[:MAX_TEXT],
                }
                for r in rows
            ]
        )


@tool
def timeseries(
    specs: list[str], start: str | None = None, end: str | None = None, rows: int = 30
) -> dict[str, Any]:
    """Series on the trading calendar, point in time (values as known each day), last
    `rows` days. Specs: px:SPY (adjusted close), fred:DGS10, breadth:pct_above_200d,
    cot:gold:managed_money:index, eia:crude_stocks, lobby:DEF, gov:3364 (contracts by
    NAICS), bills:defense, si:GME (short interest), cboe:equity_pc, jp:jgb10y; a ratio or
    difference of two (px:XLE/px:SPY); transforms after | (sma:N ema:N rsi:N ret:N diff:N
    vol:N z:N dd:N yoy)."""
    from fin_intel import timeseries as ts

    last = _day(end) or date.today()
    first = _day(start) or last - timedelta(days=max(rows * 2, 60))
    with session_factory()() as session:
        days, series = ts.build(session, specs, first, last, pit=True)
    keep = slice(max(0, len(days) - rows), len(days))
    return _plain({"dates": days[keep], "series": {k: v[keep] for k, v in series.items()}})


@tool
def data_catalog(alerts_only: bool = False) -> list[dict[str, Any]]:
    """Every data feed: status (ok, stale, failing, unmonitored), last success and age,
    failing items, the tables it loads. Check it before trusting a recent absence."""
    from fin_intel import catalog

    with session_factory()() as session:
        feeds = [catalog.as_dict(f) for f in catalog.feeds(session)]
    if alerts_only:
        feeds = [f for f in feeds if f["status"] in ("stale", "failing")]
    return _plain(feeds)
