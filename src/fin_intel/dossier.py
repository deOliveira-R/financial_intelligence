"""Dossiers: everything known about a company or a theme on a date, in one answer.

The connecting feature the rest builds toward: a research question ("what did we know about
NVDA on 2022-03-31?", "what is happening around defense?") gets one structured answer
instead of a dozen calls. Every section is point in time: metrics as of the date, 13F
holdings filed by then, short interest once published, fund holdings filed by then,
filing text filed by then, the timeline's public dates, series values as known then.

Sections fail independently: a missing source leaves its section empty (with the reason),
not the dossier. The API serves them (`/dossier/company/{ticker}`, `/dossier/theme/{name}`)
and they are the natural tools for agents.
"""

import logging
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import graph, shortinterest, themes, timeline, timeseries
from fin_intel.models import (
    CompanyMetrics,
    CusipMapping,
    FilingText,
    FundHolding,
    GraphEdge,
    GraphNode,
    InstitutionalFiler,
    InstitutionalPosition,
    Issuer,
    Security,
    ShortInterest,
    StatementItem,
)

log = logging.getLogger(__name__)

TIMELINE_DAYS = 90
METRIC_FIELDS = (
    "as_of",
    "price",
    "period_end",
    "currency",
    "market_cap",
    "enterprise_value",
    "revenue_ttm",
    "net_income_ttm",
    "fcf_ttm",
    "pe",
    "ev_ebit",
    "ev_sales",
    "p_fcf",
    "p_b",
    "fcf_yield",
    "dividend_yield",
    "shareholder_yield",
    "gross_margin",
)


def _section(name: str, build: Any, *args: Any) -> Any:
    try:
        return build(*args)
    except Exception as exc:  # one source failing must not sink the dossier
        log.warning("dossier section %s failed: %s", name, exc)
        return {"error": str(exc)}


# --- company ---


def company(session: Session, cik: int, as_of: date) -> dict[str, Any]:
    issuer = session.get(Issuer, cik)
    if issuer is None:
        raise ValueError(f"unknown issuer {cik}")
    security = _security(session, cik, as_of)
    return {
        "as_of": as_of,
        "identity": _identity(session, issuer, security),
        "metrics": _section("metrics", _metrics, session, cik, as_of),
        "financials": _section("financials", _financials, session, cik, as_of),
        "ownership": _section("ownership", _ownership, session, security, as_of),
        "short_interest": _section("short_interest", _short_interest, session, security, as_of),
        "index_funds": _section("index_funds", _funds, session, security, as_of),
        "filing_text": _section("filing_text", _filing_text, session, cik, as_of),
        "timeline": _section(
            "timeline",
            lambda: [
                asdict(e)
                for e in timeline.query(
                    session, as_of - timedelta(days=TIMELINE_DAYS), as_of, ciks={cik}, limit=30
                )
            ],
        ),
        "themes": _section("themes", _company_themes, session, cik),
    }


def _security(session: Session, cik: int, as_of: date) -> Security | None:
    """The listing its metrics were priced on as of the date (metrics.py picks the primary
    one), else its first active listing."""
    security_id = session.scalar(
        select(CompanyMetrics.security_id)
        .where(CompanyMetrics.cik == cik, CompanyMetrics.as_of <= as_of)
        .order_by(CompanyMetrics.as_of.desc())
        .limit(1)
    )
    if security_id is not None:
        return session.get(Security, security_id)
    return session.scalars(
        select(Security)
        .where(Security.cik == cik, Security.active, Security.ticker.is_not(None))
        .order_by(Security.id)
        .limit(1)
    ).first()


