"""Research queries: time series, backtests, event studies, screens, the carry gauge,
text search, index membership, lobbying spend."""

from datetime import date
from typing import Annotated

import typer

from fin_intel import (
    filing_text,
    funds,
    lobbying,
    releases,
    symbols,
)
from fin_intel.cli.app import app
from fin_intel.db import session_factory


@app.command("timeseries", rich_help_panel="Research")
def timeseries_cmd(
    specs: Annotated[list[str], typer.Argument(help="e.g. 'px:SPY|sma:200' 'fred:T10Y2Y'")],
    start: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    end: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    pit: Annotated[bool, typer.Option(help="FRED values as known on each date")] = True,
    csv_path: Annotated[str | None, typer.Option("--csv", help="Write all rows to a CSV")] = None,
    rows: Annotated[int, typer.Option(help="Rows to print")] = 10,
) -> None:
    """Prices, macro and indicators on the trading calendar (see timeseries.py for syntax)."""
    import csv

    from fin_intel import timeseries

    with session_factory()() as session:
        try:
            dates, series = timeseries.build(
                session,
                specs,
                date.fromisoformat(start) if start else None,
                date.fromisoformat(end) if end else None,
                pit,
            )
        except timeseries.SpecError as exc:
            raise typer.BadParameter(str(exc)) from None
    if csv_path:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", *series])
            for i, d in enumerate(dates):
                writer.writerow([d.isoformat(), *(v[i] for v in series.values())])
        typer.echo(f"wrote {len(dates)} rows to {csv_path}")
    width = max(12, *(len(k) for k in series))
    typer.echo(f"{'date':<11}" + "".join(f"{k:>{width + 2}}" for k in series))
    for i in range(max(0, len(dates) - rows), len(dates)):
        cells = "".join(
            f"{'—' if v[i] is None else f'{v[i]:.4g}':>{width + 2}}" for v in series.values()
        )
        typer.echo(f"{dates[i].isoformat():<11}{cells}")


@app.command("backtest", rich_help_panel="Research")
def backtest_cmd(
    asset: Annotated[str, typer.Argument(help="Ticker traded, e.g. SPY")],
    rule: Annotated[str, typer.Argument(help="e.g. 'px:SPY > px:SPY|sma:200'")],
    start: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    end: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    cost_bps: Annotated[float, typer.Option(help="Cost per unit traded, basis points")] = 5.0,
    short: Annotated[bool, typer.Option(help="Short when the rule is false")] = False,
    csv_path: Annotated[str | None, typer.Option("--csv", help="Write the equity curve")] = None,
) -> None:
    """Backtest a long/flat (or long/short) rule on point-in-time series (see backtest.py)."""
    import csv

    from fin_intel import backtest

    with session_factory()() as session:
        try:
            r = backtest.run(
                session,
                asset,
                rule,
                date.fromisoformat(start) if start else None,
                date.fromisoformat(end) if end else None,
                cost_bps,
                short,
            )
        except backtest.RuleError as exc:
            raise typer.BadParameter(str(exc)) from None

    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v:+.1%}"

    typer.echo(f"{r.asset}: {r.rule}")
    typer.echo(f"{r.start} to {r.end} ({r.years:.1f} years), exposure {r.exposure:.0%}")
    typer.echo(f"{'':12}{'strategy':>10}{'buy&hold':>10}")
    for label, key in [
        ("total", "total_return"),
        ("CAGR", "cagr"),
        ("volatility", "volatility"),
        ("max DD", "max_drawdown"),
    ]:
        a, b = getattr(r.strategy, key), getattr(r.benchmark, key)
        typer.echo(f"{label:12}{pct(a):>10}{pct(b):>10}")
    sharpe = [s.sharpe for s in (r.strategy, r.benchmark)]
    typer.echo(
        f"{'Sharpe':12}" + "".join(f"{'—' if s is None else f'{s:.2f}':>10}" for s in sharpe)
    )
    typer.echo(
        f"trades {r.trades}, win rate {pct(r.win_rate).lstrip('+')}, avg trade {pct(r.avg_trade)}"
    )
    typer.echo(
        "by year: "
        + "  ".join(f"{y['year']} {pct(y['strategy'])}/{pct(y['benchmark'])}" for y in r.by_year)
    )
    if csv_path:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "strategy", "benchmark"])
            for d, e, b in zip(r.dates, r.equity, r.benchmark_equity, strict=True):
                writer.writerow([d.isoformat(), e, b])
        typer.echo(f"wrote {len(r.dates)} rows to {csv_path}")