def _identity(session: Session, issuer: Issuer, security: Security | None) -> dict[str, Any]:
    tickers = session.scalars(
        select(Security.ticker).where(
            Security.cik == issuer.cik, Security.active, Security.ticker.is_not(None)
        )
    ).all()
    industries = session.execute(
        select(GraphEdge.dst, GraphNode.label, GraphEdge.value)
        .join(GraphNode, GraphNode.key == GraphEdge.dst)
        .where(GraphEdge.src == f"company:{issuer.cik}", GraphEdge.kind == "maps_to")
        .order_by(GraphEdge.value.desc())
    ).all()
    return {
        "cik": issuer.cik,
        "name": issuer.name,
        "ticker": security.ticker if security else None,
        "tickers": sorted(tickers),
        "sic": issuer.sic,
        "sic_description": issuer.sic_description,
        "country": issuer.country,
        "industries": [{"node": n, "label": label, "weight": w} for n, label, w in industries],
    }


def _metrics(session: Session, cik: int, as_of: date) -> dict[str, Any] | None:
    row = session.scalars(
        select(CompanyMetrics)
        .where(CompanyMetrics.cik == cik, CompanyMetrics.as_of <= as_of)
        .order_by(CompanyMetrics.as_of.desc())
        .limit(1)
    ).first()
    return {f: getattr(row, f) for f in METRIC_FIELDS} if row else None


FINANCIAL_ITEMS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_diluted",
    "operating_cash_flow",
    "capex",
    "cash",
    "total_assets",
    "total_liabilities",
    "long_term_debt",
    "equity",
    "shares_outstanding",
)
FINANCIAL_YEARS = 5


def _financials(session: Session, cik: int, as_of: date) -> dict[str, Any]:
    """Annual figures of the last FINANCIAL_YEARS fiscal years public by the date (first
    filed by then; a later restatement's value shows, as in metrics.py), in the reporting
    currency."""
    rows = session.execute(
        select(
            StatementItem.fiscal_year,
            StatementItem.line_item,
            StatementItem.value,
            StatementItem.unit,
            StatementItem.period_end,
        )
        .where(
            StatementItem.cik == cik,
            StatementItem.line_item.in_(FINANCIAL_ITEMS),
            StatementItem.fiscal_period == "FY",
            StatementItem.period_type.in_(("annual", "instant")),
            StatementItem.fiscal_year.is_not(None),
            func.coalesce(StatementItem.first_filed, StatementItem.filed) <= as_of,
        )
        .order_by(StatementItem.fiscal_year.desc(), StatementItem.period_end.desc())
    ).all()
    years: dict[int, dict[str, Any]] = {}
    units: dict[str, str] = {}
    for year, item, value, unit, period_end in rows:
        if year not in years and len(years) >= FINANCIAL_YEARS:
            continue
        figures = years.setdefault(year, {"period_end": period_end})
        figures.setdefault(item, value)  # latest period end within the fiscal year
        units.setdefault(item, unit)
    return {"years": years, "units": units}


def _cusips(session: Session, security: Security) -> list[str]:
    return list(
        session.scalars(select(CusipMapping.cusip).where(CusipMapping.security_id == security.id))
    )


def _ownership(session: Session, security: Security | None, as_of: date) -> dict[str, Any] | None:
    """13F holders in the latest quarter whose filings were public by `as_of` (filed up to
    45 days after the quarter), against the quarter before. Late filers count once filed."""
    if security is None:
        return None
    cusips = _cusips(session, security)
    if not cusips:
        return None
    public = (
        InstitutionalPosition.cusip.in_(cusips),
        InstitutionalPosition.put_call == "",
        InstitutionalPosition.filed <= as_of,
    )
    periods = session.scalars(
        select(InstitutionalPosition.period)
        .where(*public)
        .group_by(InstitutionalPosition.period)
        .order_by(InstitutionalPosition.period.desc())
        .limit(2)
    ).all()
    if not periods:
        return None

    def totals(period: date) -> tuple[int, float, float]:
        holders, value, shares = session.execute(
            select(
                func.count(func.distinct(InstitutionalPosition.filer_cik)),
                func.sum(InstitutionalPosition.value),
                func.sum(InstitutionalPosition.shares),
            ).where(*public, InstitutionalPosition.period == period)
        ).one()
        return holders, value or 0.0, shares or 0.0

    latest = totals(periods[0])
    top = session.execute(
        select(InstitutionalFiler.name, InstitutionalPosition.shares, InstitutionalPosition.value)
        .join(InstitutionalFiler, InstitutionalFiler.cik == InstitutionalPosition.filer_cik)
        .where(*public, InstitutionalPosition.period == periods[0])
        .order_by(InstitutionalPosition.value.desc().nulls_last())
        .limit(10)
    ).all()
    out: dict[str, Any] = {
        "period": periods[0],
        "holders": latest[0],
        "value": latest[1],
        "shares": latest[2],
        "top": [{"manager": n, "shares": s, "value": v} for n, s, v in top],
    }
    if len(periods) > 1:
        before = totals(periods[1])
        out |= {
            "previous_period": periods[1],
            "holders_change": latest[0] - before[0],
            "shares_change": latest[2] - before[2],
        }
    return out


def _short_interest(
    session: Session, security: Security | None, as_of: date
) -> dict[str, Any] | None:
    """The latest settlement published by `as_of` (FINRA: 8 business days later)."""
    if security is None:
        return None
    rows = session.scalars(
        select(ShortInterest)
        .where(
            ShortInterest.security_id == security.id,
            ShortInterest.settlement_date <= as_of,
        )
        .order_by(ShortInterest.settlement_date.desc())
        .limit(4)
    ).all()
    known = [r for r in rows if shortinterest.available_on(r.settlement_date) <= as_of]
    if not known:
        return None
    latest = known[0]
    out = {
        "settlement_date": latest.settlement_date,
        "short_position": latest.short_position,
        "days_to_cover": latest.days_to_cover,
        "average_daily_volume": latest.average_daily_volume,
    }
    if len(known) > 1 and known[1].short_position:
        out["change"] = latest.short_position / known[1].short_position - 1
    return out


def _funds(session: Session, security: Security | None, as_of: date) -> list[dict[str, Any]]:
    """The tracked index ETFs holding it, from each fund's latest report filed by then."""
    if security is None:
        return []
    cusips = _cusips(session, security)
    if not cusips:
        return []
    latest = (
        select(FundHolding.fund, func.max(FundHolding.period).label("period"))
        .where(FundHolding.filed <= as_of)
        .group_by(FundHolding.fund)
        .subquery()
    )
    rows = session.execute(
        select(FundHolding.fund, FundHolding.period, FundHolding.weight, FundHolding.value_usd)
        .join(latest, (latest.c.fund == FundHolding.fund) & (latest.c.period == FundHolding.period))
        .where(FundHolding.cusip.in_(cusips))
        .order_by(FundHolding.weight.desc().nulls_last())
    ).all()
    return [{"fund": f, "period": p, "weight": w, "value": v} for f, p, w, v in rows]


def _filing_text(session: Session, cik: int, as_of: date) -> list[dict[str, Any]]:
    """Sections of the latest annual and quarterly reports and press releases filed by then
    (the text itself at /filings/{accession}/text)."""
    rows = session.execute(
        select(
            FilingText.accession,
            FilingText.form,
            FilingText.section,
            FilingText.filed,
            func.length(FilingText.text),
        )
        .where(FilingText.cik == cik, FilingText.filed <= as_of)
        .order_by(FilingText.filed.desc())
        .limit(12)
    ).all()
    return [
        {"accession": a, "form": f, "section": s, "filed": d, "characters": n}
        for a, f, s, d, n in rows
    ]


def _company_themes(session: Session, cik: int) -> list[dict[str, Any]]:
    """The company's themes and, for each, its largest supplier and buyer themes."""
    names = session.scalars(
        select(GraphEdge.dst).where(GraphEdge.src == f"company:{cik}", GraphEdge.kind == "part_of")
    ).all()
    return [{"theme": key, **_theme_links(session, key, 3)} for key in names]