@app.command("event-study", rich_help_panel="Research")
def event_study_cmd(
    source: Annotated[str, typer.Argument(help="insiders, congress or 13f")],
    member: Annotated[str | None, typer.Option(help="congress: part of a name")] = None,
    min_amount: Annotated[float, typer.Option(help="congress: minimum reported amount")] = 0,
    cik: Annotated[int | None, typer.Option(help="13f: one filer")] = None,
    min_insiders: Annotated[int, typer.Option(help="insiders: cluster size")] = 3,
    item: Annotated[str | None, typer.Option(help="8k: item, e.g. 2.02 (earnings)")] = None,
    form: Annotated[str | None, typer.Option(help="8k: form, e.g. 'SC 13D'")] = None,
) -> None:
    """Average returns vs SPY after disclosed trades or corporate events (from the next
    trading day): insiders, congress, 13f, 8k."""
    from fin_intel import events

    with session_factory()() as session:
        try:
            found = events.from_source(
                session, source, member, min_amount, cik, min_insiders, item, form
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from None
        result = events.study(session, found)
    typer.echo(f"{result.events} events, {result.priced} with prices")
    typer.echo(f"{'horizon':>8}{'n':>7}{'excess':>9}{'median':>9}{'hit':>7}{'t':>7}{'return':>9}")
    for h in result.horizons:
        if not h.n:
            continue
        typer.echo(
            f"{h.horizon:>7}d{h.n:>7}{h.mean_excess:>+9.2%}{h.median_excess:>+9.2%}"
            f"{h.hit_rate:>7.0%}{h.t_stat or 0:>7.1f}{h.mean_return:>+9.2%}"
        )


@app.command("screen-backtest", rich_help_panel="Research")
def screen_backtest_cmd(
    preset: Annotated[
        str | None, typer.Option(help="magic, deep_value, quality, cash_cows")
    ] = None,
    where: Annotated[list[str] | None, typer.Option("--where", "-w")] = None,
    sort: Annotated[str | None, typer.Option(help="metric, '-' for descending")] = None,
    rank: Annotated[str | None, typer.Option(help="'magic'")] = None,
    top: Annotated[int, typer.Option(help="Names held each period")] = 20,
    periods: Annotated[bool, typer.Option(help="Show each period's picks and returns")] = False,
) -> None:
    """Backtest a screen on point-in-time metrics (run backfill-metrics first): its top
    names vs SPY and vs the whole universe, 3, 6 and 12 months after each date."""
    from fin_intel import screener, screentest

    with session_factory()() as session:
        try:
            r = screentest.run(session, preset, where, rank, sort, top)
        except screener.ScreenError as exc:
            raise typer.BadParameter(str(exc)) from None

    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v:+.1%}"

    typer.echo(f"{r.screen}: top {r.top}, {len(r.periods)} rebalance dates")
    typer.echo(
        f"{'horizon':>8}{'periods':>9}{'return':>9}{'vs univ':>9}{'vs SPY':>9}{'beat':>7}"
        f"{'median vs univ':>16}"
    )
    for s in r.summary:
        beat = "—" if s.beat_universe is None else f"{s.beat_universe:.0%}"
        typer.echo(
            f"{s.horizon:>7}d{s.periods:>9}{pct(s.mean_return):>9}{pct(s.vs_universe):>9}"
            f"{pct(s.vs_spy):>9}{beat:>7}{pct(s.median_vs_universe):>16}"
        )
    if periods:
        h = screentest.HORIZONS[0]
        for p in r.periods:
            typer.echo(
                f"{p.as_of} {pct(p.returns[h]):>8} univ {pct(p.universe[h]):>8} "
                f"SPY {pct(p.spy[h]):>8}  {' '.join(p.picks[:12])}"
            )


@app.command("screen", rich_help_panel="Research")
def screen_cmd(
    where: Annotated[list[str] | None, typer.Option("--where", "-w", help="e.g. 'pe<15'")] = None,
    sort: Annotated[str | None, typer.Option(help="metric, '-' prefix for descending")] = None,
    rank: Annotated[str | None, typer.Option(help="'magic'")] = None,
    preset: Annotated[
        str | None, typer.Option(help="magic, deep_value, quality, cash_cows")
    ] = None,
    limit: int = 25,
    show: Annotated[
        str, typer.Option(help="Comma-separated metrics to display")
    ] = "market_cap,pe,ev_ebit,p_fcf,roic,operating_margin,revenue_growth,piotroski_f",
    sector: Annotated[list[str] | None, typer.Option(help="Only these sectors")] = None,
    exclude_sector: Annotated[list[str] | None, typer.Option(help="Leave out sectors")] = None,
) -> None:
    """Screen the market on company metrics (see screener.py; sectors in sectors.py)."""
    from fin_intel import screener

    with session_factory()() as session:
        try:
            as_of, rows = screener.screen(
                session, where, sort, rank, preset, limit, sector, exclude_sector
            )
        except screener.ScreenError as exc:
            raise typer.BadParameter(str(exc)) from None
    columns = [c.strip() for c in show.split(",") if c.strip()]
    typer.echo(f"{len(rows)} companies (metrics as of {as_of})")
    typer.echo(f"{'ticker':<8} {'name':<30}" + "".join(f"{c[:14]:>15}" for c in columns))

    def cell(value) -> str:
        if value is None:
            return "—"
        if abs(value) >= 1e9:
            return f"{value / 1e9:,.1f}B"
        if abs(value) >= 1e6:
            return f"{value / 1e6:,.1f}M"
        return f"{value:.3g}"

    for r in rows:
        name = (r["name"] or "")[:30]
        typer.echo(
            f"{r['ticker'] or '':<8} {name:<30}" + "".join(f"{cell(r[c]):>15}" for c in columns)
        )