def _theme_links(session: Session, key: str, n: int) -> dict[str, Any]:
    edges = graph.neighbors(session, key)

    def top(kind: str, share: str) -> list[dict[str, Any]]:
        found = sorted(edges.get(kind, []), key=lambda e: -(e[share] or 0))[:n]
        return [{"node": e["node"], "label": e["label"], share: e[share]} for e in found]

    return {
        "suppliers": top("supplies_in", "buyer_share"),
        "buyers": top("supplies_out", "supplier_share"),
    }


# --- theme ---


def theme(session: Session, name: str, as_of: date) -> dict[str, Any]:
    t = themes.get(name)
    key = f"theme:{t.name}"
    return {
        "as_of": as_of,
        "theme": asdict(t),
        "companies": _section("companies", _theme_companies, session, key, as_of),
        "supply_chain": _section("supply_chain", _theme_links, session, key, 6),
        "series": _section("series", _theme_series, session, t, as_of),
        "timeline": _section(
            "timeline",
            lambda: [
                asdict(e)
                for e in timeline.query(
                    session,
                    as_of - timedelta(days=TIMELINE_DAYS),
                    as_of,
                    theme=t.name,
                    kinds=["bill", "presidential", "lobbying", "contracts", "release"],
                    limit=15,
                )
            ],
        ),
    }


def _theme_companies(session: Session, key: str, as_of: date) -> dict[str, Any]:
    """How many companies the theme has, and the largest by market value as of the date."""
    ciks = {int(c["node"].split(":")[1]) for c in graph.companies(session, key)}
    if not ciks:
        return {"count": 0, "largest": []}
    latest = (
        select(CompanyMetrics.cik, func.max(CompanyMetrics.as_of).label("as_of"))
        .where(CompanyMetrics.cik.in_(ciks), CompanyMetrics.as_of <= as_of)
        .group_by(CompanyMetrics.cik)
        .subquery()
    )
    rows = session.execute(
        select(Issuer.name, Security.ticker, CompanyMetrics.market_cap, CompanyMetrics.pe)
        .join(
            latest, (latest.c.cik == CompanyMetrics.cik) & (latest.c.as_of == CompanyMetrics.as_of)
        )
        .join(Issuer, Issuer.cik == CompanyMetrics.cik)
        .join(Security, Security.id == CompanyMetrics.security_id)
        .order_by(CompanyMetrics.market_cap.desc().nulls_last())
        .limit(15)
    ).all()
    return {
        "count": len(ciks),
        "largest": [{"name": n, "ticker": t, "market_cap": m, "pe": pe} for n, t, m, pe in rows],
    }


def theme_specs(t: themes.Theme) -> dict[str, str]:
    """Research series for a theme: ETF returns, lobbying spend, contract obligations,
    breadth of its universe industry, macro and energy series."""
    specs = {}
    for etf in t.etfs:
        specs[f"{etf} 3m return"] = f"px:{etf}|ret:63"
        specs[f"{etf} 12m return"] = f"px:{etf}|ret:252"
    if t.universe_industry:
        specs["share above 200d average"] = f"breadth:industry:{t.universe_industry}:pct_above_200d"
    for code in t.lobbying:
        specs[f"lobbying {code} (quarter)"] = f"lobby:{code}"
    for naics in t.naics:
        specs[f"contracts {naics} (month)"] = f"gov:{naics}"
    for series in t.fred:
        specs[series] = f"fred:{series}"
    for series in t.eia:
        specs[series] = f"eia:{series}"
    return specs


def _theme_series(session: Session, t: themes.Theme, as_of: date) -> dict[str, Any]:
    """Each series' value as known on the date and a year earlier (one spec at a time, so a
    missing source drops only its series)."""
    out: dict[str, Any] = {}
    for label, spec in theme_specs(t).items():
        try:
            days, series = timeseries.build(
                session, [spec], as_of - timedelta(days=400), as_of, pit=True
            )
        except timeseries.SpecError as exc:
            out[label] = {"spec": spec, "error": str(exc)}
            continue
        values = series[spec]
        latest = next((v for v in reversed(values) if v is not None), None)
        year_ago = values[-253] if len(values) > 252 else None
        out[label] = {"spec": spec, "value": latest, "year_ago": year_ago}
    return out