@app.command(rich_help_panel="Research")
def carry(
    as_of: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: latest")] = None,
) -> None:
    """Yen carry trade gauge: differentials, carry-to-risk, crowding, flows, and flags
    worth attention (carry.py)."""
    from fin_intel import carry as gauge_module

    with session_factory()() as session:
        g = gauge_module.gauge(session, date.fromisoformat(as_of) if as_of else None)

    def fmt(v: float | None, spec: str) -> str:
        return "-" if v is None else format(v, spec)

    typer.echo(f"yen carry gauge as of {g.as_of}   next BOJ {g.next_boj}   next FOMC {g.next_fomc}")
    typer.echo(f"{'':42}{'value':>12}{'1m change':>12}{'3y pctile':>11}")
    for r in g.readings:
        relative = r.name in ("usd_jpy", "mxn_jpy", "aud_jpy")
        change = fmt(r.change_1m, "+.1%" if relative else "+.2f")
        typer.echo(
            f"{r.name:42}{fmt(r.value, ',.2f'):>12}{change:>12}{fmt(r.percentile_3y, '.0%'):>11}"
        )
    for flag in g.flags or ["no flags"]:
        typer.echo(f"  ! {flag}" if g.flags else f"  {flag}")


@app.command(rich_help_panel="Research")
def calendar(
    days: Annotated[int, typer.Option(help="Days ahead")] = 14,
    daily: Annotated[bool, typer.Option(help="Include daily releases (rates, VIX...)")] = False,
) -> None:
    """Upcoming economic releases and the tracked series each updates."""

    with session_factory()() as session:
        for e in releases.upcoming(session, days, daily=daily):
            typer.echo(f"{e.date}  {e.release or e.release_id:<45} {' '.join(e.series)}")


@app.command("search-filings", rich_help_panel="Research")
def search_filings(
    query: str,
    ticker: Annotated[list[str] | None, typer.Option(help="Only these companies")] = None,
    since: Annotated[str | None, typer.Option(help="Filed on or after (YYYY-MM-DD)")] = None,
    limit: int = 20,
) -> None:
    """Full-text search over stored filing text, e.g. '"export controls" china -russia'."""

    with session_factory()() as session:
        ciks = None
        if ticker:
            securities = [symbols.get_security(session, t) for t in ticker]
            ciks = [s.cik for s in securities if s is not None and s.cik]
        hits = filing_text.search(
            session, query, ciks, date.fromisoformat(since) if since else None, limit
        )
    for h in hits:
        typer.echo(
            f"{h.filed} {h.form:5} {h.section:12} cik {h.cik} {h.accession}\n    {h.snippet}"
        )


@app.command("search-presidential", rich_help_panel="Research")
def search_presidential(
    query: str,
    kind: Annotated[str | None, typer.Option(help="executive_order, proclamation...")] = None,
    since: Annotated[str | None, typer.Option(help="Signed on or after (YYYY-MM-DD)")] = None,
    limit: int = 20,
) -> None:
    """Full-text search over presidential documents, e.g. '"section 232" steel'."""
    from fin_intel import presidential

    with session_factory()() as session:
        hits = presidential.search(
            session, query, kind, date.fromisoformat(since) if since else None, limit
        )
    for h in hits:
        label = f"{h.kind} {h.number or ''}".strip()
        typer.echo(f"{h.signed} {label:24} {h.title}\n    {h.snippet}")


@app.command("fund-holdings", rich_help_panel="Research")
def fund_holdings(
    fund: str,
    as_of: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: latest")] = None,
    limit: Annotated[int, typer.Option(help="Largest holdings to show")] = 25,
) -> None:
    """A tracked fund's holdings in its latest report public on a date (index membership)."""

    with session_factory()() as session:
        rows = funds.members(session, fund, date.fromisoformat(as_of) if as_of else None)
    if not rows:
        typer.echo(f"no holdings for {fund}; run sync-funds")
        raise typer.Exit(1)
    first = rows[0]
    typer.echo(f"{fund.upper()}: {len(rows)} holdings, {first['period']} (filed {first['filed']})")
    for r in rows[:limit]:
        typer.echo(f"  {r['weight'] or 0:6.2f}%  {r['cusip'] or r['isin'] or '':12}  {r['name']}")


@app.command("lobbying", rich_help_panel="Research")
def lobbying_cmd(
    issue: Annotated[str | None, typer.Option(help="Issue code, e.g. DEF, ENG, HCR, TRD")] = None,
    client: Annotated[str | None, typer.Option(help="Part of a client name")] = None,
) -> None:
    """Quarterly lobbying spend on an issue area, or by clients matching a name."""

    with session_factory()() as session:
        if issue:
            for y, q, spend, n in lobbying.by_issue(session, issue)[-16:]:
                typer.echo(f"{y}-Q{q}  ${spend / 1e6:8.1f}M  {n:6} reports")
        if client:
            for r in lobbying.by_client(session, client):
                typer.echo(
                    f"{r['year']}-Q{r['quarter']}  ${(r['spend'] or 0) / 1e6:7.2f}M  {r['client']}"
                )


@app.command("build-graph", rich_help_panel="Research")
def build_graph_cmd() -> None:
    """Rebuild the supply-chain graph from the BEA tables and the themes (after editing
    themes.py; sync-bea rebuilds it on its own)."""
    from fin_intel import graph

    with session_factory()() as session:
        counts = graph.build(session)
        session.commit()
    typer.echo(f"graph: {counts['nodes']} nodes, {counts['edges']} edges")


@app.command("graph", rich_help_panel="Research")
def graph_cmd(
    node: Annotated[str, typer.Argument(help="theme:defense, industry:334413, or a name/code")],
    direction: Annotated[
        str, typer.Option(help="upstream (suppliers), downstream (buyers) or edges")
    ] = "upstream",
    hops: Annotated[int, typer.Option(help="Most edges from the node")] = 2,
    min_share: Annotated[float, typer.Option(help="Smallest share an edge must carry")] = 0.02,
    rows: Annotated[int, typer.Option(help="Rows to print")] = 25,
) -> None:
    """Walk the supply-chain graph: who a node buys from (upstream: share of its inputs) or
    sells to (downstream: share of its output), N hops out; or its direct edges."""
    from fin_intel import graph

    with session_factory()() as session:
        try:
            key = graph.resolve(session, node)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from None
        if direction == "edges":
            for kind, edges in graph.neighbors(session, key).items():
                typer.secho(kind, bold=True)
                ordered = sorted(edges, key=lambda e: -(e["value"] or 0))
                for e in ordered[:rows]:
                    value = f"{e['value']:>10,.0f}" if e["value"] is not None else " " * 10
                    note = f"  ({e['note']})" if e["note"] else ""
                    typer.echo(f"  {value}  {e['node']:<24} {e['label'][:60]}{note}")
            return
        try:
            found = graph.walk(session, key, direction, hops, min_share)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from None
    typer.echo(f"{direction} of {key} (share = product of shares along the path)")
    for r in found[:rows]:
        typer.echo(f"  {r['share']:6.1%}  {r['depth']}  {r['node']:<24} {r['label'][:60]}")


@app.command("theme", rich_help_panel="Research")
def theme_cmd(
    name: Annotated[str | None, typer.Argument(help="Theme name; omit to list them")] = None,
) -> None:
    """A theme's codes in every source's vocabulary, its BEA industries and its largest
    suppliers and buyers."""
    from fin_intel import graph, themes

    if name is None:
        for t in themes.THEMES:
            typer.echo(f"  {t.name:<18} {t.label}")
        return
    try:
        theme = themes.get(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.secho(theme.label, bold=True)
    for field in (
        "universe_industry",
        "sic",
        "naics",
        "bea",
        "lobbying",
        "policy_areas",
        "bill_subjects",
        "presidential_query",
        "etfs",
        "cot",
        "fred",
        "eia",
    ):
        value = getattr(theme, field)
        if value:
            typer.echo(f"  {field:<19} {value}")
    with session_factory()() as session:
        try:
            key = graph.resolve(session, f"theme:{name}")
        except ValueError:
            typer.echo("  (no graph yet: run fin-intel sync-bea)")
            return
        edges = graph.neighbors(session, key)
    for kind, title, share in (
        ("maps_to_out", "BEA industries", None),
        ("supplies_in", "bought from (share of its inputs)", "buyer_share"),
        ("supplies_out", "sold to (share of its output)", "supplier_share"),
    ):
        typer.secho(title, bold=True)
        found = edges.get(kind, [])
        if share:
            found = sorted(found, key=lambda e: -(e[share] or 0))[:8]
        for e in found:
            pct = f"{e[share]:6.1%}  " if share and e[share] is not None else ""
            note = f"  ({e['note']})" if e["note"] else ""
            typer.echo(f"  {pct}{e['node']:<24} {e['label'][:60]}{note}")
